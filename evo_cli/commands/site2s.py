import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import rich_click as click
from rich.text import Text

from evo_cli.console import console, error, info, step, success, warning

BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
GATE_HOSTS = {"site2s.com", "98sub.net", "traffic2s.com", "seo2s.com"}
GOOGLE_REFERER = "https://www.google.com/"
CAPTCHA_URL = "https://98sub.net/site2s/captcha.php"
GETLINK_URL = "https://site2s.com/rest/connect"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
)
ENCODED_RE = re.compile(r'encodedRedirectUrl\s*=\s*"([A-Z2-7=]+)"')
TOKEN_RE = re.compile(r'token\s*=\s*"([0-9a-fA-F]{16,})"')

EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo site2s https://site2s.com/b8ijsi3b[/cyan]      resolve the final link\n"
    "  [cyan]evo site2s b8ijsi3b[/cyan]                         alias also works\n"
    "  [cyan]evo site2s <url> --json[/cyan]                     print machine-readable result\n"
    "  [cyan]evo site2s <url> --manual[/cyan]                   solve the reCAPTCHA yourself in the window\n"
    "  [cyan]evo site2s <url> --headless[/cyan]                 no window (use under xvfb-run)\n\n"
    "[dim]Layer 1 (Base32 redirect) is decoded with no captcha. Task/campaign links\n"
    "go through the 98sub reCAPTCHA + server countdown before the link is released.[/dim]"
)


def base32_decode(encoded):
    bits = ""
    for ch in encoded:
        if ch == "=":
            break
        val = BASE32_ALPHABET.find(ch.upper())
        if val == -1:
            raise ValueError(f"invalid base32 char: {ch!r}")
        bits += format(val, "05b")
    out = []
    i = 0
    while i < len(bits) - 7:
        out.append(chr(int(bits[i : i + 8], 2)))
        i += 8
    return "".join(out)


def normalize_url(target):
    target = target.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]+", target):
        return f"https://site2s.com/{target}", target
    if not target.startswith("http"):
        target = "https://" + target
    alias = urllib.parse.urlparse(target).path.strip("/").split("/")[-1]
    return target, alias


def host_of(url):
    return urllib.parse.urlparse(url).netloc.lower().split(":")[0]


def is_gate(url):
    host = host_of(url)
    return any(host == g or host.endswith("." + g) for g in GATE_HOSTS)


def load_browser():
    try:
        from patchright.sync_api import sync_playwright
    except ImportError as exc:
        raise click.ClickException(
            "patchright is required for Cloudflare/reCAPTCHA handling.\n"
            "  Install it with:  pip install patchright\n"
            "  It drives the locally installed Google Chrome."
        ) from exc
    return sync_playwright


def new_context(sync_playwright, headless):
    p = sync_playwright().start()
    args = ["--no-sandbox", "--disable-blink-features=AutomationControlled"]
    ctx = p.chromium.launch_persistent_context(
        user_data_dir="/tmp/.evo-site2s-profile",
        channel="chrome",
        headless=headless,
        no_viewport=True,
        args=args,
    )
    return p, ctx


def wait_past_cloudflare(page, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        page.wait_for_timeout(1500)
        title = (page.title() or "").lower()
        if "just a moment" not in title and "moment" not in title:
            return True
    return False


def fetch_redirect_url(page, site_url, timeout):
    info(f"Loading [accent]{site_url}[/accent] (passing Cloudflare)...")
    page.goto(site_url, wait_until="domcontentloaded", timeout=60000)
    if not wait_past_cloudflare(page, timeout):
        raise click.ClickException("timed out waiting for Cloudflare challenge to clear")
    html = page.content()
    match = ENCODED_RE.search(html)
    if not match:
        return None
    decoded = base32_decode(match.group(1))
    info(f"Layer-1 redirect: [accent]{decoded}[/accent]")
    return decoded


def get_recaptcha_response(page):
    return page.evaluate(
        """() => {
            const ta = document.querySelector('textarea[name="g-recaptcha-response"]');
            if (ta && ta.value) return ta.value;
            try { return (typeof grecaptcha !== 'undefined') ? grecaptcha.getResponse() : ''; }
            catch (e) { return ''; }
        }"""
    )


def solve_captcha_for_token(ctx, site_url, alias, manual, captcha_wait, timeout):
    page = ctx.new_page()
    captcha_page = CAPTCHA_URL + "?" + urllib.parse.urlencode({"w": site_url, "v": "0"})
    info(f"Opening captcha page for alias [accent]{alias}[/accent]...")
    page.set_extra_http_headers({"referer": GOOGLE_REFERER})
    page.goto(captcha_page, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)

    try:
        page.click("#getLinkButton", timeout=5000)
    except Exception:
        pass
    page.wait_for_timeout(1500)

    if manual:
        wait_for = max(captcha_wait, 240)
        warning(f"Solve the reCAPTCHA in the browser window (auto-detected, up to {wait_for}s)...")
    else:
        wait_for = captcha_wait
        info("Waiting for reCAPTCHA to validate (checkbox auto-pass)...")

    deadline = time.time() + wait_for
    while time.time() < deadline:
        if get_recaptcha_response(page):
            break
        page.wait_for_timeout(1500)

    if not get_recaptcha_response(page):
        raise click.ClickException(
            "reCAPTCHA not solved. Re-run with --manual to solve it yourself, "
            "or the IP may be flagged (try a residential IP)."
        )

    info("reCAPTCHA solved, submitting...")
    try:
        page.click('button[type="submit"][name="submit"]', timeout=5000)
    except Exception:
        page.evaluate("() => { const f = document.querySelector('form'); if (f) f.submit(); }")
    page.wait_for_timeout(3000)

    token = None
    deadline = time.time() + 20
    while time.time() < deadline:
        match = TOKEN_RE.search(page.content())
        if match:
            token = match.group(1)
            break
        page.wait_for_timeout(1500)

    page.close()
    if not token:
        raise click.ClickException("captcha accepted but no token was issued (submit may have failed)")
    info(f"Token issued: [accent]{token}[/accent]")
    return token


def call_getlink(token):
    url = GETLINK_URL + "?" + urllib.parse.urlencode({"action": "getLink", "token": token})
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("X-Requested-With", "XMLHttpRequest")
    req.add_header("Referer", "https://98sub.net/")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except ValueError:
            raise click.ClickException(f"getLink HTTP {exc.code}: {body}") from exc


def poll_getlink(token, timeout):
    info("Polling getLink (waiting out the server countdown ~70s)...")
    deadline = time.time() + timeout
    delay = 5
    last = {}
    with console.status("[info]Waiting for the link to be released...[/info]", spinner="dots"):
        while time.time() < deadline:
            data = call_getlink(token)
            last = data
            if data.get("status") == "success" and data.get("urlrespone"):
                return data
            time.sleep(delay)
    raise click.ClickException(
        f"link not released within timeout. Last response: {json.dumps(last, ensure_ascii=False)}"
    )


def run(target, headless, manual, timeout, captcha_wait, as_json):
    site_url, alias = normalize_url(target)
    sync_playwright = load_browser()

    if not headless and not os.environ.get("DISPLAY"):
        warning("No DISPLAY found. Headful Chrome needs a display; run under `xvfb-run` or pass --headless.")

    p, ctx = new_context(sync_playwright, headless)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        redirect_url = fetch_redirect_url(page, site_url, timeout)

        if redirect_url and not is_gate(redirect_url):
            final = redirect_url
            slug = None
        else:
            if redirect_url:
                info("Layer-1 points to a task gate; running captcha + token flow.")
            token = solve_captcha_for_token(ctx, site_url, alias, manual, captcha_wait, timeout)
            data = poll_getlink(token, timeout)
            final = data.get("urlrespone")
            slug = data.get("slug")
    finally:
        try:
            ctx.close()
        finally:
            p.stop()

    if as_json:
        console.print_json(json.dumps({"alias": alias, "final_url": final, "slug": slug}))
    else:
        success(f"Final link: [accent]{final}[/accent]")
        if slug:
            info(f"slug: {slug}")
    return final


@click.command("site2s", epilog=EPILOG)
@click.argument("target")
@click.option("--headless", is_flag=True, help="Run Chrome headless (needs xvfb; Cloudflare may flag it).")
@click.option("--manual", is_flag=True, help="Pause for you to solve the reCAPTCHA in the window.")
@click.option("--timeout", default=150, show_default=True, help="Overall timeout per stage (seconds).")
@click.option("--captcha-wait", default=40, show_default=True, help="Seconds to wait for reCAPTCHA auto-pass.")
@click.option("--json", "as_json", is_flag=True, help="Print result as JSON.")
def site2s(target, headless, manual, timeout, captcha_wait, as_json):
    """Resolve a **Site2S** short link to its real destination.

    `TARGET` is a `https://site2s.com/<alias>` URL or just the `<alias>`.

    Site2S hides the destination behind Cloudflare, a fake "search this keyword
    on Google" task, a reCAPTCHA, and a server-side countdown. This command
    drives a real Chrome (via patchright) to pass Cloudflare, decodes the
    Base32 layer-1 redirect, spoofs a Google referrer to skip the keyword task,
    waits out the countdown, and pulls the final link from the `getLink` API.

    Direct links resolve instantly with no captcha. Task/campaign links need the
    reCAPTCHA solved - it auto-passes on clean IPs, otherwise use `--manual`.
    """
    step("evo site2s")
    try:
        run(target, headless, manual, timeout, captcha_wait, as_json)
    except click.ClickException:
        raise
    except Exception as exc:
        error(str(exc))
        sys.exit(1)
