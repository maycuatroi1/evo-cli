from concurrent.futures import ThreadPoolExecutor, as_completed

from evo_cli.tts import openai as openai_tts
from evo_cli.tts import vbee
from evo_cli.tts.chunking import join_audio, split_text
from evo_cli.tts.creds import has_openai_credentials, has_vbee_credentials
from evo_cli.tts.errors import TtsError

PROVIDERS = ("vbee", "openai")
JOINABLE_FORMATS = ("mp3", "wav", "pcm")
MODES = ("realtime", "batch")


def resolve_provider(provider):
    if provider in PROVIDERS:
        return provider
    if provider in (None, "", "auto"):
        if has_vbee_credentials():
            return "vbee"
        if has_openai_credentials():
            return "openai"
        raise TtsError(
            "no TTS credentials found. Store Vbee with "
            "`evo cred add vbee.app_id --from-stdin` + `evo cred add vbee.token --from-stdin`, "
            "or OpenAI with `evo cred add openai_api_key --from-stdin`."
        )
    raise TtsError(f"unknown provider '{provider}' (expected one of: {', '.join(PROVIDERS)}, auto)")


def default_voice(provider):
    return vbee.DEFAULT_VOICE if provider == "vbee" else openai_tts.DEFAULT_VOICE


def supported_formats(provider):
    return vbee.FORMATS if provider == "vbee" else openai_tts.FORMATS


def chunk_limit(provider, mode):
    if provider == "vbee":
        return vbee.BATCH_LIMIT if mode == "batch" else vbee.REALTIME_LIMIT
    return openai_tts.TEXT_LIMIT


def _run_chunks(chunks, call, concurrency, on_progress):
    total = len(chunks)
    if concurrency <= 1 or total == 1:
        parts = []
        for index, chunk in enumerate(chunks, start=1):
            parts.append(call(chunk))
            if on_progress:
                on_progress(index, total)
        return parts
    parts = [None] * total
    completed = 0
    with ThreadPoolExecutor(max_workers=min(concurrency, total)) as pool:
        futures = {pool.submit(call, chunk): index for index, chunk in enumerate(chunks)}
        for future in as_completed(futures):
            parts[futures[future]] = future.result()
            completed += 1
            if on_progress:
                on_progress(completed, total)
    return parts


def synthesize(
    text,
    provider="auto",
    mode="realtime",
    voice=None,
    output_format="mp3",
    speed=1.0,
    bitrate=128,
    sample_rate=None,
    instructions=None,
    model=None,
    webhook_url=None,
    timeout=900,
    poll_interval=3.0,
    concurrency=1,
    on_progress=None,
    on_poll=None,
):
    provider = resolve_provider(provider)
    if mode not in MODES:
        raise TtsError(f"unknown mode '{mode}' (expected one of: {', '.join(MODES)})")
    text = (text or "").strip()
    if not text:
        raise TtsError("nothing to speak: text is empty")
    if output_format not in supported_formats(provider):
        raise TtsError(
            f"{provider} does not support format '{output_format}' "
            f"(supported: {', '.join(supported_formats(provider))})"
        )

    if provider == "vbee" and mode == "batch":

        def call(chunk):
            audio, _ = vbee.synthesize_async(
                chunk,
                voice=voice,
                output_format=output_format,
                bitrate=bitrate,
                speed=speed,
                sample_rate=sample_rate,
                webhook_url=webhook_url,
                timeout=timeout,
                interval=poll_interval,
                on_poll=on_poll,
            )
            return audio

    elif provider == "vbee":

        def call(chunk):
            return vbee.synthesize(
                chunk,
                voice=voice,
                output_format=output_format,
                bitrate=bitrate,
                speed=speed,
                sample_rate=sample_rate,
            )

    else:

        def call(chunk):
            return openai_tts.synthesize(
                chunk,
                voice=voice,
                output_format=output_format,
                model=model or openai_tts.DEFAULT_MODEL,
                instructions=instructions,
                speed=speed,
            )

    chunks = split_text(text, chunk_limit(provider, mode))
    if len(chunks) > 1 and output_format not in JOINABLE_FORMATS:
        raise TtsError(
            f"text needs {len(chunks)} chunks but '{output_format}' cannot be joined; "
            f"use one of: {', '.join(JOINABLE_FORMATS)}"
        )
    parts = _run_chunks(chunks, call, concurrency, on_progress)
    return join_audio(parts, output_format)


def synthesize_many(items, concurrency=4, on_item=None, **kwargs):
    results = [None] * len(items)

    def work(index):
        item = items[index]
        options = dict(kwargs)
        for key in ("voice", "instructions", "speed", "output_format"):
            if item.get(key) is not None:
                options[key] = item[key]
        return synthesize(item["text"], **options)

    def record(index, audio=None, error=None):
        entry = dict(items[index])
        entry["audio"] = audio
        entry["error"] = error
        results[index] = entry
        if on_item:
            on_item(entry)

    if concurrency <= 1 or len(items) == 1:
        for index in range(len(items)):
            try:
                record(index, audio=work(index))
            except TtsError as exc:
                record(index, error=str(exc))
        return results

    with ThreadPoolExecutor(max_workers=min(concurrency, len(items) or 1)) as pool:
        futures = {pool.submit(work, index): index for index in range(len(items))}
        for future in as_completed(futures):
            index = futures[future]
            try:
                record(index, audio=future.result())
            except TtsError as exc:
                record(index, error=str(exc))
    return results


def list_voices(provider="auto", language_code=None, gender=None, ownership="VBEE", limit=100):
    provider = resolve_provider(provider)
    if provider == "openai":
        return openai_tts.list_voices()
    voices, pagination = vbee.list_voices(
        ownership=ownership, language_code=language_code, gender=gender, limit=min(limit, 100)
    )
    collected = list(voices)
    cursor = pagination.get("next_cursor")
    while pagination.get("has_next_page") and cursor and len(collected) < limit:
        voices, pagination = vbee.list_voices(
            ownership=ownership,
            language_code=language_code,
            gender=gender,
            limit=min(limit - len(collected), 100),
            cursor=cursor,
        )
        collected.extend(voices)
        cursor = pagination.get("next_cursor")
    return collected[:limit]
