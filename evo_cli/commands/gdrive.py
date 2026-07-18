import base64
import json
import mimetypes
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import rich_click as click
from rich.text import Text

from evo_cli.console import error, info, step, success, warning
from evo_cli.credentials import google_oauth
from evo_cli.credentials.registry import dig, load_entries
from evo_cli.credentials.store import compile_flat

DRIVE_SERVICE_ID = "google_drive"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DOCS_API = "https://docs.googleapis.com/v1/documents/{doc_id}"
DRIVE_FILE_API = "https://www.googleapis.com/drive/v3/files/{file_id}"
DRIVE_EXPORT_API = "https://www.googleapis.com/drive/v3/files/{file_id}/export"

DOC_ID_RE = re.compile(r"/document/d/([a-zA-Z0-9_-]+)")
RAW_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{20,}$")
MD_INLINE_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)")
MD_REF_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\[([^\]]+)\]")
MD_REF_DEF_RE = re.compile(r"^\[([^\]]+)\]:\s*<?([^>\s]+)>?\s*$", re.MULTILINE)
DATA_URI_RE = re.compile(r"^data:([^;,]+)(?:;([^,]+))?,(.*)$", re.DOTALL)

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo gdrive doc-read <url>[/cyan]                read doc, write `<title>/doc.md` + `<title>/images/`\n"
    "  [cyan]evo gdrive doc-read <url> -o ./out[/cyan]      write into ./out instead\n"
    "  [cyan]evo gdrive doc-read <id> --no-images[/cyan]    skip image download\n"
    "  [cyan]evo gdrive doc-read <url> --via-docs-api[/cyan] use Docs API (needs Docs API enabled in the OAuth project)\n"
    "  [cyan]evo gdrive doc-read <url> --raw[/cyan]         also dump the raw API response"
)


def extract_doc_id(value):
    match = DOC_ID_RE.search(value)
    if match:
        return match.group(1)
    if RAW_ID_RE.match(value):
        return value
    return None


def drive_entry():
    for path, entry in load_entries():
        if entry.get("id") == DRIVE_SERVICE_ID and entry.get("oauth"):
            return path, entry
    raise click.ClickException(
        f"no '{DRIVE_SERVICE_ID}' oauth entry in the credential store. "
        "Authorise it once with: evo cred auth --service google-drive --client-secrets <client.json>"
    )


def token_expired(token_section):
    expiry = token_section.get("expiry")
    if not expiry:
        return True
    try:
        when = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return (when - now).total_seconds() < 60


def refresh_token(path, entry):
    creds, err = google_oauth.resolve_creds(entry)
    if err:
        raise click.ClickException(
            f"cannot refresh {DRIVE_SERVICE_ID}: {err}. "
            "Authorise once with: evo cred auth --service google-drive --client-secrets <client.json>"
        )
    try:
        expiry = google_oauth.refresh_entry(path, entry, creds)
    except Exception as exc:
        raise click.ClickException(f"token refresh failed: {google_oauth.describe_error(exc)}") from exc

    compile_flat()
    info(f"Refreshed Drive access token (valid until [accent]{expiry}[/accent])")
    return creds["container"][entry["oauth"]["access_field"]]


def access_token():
    path, entry = drive_entry()
    oauth = entry["oauth"]
    container = dig(entry.get("flat", {}), oauth["container"]) or {}
    token = container.get(oauth["access_field"])
    if not token or token_expired(container):
        info("Access token missing or expired - refreshing.")
        token = refresh_token(path, entry)
    return token


def http_get(url, token=None, timeout=120):
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "evo-cli")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", "replace")
        raise click.ClickException(f"GET {url} failed: HTTP {exc.code} {err_body}") from exc


def safe_filename(name):
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", name).strip("._-")
    return cleaned or "image"


def guess_image_ext(content_type, fallback="png"):
    if content_type:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ""
        ext = ext.lstrip(".")
        if ext == "jpe":
            ext = "jpg"
        if ext:
            return ext
    return fallback


def download_image(url, dest_path, token=None):
    headers = {"User-Agent": "evo-cli"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            content_type = resp.headers.get("Content-Type", "")
            body = resp.read()
    except urllib.error.HTTPError as exc:
        raise click.ClickException(f"image download failed: HTTP {exc.code} {url}") from exc
    if not dest_path.suffix:
        dest_path = dest_path.with_suffix(f".{guess_image_ext(content_type)}")
    dest_path.write_bytes(body)
    return dest_path


def fetch_file_metadata(file_id, token):
    url = DRIVE_FILE_API.format(file_id=file_id) + "?" + urllib.parse.urlencode(
        {"fields": "id,name,mimeType,modifiedTime"}
    )
    body, _ = http_get(url, token=token)
    return json.loads(body.decode("utf-8"))


def parse_filename_from_disposition(headers):
    disp = headers.get("Content-Disposition") or headers.get("content-disposition") or ""
    match = re.search(r"filename\*=UTF-8''([^;]+)", disp)
    if match:
        return urllib.parse.unquote(match.group(1).strip().strip('"'))
    match = re.search(r'filename="?([^";]+)"?', disp)
    if match:
        return match.group(1).strip()
    return None


def drive_export_markdown(file_id, token):
    url = DRIVE_EXPORT_API.format(file_id=file_id) + "?" + urllib.parse.urlencode({"mimeType": "text/markdown"})
    body, headers = http_get(url, token=token)
    return body.decode("utf-8"), parse_filename_from_disposition(headers)


def fetch_document(doc_id, token):
    body, _ = http_get(DOCS_API.format(doc_id=doc_id), token=token)
    return json.loads(body.decode("utf-8"))


def render_text_run(text_run):
    content = text_run.get("content", "")
    if not content:
        return ""
    style = text_run.get("textStyle") or {}
    link = (style.get("link") or {}).get("url")
    trailing_newline = content.endswith("\n")
    body = content[:-1] if trailing_newline else content
    if not body.strip():
        return content
    if style.get("bold"):
        body = f"**{body}**"
    if style.get("italic"):
        body = f"*{body}*"
    if style.get("strikethrough"):
        body = f"~~{body}~~"
    if style.get("underline") and not link:
        body = f"<u>{body}</u>"
    if link:
        body = f"[{body}]({link})"
    return body + ("\n" if trailing_newline else "")


def heading_prefix(named_style):
    if not named_style:
        return ""
    if named_style == "TITLE":
        return "# "
    match = re.match(r"HEADING_(\d)", named_style)
    if match:
        level = max(1, min(6, int(match.group(1))))
        return "#" * level + " "
    return ""


def bullet_prefix(paragraph, list_props_by_id):
    bullet = paragraph.get("bullet")
    if not bullet:
        return None
    nesting = bullet.get("nestingLevel", 0)
    indent = "  " * nesting
    list_props = list_props_by_id.get(bullet.get("listId"), {})
    nesting_levels = list_props.get("nestingLevels", [])
    glyph_type = None
    if 0 <= nesting < len(nesting_levels):
        glyph_type = nesting_levels[nesting].get("glyphType")
    if glyph_type and glyph_type != "GLYPH_TYPE_UNSPECIFIED":
        return f"{indent}1. "
    return f"{indent}- "


def render_paragraph(paragraph, inline_objects, list_props_by_id, image_resolver):
    elements = paragraph.get("elements") or []
    style = paragraph.get("paragraphStyle") or {}
    prefix = heading_prefix(style.get("namedStyleType"))
    bullet = bullet_prefix(paragraph, list_props_by_id)
    parts = []
    for element in elements:
        if "textRun" in element:
            parts.append(render_text_run(element["textRun"]))
        elif "inlineObjectElement" in element:
            obj_id = element["inlineObjectElement"].get("inlineObjectId")
            obj = inline_objects.get(obj_id, {})
            embedded = (obj.get("inlineObjectProperties") or {}).get("embeddedObject") or {}
            image_props = embedded.get("imageProperties") or {}
            content_uri = image_props.get("contentUri")
            alt = embedded.get("title") or embedded.get("description") or obj_id or "image"
            if content_uri and image_resolver:
                rel = image_resolver(obj_id, content_uri)
                if rel:
                    parts.append(f"![{alt}]({rel})")
                    continue
            parts.append(f"![{alt}]()")
        elif "horizontalRule" in element:
            parts.append("\n---\n")
        elif "pageBreak" in element:
            parts.append("\n\n---\n\n")
    text = "".join(parts)
    if bullet is not None:
        text = bullet + text.lstrip()
    elif prefix:
        text = prefix + text.lstrip()
    return text


def render_table(table, inline_objects, list_props_by_id, image_resolver):
    rows = table.get("tableRows") or []
    if not rows:
        return ""
    rendered_rows = []
    for row in rows:
        cells = row.get("tableCells") or []
        rendered_cells = []
        for cell in cells:
            cell_parts = []
            for element in cell.get("content") or []:
                if "paragraph" in element:
                    cell_parts.append(
                        render_paragraph(
                            element["paragraph"], inline_objects, list_props_by_id, image_resolver
                        ).strip()
                    )
            rendered_cells.append(" ".join(p for p in cell_parts if p).replace("|", "\\|"))
        rendered_rows.append("| " + " | ".join(rendered_cells) + " |")
    if rendered_rows:
        header_cells = rendered_rows[0].count("|") - 1
        separator = "| " + " | ".join(["---"] * max(1, header_cells)) + " |"
        return "\n".join([rendered_rows[0], separator, *rendered_rows[1:]]) + "\n"
    return ""


def document_to_markdown(document, image_resolver):
    body = document.get("body") or {}
    content = body.get("content") or []
    inline_objects = document.get("inlineObjects") or {}
    list_props_by_id = {
        lid: lst.get("listProperties") or {} for lid, lst in (document.get("lists") or {}).items()
    }
    title = document.get("title") or "Document"
    out_parts = [f"# {title}\n"]
    for element in content:
        if "paragraph" in element:
            text = render_paragraph(element["paragraph"], inline_objects, list_props_by_id, image_resolver)
            if text.strip():
                out_parts.append(text.rstrip() + "\n")
        elif "table" in element:
            out_parts.append(render_table(element["table"], inline_objects, list_props_by_id, image_resolver))
    return "\n".join(part for part in out_parts if part).rstrip() + "\n"


def build_image_resolver(inline_objects, out_dir, no_images):
    if no_images or not inline_objects:
        return None
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    cache = {}

    def resolver(obj_id, content_uri):
        if obj_id in cache:
            return cache[obj_id]
        dest = images_dir / safe_filename(obj_id)
        try:
            written = download_image(content_uri, dest)
        except click.ClickException as exc:
            warning(str(exc))
            cache[obj_id] = None
            return None
        rel = f"images/{written.name}"
        cache[obj_id] = rel
        info(f"Saved image [accent]{rel}[/accent]")
        return rel

    return resolver


def save_data_uri(data_uri, dest_no_ext):
    match = DATA_URI_RE.match(data_uri)
    if not match:
        return None
    mime, encoding, payload = match.group(1), match.group(2) or "", match.group(3)
    if "base64" in encoding.lower():
        body = base64.b64decode(payload)
    else:
        body = urllib.parse.unquote_to_bytes(payload)
    ext = guess_image_ext(mime)
    dest = dest_no_ext.with_suffix(f".{ext}")
    dest.write_bytes(body)
    return dest


def materialize_image(source_url, dest_no_ext, token):
    if source_url.startswith("data:"):
        return save_data_uri(source_url, dest_no_ext)
    use_token = token if "googleapis.com" in source_url else None
    return download_image(source_url, dest_no_ext, token=use_token)


def download_markdown_images(markdown, out_dir, token):
    images_dir = out_dir / "images"
    cache = {}
    index = [0]

    def next_dest():
        index[0] += 1
        images_dir.mkdir(parents=True, exist_ok=True)
        return images_dir / f"image_{index[0]:03d}"

    ref_defs = {}
    for match in MD_REF_DEF_RE.finditer(markdown):
        ref_defs[match.group(1)] = match.group(2)

    def resolve(url, alt_for_log):
        if url in cache:
            return cache[url]
        try:
            written = materialize_image(url, next_dest(), token)
        except click.ClickException as exc:
            warning(str(exc))
            cache[url] = None
            return None
        if written is None:
            warning(f"could not parse data URI for image '{alt_for_log}'")
            cache[url] = None
            return None
        rel = f"images/{written.name}"
        cache[url] = rel
        info(f"Saved image [accent]{rel}[/accent]")
        return rel

    def inline_repl(match):
        alt = match.group(1)
        url = match.group(2)
        rel = resolve(url, alt)
        return f"![{alt}]({rel})" if rel else match.group(0)

    def ref_repl(match):
        alt = match.group(1)
        ref_id = match.group(2)
        url = ref_defs.get(ref_id)
        if not url:
            return match.group(0)
        rel = resolve(url, alt)
        return f"![{alt}]({rel})" if rel else f"![{alt}]({url})"

    markdown = MD_INLINE_IMAGE_RE.sub(inline_repl, markdown)
    markdown = MD_REF_IMAGE_RE.sub(ref_repl, markdown)
    markdown = MD_REF_DEF_RE.sub("", markdown)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).rstrip() + "\n"
    return markdown


def run_doc_read(target, out_dir, no_images, raw, via_docs_api):
    doc_id = extract_doc_id(target)
    if not doc_id:
        raise click.ClickException("could not extract a Google Doc ID from input.")
    info(f"Document ID: [accent]{doc_id}[/accent]")

    token = access_token()

    if via_docs_api:
        step("Render via Docs API")
        document = fetch_document(doc_id, token)
        title = document.get("title") or doc_id
        target_dir = Path(out_dir) if out_dir else Path.cwd() / safe_filename(title)
        target_dir.mkdir(parents=True, exist_ok=True)
        info(f"Title: [accent]{title}[/accent]")
        info(f"Output: [accent]{target_dir}[/accent]")
        if raw:
            (target_dir / "raw.json").write_text(
                json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            info(f"Wrote raw API response to [accent]{target_dir / 'raw.json'}[/accent]")
        resolver = build_image_resolver(document.get("inlineObjects") or {}, target_dir, no_images)
        markdown = document_to_markdown(document, resolver)
    else:
        step("Export via Drive API (text/markdown)")
        markdown, exported_name = drive_export_markdown(doc_id, token)
        title = re.sub(r"\.(md|markdown)$", "", exported_name or "") or doc_id
        target_dir = Path(out_dir) if out_dir else Path.cwd() / safe_filename(title)
        target_dir.mkdir(parents=True, exist_ok=True)
        info(f"Title: [accent]{title}[/accent]")
        info(f"Output: [accent]{target_dir}[/accent]")
        if raw:
            (target_dir / "raw.md").write_text(markdown, encoding="utf-8")
            info(f"Wrote raw export to [accent]{target_dir / 'raw.md'}[/accent]")
        if not no_images:
            step("Download referenced images")
            markdown = download_markdown_images(markdown, target_dir, token)

    md_path = target_dir / "doc.md"
    md_path.write_text(markdown, encoding="utf-8")
    success(f"Wrote [accent]{md_path}[/accent] ({len(markdown)} bytes)")


@click.group("gdrive")
def gdrive():
    """**Google Drive** helpers. Read Google Docs (text + images) from URL or ID."""


@gdrive.command("doc-read", epilog=EPILOG)
@click.argument("target")
@click.option("-o", "--output", "out_dir", default=None, help="Output directory (default: ./<doc-title>).")
@click.option("--no-images", is_flag=True, help="Skip image download; keep remote URLs in markdown.")
@click.option(
    "--via-docs-api",
    is_flag=True,
    help="Use Docs API instead of Drive export (needs Docs API enabled in the OAuth project).",
)
@click.option("--raw", is_flag=True, help="Also write the raw API response next to doc.md.")
def doc_read(target, out_dir, no_images, raw, via_docs_api):
    """Read a Google Doc and write **doc.md** + downloaded **images/**.

    `TARGET` is either a Google Docs URL (`https://docs.google.com/document/d/<id>/...`)
    or a raw document ID. Auth uses the Drive OAuth token saved in
    `~/.omelet.json` under `rclone.token`, refreshing it automatically when expired.

    Default path: Drive API export to `text/markdown`, then any inline image URLs
    in the markdown are downloaded into `images/` and their references rewritten
    to local paths. Pass `--via-docs-api` to use the Docs API instead (gives
    cleaner image references but needs Docs API enabled in the OAuth project).
    """
    step("evo gdrive doc-read")
    try:
        run_doc_read(target, out_dir, no_images, raw, via_docs_api)
    except click.ClickException:
        raise
    except Exception as exc:
        error(str(exc))
        sys.exit(1)
