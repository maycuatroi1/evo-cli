import json
import sys
import tempfile
from pathlib import Path

import rich_click as click
from rich.table import Table
from rich.text import Text

from evo_cli.console import console, error, info, step, success, warning
from evo_cli.tts import core, player
from evo_cli.tts.errors import TtsError

TEXT_SUFFIXES = (".txt", ".md")

SPEAK_EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo tts speak 'Xin chào, bản build đã xong'[/cyan]   speak it out loud now\n"
    "  [cyan]evo tts speak -f notes.md -o notes.mp3[/cyan]        read a file, keep the audio\n"
    "  [cyan]evo tts speak 'hello' -p openai -V nova[/cyan]       use gpt-4o-mini-tts instead\n"
    "  [cyan]git log -1 --format=%s | evo tts speak[/cyan]        read stdin\n"
    "  [cyan]evo tts speak 'hi' --stdout > out.mp3[/cyan]         pipe raw audio"
)

BATCH_EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo tts batch chapters/ -o audio/[/cyan]             one mp3 per .txt/.md file\n"
    "  [cyan]evo tts batch a.txt b.txt -c 8[/cyan]                8 requests in flight\n"
    "  [cyan]evo tts batch -t 'câu một' -t 'câu hai'[/cyan]       inline strings\n"
    '  [cyan]evo tts batch --manifest jobs.jsonl[/cyan]           {"id":..,"text":..,"voice":..} per line\n'
    "  [cyan]evo tts batch big.txt --mode realtime[/cyan]         skip the async API"
)

VOICES_EPILOG = Text.from_markup(
    "[bold]Examples[/bold]\n\n"
    "  [cyan]evo tts voices[/cyan]                                Vbee Vietnamese voices\n"
    "  [cyan]evo tts voices -l en-US --gender male[/cyan]         filter by language and gender\n"
    "  [cyan]evo tts voices -p openai[/cyan]                      gpt-4o-mini-tts voices\n"
    "  [cyan]evo tts voices --json[/cyan]                         machine-readable"
)


def read_input_text(text, text_file):
    if text and text_file:
        raise click.UsageError("pass either TEXT or --file, not both.")
    if text_file:
        return Path(text_file).read_text(encoding="utf-8")
    if text:
        return text
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise click.UsageError("no text given: pass TEXT, --file, or pipe it on stdin.")


def collect_items(inputs, texts, manifest):
    items = []
    if manifest:
        for line_number, line in enumerate(Path(manifest).read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError as exc:
                raise click.UsageError(f"{manifest}:{line_number} is not valid JSON: {exc}") from exc
            if not record.get("text"):
                raise click.UsageError(f"{manifest}:{line_number} has no 'text' field.")
            items.append(
                {
                    "name": str(record.get("id") or f"item-{len(items) + 1:03d}"),
                    "text": record["text"],
                    "voice": record.get("voice"),
                    "instructions": record.get("instructions"),
                }
            )
    for raw in inputs:
        path = Path(raw)
        if path.is_dir():
            files = sorted(p for p in path.rglob("*") if p.suffix.lower() in TEXT_SUFFIXES)
            if not files:
                warning(f"no {' or '.join(TEXT_SUFFIXES)} files under [accent]{path}[/accent]")
            for file_path in files:
                items.append({"name": file_path.stem, "text": file_path.read_text(encoding="utf-8")})
        elif path.is_file():
            items.append({"name": path.stem, "text": path.read_text(encoding="utf-8")})
        else:
            raise click.UsageError(f"input not found: {raw}")
    for text in texts:
        items.append({"name": f"item-{len(items) + 1:03d}", "text": text})
    return [item for item in items if item["text"].strip()]


def unique_path(out_dir, name, output_format):
    candidate = out_dir / f"{name}.{output_format}"
    counter = 2
    while candidate.exists():
        candidate = out_dir / f"{name}-{counter}.{output_format}"
        counter += 1
    return candidate


@click.group("tts")
def tts_group():
    """**Text to speech** via Vbee (Vietnamese) or OpenAI `gpt-4o-mini-tts`.

    `speak` is the realtime path: it synthesises and plays immediately.
    `batch` is the bulk path: it uses Vbee's async API and writes one file per input.
    Credentials come from the omelet store (`evo cred add vbee.app_id`, `vbee.token`,
    `openai_api_key`); nothing is read from hardcoded values.
    """


@tts_group.command("speak", epilog=SPEAK_EPILOG)
@click.argument("text", required=False)
@click.option("-f", "--file", "text_file", help="Read the text from a file instead of the argument.")
@click.option(
    "-p",
    "--provider",
    type=click.Choice(["auto", "vbee", "openai"]),
    default="auto",
    show_default=True,
    help="auto picks Vbee when its credentials exist, else OpenAI.",
)
@click.option("-V", "--voice", help="Voice code (see `evo tts voices`).")
@click.option("-o", "--output", help="Keep the audio at this path instead of a temp file.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["mp3", "wav"]),
    default="mp3",
    show_default=True,
    help="Audio container.",
)
@click.option("--speed", type=float, default=1.0, show_default=True, help="Speaking rate (Vbee: 0.25-1.9).")
@click.option("--bitrate", type=int, default=128, show_default=True, help="Vbee bitrate in kbps.")
@click.option("--instructions", help="OpenAI only: how the voice should deliver the text.")
@click.option("--model", help="OpenAI model override (default gpt-4o-mini-tts).")
@click.option("--no-play", is_flag=True, help="Synthesise only; do not play through the speakers.")
@click.option("--stdout", "to_stdout", is_flag=True, help="Write raw audio bytes to stdout (implies --no-play).")
@click.option("-q", "--quiet", is_flag=True, help="Suppress progress output.")
def speak(
    text,
    text_file,
    provider,
    voice,
    output,
    output_format,
    speed,
    bitrate,
    instructions,
    model,
    no_play,
    to_stdout,
    quiet,
):
    """Synthesise **TEXT** and play it right away.

    Long text is split on sentence boundaries so each request stays inside the
    provider's realtime limit (Vbee 300 characters, OpenAI 4000), then the
    resulting audio is joined back into one file.
    """
    body = read_input_text(text, text_file)
    if not quiet and not to_stdout:
        step("evo tts speak")
    try:
        resolved = core.resolve_provider(provider)
        chunks = core.chunk_limit(resolved, "realtime")
        if not quiet and not to_stdout:
            info(
                f"Provider [accent]{resolved}[/accent], voice "
                f"[accent]{voice or core.default_voice(resolved)}[/accent], "
                f"{len(body.strip())} characters (limit {chunks}/request)"
            )

        def on_progress(done, total):
            if total > 1 and not quiet and not to_stdout:
                info(f"Chunk {done}/{total}")

        audio = core.synthesize(
            body,
            provider=resolved,
            mode="realtime",
            voice=voice,
            output_format=output_format,
            speed=speed,
            bitrate=bitrate,
            instructions=instructions,
            model=model,
            on_progress=on_progress,
        )
    except TtsError as exc:
        error(str(exc))
        sys.exit(1)

    if to_stdout:
        player.write_stdout(audio)
        return

    if output:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        handle = tempfile.NamedTemporaryFile(suffix=f".{output_format}", delete=False, prefix="evo-tts-")
        handle.close()
        target = Path(handle.name)
    target.write_bytes(audio)
    if not quiet:
        success(f"Wrote [accent]{target}[/accent] ({len(audio)} bytes)")

    if no_play:
        return
    if not player.play(target):
        warning(f"no audio player found - {player.player_hint()}")


@tts_group.command("batch", epilog=BATCH_EPILOG)
@click.argument("inputs", nargs=-1)
@click.option("-t", "--text", "texts", multiple=True, help="Inline string to synthesise; repeatable.")
@click.option("--manifest", help="JSONL file, one {id, text, voice} object per line.")
@click.option("-o", "--out-dir", default="tts-out", show_default=True, help="Where the audio files land.")
@click.option(
    "-p",
    "--provider",
    type=click.Choice(["auto", "vbee", "openai"]),
    default="auto",
    show_default=True,
    help="auto picks Vbee when its credentials exist, else OpenAI.",
)
@click.option(
    "--mode",
    type=click.Choice(["batch", "realtime"]),
    default="batch",
    show_default=True,
    help="batch uses Vbee's async API (100k characters per request); realtime chunks at 300.",
)
@click.option("-V", "--voice", help="Voice code applied to every item without its own.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["mp3", "wav"]),
    default="mp3",
    show_default=True,
    help="Audio container.",
)
@click.option("--speed", type=float, default=1.0, show_default=True, help="Speaking rate.")
@click.option("--bitrate", type=int, default=128, show_default=True, help="Vbee bitrate in kbps.")
@click.option("--instructions", help="OpenAI only: how the voice should deliver the text.")
@click.option("--model", help="OpenAI model override (default gpt-4o-mini-tts).")
@click.option("-c", "--concurrency", type=int, default=4, show_default=True, help="Items in flight at once.")
@click.option("--webhook", help="Vbee webhookUrl; the API requires one even though evo polls for the result.")
@click.option("--timeout", type=int, default=900, show_default=True, help="Seconds to wait per async request.")
@click.option("--poll-interval", type=float, default=3.0, show_default=True, help="Seconds between polls.")
def batch(
    inputs,
    texts,
    manifest,
    out_dir,
    provider,
    mode,
    voice,
    output_format,
    speed,
    bitrate,
    instructions,
    model,
    concurrency,
    webhook,
    timeout,
    poll_interval,
):
    """Synthesise many inputs at once, one audio file per item.

    `INPUTS` are text files or directories (`.txt`, `.md` are picked up
    recursively). With `--mode batch` on Vbee each item goes through the async
    API and evo polls `/v1/tts/requests/{id}` until the audio link appears.
    OpenAI has no batch speech endpoint, so there the work is parallelised
    locally with `--concurrency`.
    """
    step("evo tts batch")
    items = collect_items(inputs, texts, manifest)
    if not items:
        raise click.UsageError("nothing to do: pass INPUTS, --text, or --manifest.")

    try:
        resolved = core.resolve_provider(provider)
    except TtsError as exc:
        error(str(exc))
        sys.exit(1)

    effective_mode = mode
    if resolved == "openai" and mode == "batch":
        info("OpenAI has no batch speech endpoint - running the items concurrently instead.")
        effective_mode = "realtime"

    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    info(
        f"Provider [accent]{resolved}[/accent], mode [accent]{effective_mode}[/accent], "
        f"{len(items)} items, concurrency {concurrency} -> [accent]{target_dir}[/accent]"
    )

    written = []
    failed = []

    def on_item(entry):
        if entry.get("error"):
            failed.append(entry)
            error(f"{entry['name']}: {entry['error']}")
            return
        path = unique_path(target_dir, entry["name"], output_format)
        path.write_bytes(entry["audio"])
        written.append(path)
        success(f"[accent]{path}[/accent] ({len(entry['audio'])} bytes)")

    core.synthesize_many(
        items,
        concurrency=concurrency,
        on_item=on_item,
        provider=resolved,
        mode=effective_mode,
        voice=voice,
        output_format=output_format,
        speed=speed,
        bitrate=bitrate,
        instructions=instructions,
        model=model,
        webhook_url=webhook,
        timeout=timeout,
        poll_interval=poll_interval,
    )

    console.print()
    if failed:
        warning(f"{len(written)}/{len(items)} succeeded, {len(failed)} failed")
        sys.exit(1)
    success(f"{len(written)} files in {target_dir}")


@tts_group.command("voices", epilog=VOICES_EPILOG)
@click.option(
    "-p",
    "--provider",
    type=click.Choice(["auto", "vbee", "openai"]),
    default="auto",
    show_default=True,
    help="Which catalog to list.",
)
@click.option("-l", "--language", default="vi-VN", show_default=True, help="Vbee language code; empty for all.")
@click.option("--gender", type=click.Choice(["male", "female"]), help="Filter by gender (Vbee).")
@click.option(
    "--ownership",
    type=click.Choice(["VBEE", "COMMUNITY", "PERSONAL"]),
    default="VBEE",
    show_default=True,
    help="Vbee voice ownership.",
)
@click.option("--limit", type=int, default=50, show_default=True, help="How many voices to fetch.")
@click.option("--search", help="Only show voices whose name or code contains this.")
@click.option("--json", "as_json", is_flag=True, help="Print raw JSON instead of a table.")
def voices(provider, language, gender, ownership, limit, search, as_json):
    """List the voice codes available for `--voice`."""
    try:
        entries = core.list_voices(
            provider=provider,
            language_code=language or None,
            gender=gender,
            ownership=ownership,
            limit=limit,
        )
    except TtsError as exc:
        error(str(exc))
        sys.exit(1)

    if search:
        needle = search.lower()
        entries = [
            entry
            for entry in entries
            if needle in str(entry.get("code", "")).lower() or needle in str(entry.get("name", "")).lower()
        ]

    if as_json:
        console.print_json(json.dumps(entries, ensure_ascii=False))
        return

    table = Table(title=f"{core.resolve_provider(provider)} voices ({len(entries)})", box=None, pad_edge=False)
    table.add_column("code", style="bold cyan", overflow="fold")
    table.add_column("name")
    table.add_column("lang")
    table.add_column("gender")
    for entry in entries:
        table.add_row(
            str(entry.get("code", "")),
            str(entry.get("name", "")),
            str(entry.get("language_code", "")),
            str(entry.get("gender", "")),
        )
    console.print(table)
