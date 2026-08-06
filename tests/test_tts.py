import base64
import io
import wave

import pytest
from click.testing import CliRunner

from evo_cli.cli import cli
from evo_cli.commands import tts as tts_command
from evo_cli.tts import core, gemini, vbee
from evo_cli.tts.chunking import join_audio, split_text
from evo_cli.tts.errors import TtsError

EVO_TTS_ENV_VARS = (
    "EVO_TTS_PROVIDER",
    "EVO_TTS_VOICE",
    "EVO_TTS_VOICE_GEMINI",
    "EVO_TTS_VOICE_OPENAI",
    "EVO_TTS_VOICE_VBEE",
    "EVO_TTS_SPEED",
    "EVO_TTS_DIR",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
)


@pytest.fixture(autouse=True)
def clean_tts_env(monkeypatch):
    # These are set for real on machines that picked a default provider, and they
    # would otherwise decide the outcome of the auto-detection tests. CI has none of
    # them set, so without this the suite passes there and fails on a real desktop.
    for name in EVO_TTS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def runner():
    return CliRunner()


def gemini_response(pcm, mime_type="audio/l16; rate=24000; channels=1"):
    return {
        "candidates": [
            {
                "content": {"parts": [{"inlineData": {"mimeType": mime_type, "data": base64.b64encode(pcm).decode()}}]},
                "finishReason": "STOP",
            }
        ]
    }


def wav_bytes(frames, framerate=8000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(framerate)
        writer.writeframes(b"\x00\x01" * frames)
    return buffer.getvalue()


def test_tts_command_is_registered():
    assert "tts" in cli.commands
    assert set(cli.commands["tts"].commands) == {"speak", "batch", "voices"}


def test_tts_help_runs(runner):
    result = runner.invoke(cli, ["tts", "--help"])
    assert result.exit_code == 0
    assert "Text to speech" in result.output


def test_split_text_returns_single_chunk_when_short():
    assert split_text("Xin chào", 300) == ["Xin chào"]


def test_split_text_keeps_every_chunk_within_limit():
    text = " ".join(f"Câu số {index} nói một điều gì đó dài vừa phải." for index in range(40))
    chunks = split_text(text, 100)
    assert len(chunks) > 1
    assert all(len(chunk) <= 100 for chunk in chunks)


def test_split_text_preserves_all_words():
    text = "Một hai ba. Bốn năm sáu! Bảy tám chín? Mười."
    assert " ".join(split_text(text, 12)).split() == text.split()


def test_split_text_hard_splits_a_single_long_token():
    chunks = split_text("x" * 250, 100)
    assert chunks == ["x" * 100, "x" * 100, "x" * 50]


def test_split_text_ignores_empty_input():
    assert split_text("   ", 300) == []


def test_join_audio_concatenates_mp3():
    assert join_audio([b"aaa", b"bbb"], "mp3") == b"aaabbb"


def test_join_audio_returns_single_part_untouched():
    assert join_audio([b"only"], "mp3") == b"only"


def test_join_audio_stitches_wav_frames():
    joined = join_audio([wav_bytes(10), wav_bytes(15)], "wav")
    with wave.open(io.BytesIO(joined), "rb") as reader:
        assert reader.getnframes() == 25
        assert reader.getframerate() == 8000


@pytest.fixture
def no_credentials(monkeypatch):
    for name in ("has_gemini_credentials", "has_vbee_credentials", "has_openai_credentials"):
        monkeypatch.setattr(core, name, lambda: False)
    return monkeypatch


def test_resolve_provider_accepts_explicit_names():
    assert core.resolve_provider("gemini") == "gemini"
    assert core.resolve_provider("vbee") == "vbee"
    assert core.resolve_provider("openai") == "openai"


def test_resolve_provider_rejects_unknown():
    with pytest.raises(TtsError, match="unknown provider"):
        core.resolve_provider("elevenlabs")


def test_resolve_provider_auto_prefers_gemini(no_credentials):
    no_credentials.setattr(core, "has_gemini_credentials", lambda: True)
    no_credentials.setattr(core, "has_vbee_credentials", lambda: True)
    no_credentials.setattr(core, "has_openai_credentials", lambda: True)
    assert core.resolve_provider("auto") == "gemini"


def test_resolve_provider_auto_falls_back_to_vbee(no_credentials):
    no_credentials.setattr(core, "has_vbee_credentials", lambda: True)
    no_credentials.setattr(core, "has_openai_credentials", lambda: True)
    assert core.resolve_provider("auto") == "vbee"


def test_resolve_provider_auto_falls_back_to_openai(no_credentials):
    no_credentials.setattr(core, "has_openai_credentials", lambda: True)
    assert core.resolve_provider("auto") == "openai"


def test_resolve_provider_auto_without_credentials(no_credentials):
    with pytest.raises(TtsError, match="no TTS credentials"):
        core.resolve_provider("auto")


def test_chunk_limit_differs_between_modes():
    assert core.chunk_limit("vbee", "realtime") == vbee.REALTIME_LIMIT
    assert core.chunk_limit("vbee", "batch") == vbee.BATCH_LIMIT
    assert core.chunk_limit("openai", "realtime") == core.openai_tts.TEXT_LIMIT


def test_chunk_limit_is_mode_independent_for_gemini():
    assert core.chunk_limit("gemini", "realtime") == gemini.TEXT_LIMIT
    assert core.chunk_limit("gemini", "batch") == gemini.TEXT_LIMIT


def test_synthesize_rejects_empty_text():
    with pytest.raises(TtsError, match="text is empty"):
        core.synthesize("   ", provider="vbee")


def test_synthesize_rejects_unsupported_format():
    with pytest.raises(TtsError, match="does not support format"):
        core.synthesize("xin chào", provider="openai", output_format="m4a")


def test_synthesize_rejects_unknown_mode():
    with pytest.raises(TtsError, match="unknown mode"):
        core.synthesize("xin chào", provider="vbee", mode="stream")


def test_synthesize_chunks_and_joins(monkeypatch):
    seen = []

    def fake_synthesize(text, **kwargs):
        seen.append(text)
        return text.encode("utf-8")

    monkeypatch.setattr(vbee, "synthesize", fake_synthesize)
    text = " ".join(f"Câu số {index} có độ dài vừa phải." for index in range(30))
    audio = core.synthesize(text, provider="vbee", mode="realtime")
    assert len(seen) > 1
    assert audio == b"".join(chunk.encode("utf-8") for chunk in seen)


def test_synthesize_refuses_unjoinable_multichunk(monkeypatch):
    monkeypatch.setattr(core.openai_tts, "synthesize", lambda text, **kwargs: b"x")
    with pytest.raises(TtsError, match="cannot be joined"):
        core.synthesize("word " * 3000, provider="openai", output_format="flac")


def test_synthesize_batch_uses_the_async_path(monkeypatch):
    calls = []

    def fake_async(text, **kwargs):
        calls.append(kwargs["webhook_url"])
        return b"audio", "req-1"

    monkeypatch.setattr(vbee, "synthesize_async", fake_async)
    audio = core.synthesize("xin chào", provider="vbee", mode="batch", webhook_url="https://cb")
    assert audio == b"audio"
    assert calls == ["https://cb"]


def test_synthesize_many_reports_per_item_errors(monkeypatch):
    def fake_synthesize(text, **kwargs):
        if "bad" in text:
            raise TtsError("boom")
        return text.encode("utf-8")

    monkeypatch.setattr(vbee, "synthesize", fake_synthesize)
    items = [{"name": "a", "text": "good one"}, {"name": "b", "text": "bad one"}]
    results = core.synthesize_many(items, concurrency=1, provider="vbee", mode="realtime")
    assert results[0]["audio"] == b"good one"
    assert results[0]["error"] is None
    assert results[1]["audio"] is None
    assert results[1]["error"] == "boom"


def test_synthesize_many_lets_an_item_override_the_voice(monkeypatch):
    voices = []

    def fake_synthesize(text, **kwargs):
        voices.append(kwargs["voice"])
        return b"x"

    monkeypatch.setattr(vbee, "synthesize", fake_synthesize)
    items = [{"name": "a", "text": "one", "voice": "custom"}, {"name": "b", "text": "two"}]
    core.synthesize_many(items, concurrency=1, provider="vbee", mode="realtime", voice="default")
    assert voices == ["custom", "default"]


def test_vbee_realtime_guards_the_character_limit():
    with pytest.raises(TtsError, match="at most 300 characters"):
        vbee.synthesize("x" * 301)


def test_openai_guards_the_character_limit():
    with pytest.raises(TtsError, match="at most 4000 characters"):
        core.openai_tts.synthesize("x" * 4001)


def test_speak_requires_some_input(runner):
    result = runner.invoke(cli, ["tts", "speak"], input="")
    assert result.exit_code != 0


def test_speak_rejects_text_and_file_together(runner):
    result = runner.invoke(cli, ["tts", "speak", "hello", "--file", "x.txt"])
    assert result.exit_code != 0
    assert "not both" in result.output


def test_batch_requires_inputs(runner):
    result = runner.invoke(cli, ["tts", "batch"])
    assert result.exit_code != 0
    assert "nothing to do" in result.output


def test_speak_writes_and_skips_playback(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(core, "resolve_provider", lambda provider: "vbee")
    monkeypatch.setattr(core, "synthesize", lambda text, **kwargs: b"fake-audio")
    target = tmp_path / "out.mp3"
    result = runner.invoke(cli, ["tts", "speak", "xin chào", "--no-play", "-o", str(target)])
    assert result.exit_code == 0
    assert target.read_bytes() == b"fake-audio"


def test_batch_writes_one_file_per_input(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(core, "resolve_provider", lambda provider: "vbee")
    monkeypatch.setattr(core, "synthesize", lambda text, **kwargs: text.encode("utf-8"))
    source = tmp_path / "in"
    source.mkdir()
    (source / "one.txt").write_text("một", encoding="utf-8")
    (source / "two.md").write_text("hai", encoding="utf-8")
    out_dir = tmp_path / "out"

    result = runner.invoke(cli, ["tts", "batch", str(source), "-o", str(out_dir), "-c", "1"])
    assert result.exit_code == 0
    assert (out_dir / "one.mp3").read_text(encoding="utf-8") == "một"
    assert (out_dir / "two.mp3").read_text(encoding="utf-8") == "hai"


def test_batch_reads_a_manifest(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(core, "resolve_provider", lambda provider: "vbee")
    monkeypatch.setattr(core, "synthesize", lambda text, **kwargs: text.encode("utf-8"))
    manifest = tmp_path / "jobs.jsonl"
    manifest.write_text('{"id": "greeting", "text": "xin chào"}\n', encoding="utf-8")
    out_dir = tmp_path / "out"

    result = runner.invoke(cli, ["tts", "batch", "--manifest", str(manifest), "-o", str(out_dir)])
    assert result.exit_code == 0
    assert (out_dir / "greeting.mp3").read_text(encoding="utf-8") == "xin chào"


def test_batch_exits_nonzero_when_an_item_fails(runner, monkeypatch, tmp_path):
    def fake_synthesize(text, **kwargs):
        raise TtsError("nope")

    monkeypatch.setattr(core, "resolve_provider", lambda provider: "vbee")
    monkeypatch.setattr(core, "synthesize", fake_synthesize)
    result = runner.invoke(cli, ["tts", "batch", "-t", "xin chào", "-o", str(tmp_path / "out")])
    assert result.exit_code == 1
    assert "nope" in result.output


def test_resolve_provider_auto_follows_the_env_override(monkeypatch):
    monkeypatch.setenv("EVO_TTS_PROVIDER", "openai")
    monkeypatch.setattr(core, "has_vbee_credentials", lambda: True)
    assert core.resolve_provider("auto") == "openai"


def test_env_override_does_not_beat_an_explicit_provider(monkeypatch):
    monkeypatch.setenv("EVO_TTS_PROVIDER", "openai")
    assert core.resolve_provider("vbee") == "vbee"


def test_resolve_provider_rejects_a_bogus_env_override(monkeypatch):
    monkeypatch.setenv("EVO_TTS_PROVIDER", "elevenlabs")
    with pytest.raises(TtsError, match="EVO_TTS_PROVIDER"):
        core.resolve_provider("auto")


def test_default_voice_for_falls_back_to_the_provider_default(monkeypatch):
    monkeypatch.delenv("EVO_TTS_VOICE", raising=False)
    monkeypatch.delenv("EVO_TTS_VOICE_OPENAI", raising=False)
    assert core.default_voice_for("openai") == core.openai_tts.DEFAULT_VOICE
    assert core.default_voice_for("vbee") == vbee.DEFAULT_VOICE


def test_scoped_voice_env_wins_over_the_shared_one(monkeypatch):
    monkeypatch.setenv("EVO_TTS_VOICE", "shared")
    monkeypatch.setenv("EVO_TTS_VOICE_OPENAI", "nova")
    assert core.default_voice_for("openai") == "nova"
    assert core.default_voice_for("vbee") == "shared"


def test_scoped_voice_does_not_leak_across_providers(monkeypatch):
    monkeypatch.delenv("EVO_TTS_VOICE", raising=False)
    monkeypatch.setenv("EVO_TTS_VOICE_OPENAI", "nova")
    assert core.default_voice_for("vbee") == vbee.DEFAULT_VOICE


def test_default_voice_for_gemini_is_the_friendly_one(monkeypatch):
    monkeypatch.delenv("EVO_TTS_VOICE", raising=False)
    assert core.default_voice_for("gemini") == gemini.DEFAULT_VOICE == "Achird"


def test_gemini_voice_catalog_is_the_documented_thirty():
    codes = [entry["code"] for entry in gemini.list_voices()]
    assert len(codes) == 30
    assert len(set(codes)) == 30
    assert {"Achird", "Kore", "Puck", "Sulafat"} <= set(codes)


def test_gemini_normalises_the_voice_case_but_passes_unknown_through():
    assert gemini.normalise_voice("achird") == "Achird"
    assert gemini.normalise_voice("  KORE ") == "Kore"
    assert gemini.normalise_voice("BrandNewVoice") == "BrandNewVoice"
    assert gemini.normalise_voice(None) == gemini.DEFAULT_VOICE


def test_gemini_payload_carries_the_voice_and_the_instructions():
    payload = gemini.build_payload("xin chào", voice="sulafat", instructions="kể chuyện, ấm áp:")
    config = payload["generationConfig"]
    assert config["responseModalities"] == ["AUDIO"]
    assert config["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Sulafat"
    assert payload["contents"][0]["parts"][0]["text"] == "kể chuyện, ấm áp:\nxin chào"


def test_gemini_guards_the_character_limit():
    with pytest.raises(TtsError, match="at most 2000 characters"):
        gemini.synthesize("x" * 2001)


def test_gemini_wraps_the_raw_pcm_in_a_wav_container(monkeypatch):
    pcm = b"\x00\x01" * 100
    monkeypatch.setattr(gemini, "_post", lambda *args: gemini_response(pcm))
    audio = gemini.synthesize("xin chào", output_format="wav")
    with wave.open(io.BytesIO(audio), "rb") as reader:
        assert reader.getframerate() == 24000
        assert reader.getnchannels() == 1
        assert reader.getsampwidth() == 2
        assert reader.readframes(reader.getnframes()) == pcm


def test_gemini_returns_the_pcm_untouched_when_asked(monkeypatch):
    pcm = b"\x02\x03" * 40
    monkeypatch.setattr(gemini, "_post", lambda *args: gemini_response(pcm))
    assert gemini.synthesize("xin chào", output_format="pcm") == pcm


def test_gemini_reports_a_response_without_audio(monkeypatch):
    monkeypatch.setattr(gemini, "_post", lambda *args: {"candidates": [{"finishReason": "SAFETY"}]})
    with pytest.raises(TtsError, match="finishReason=SAFETY"):
        gemini.synthesize("xin chào")


def test_gemini_retries_a_server_error_then_succeeds(monkeypatch):
    attempts = []

    def flaky(url, **kwargs):
        attempts.append(url)
        if len(attempts) < 3:
            failure = TtsError("boom")
            failure.status = 500
            raise failure
        return gemini_response(b"\x00\x00")

    monkeypatch.setattr(gemini, "request_json", flaky)
    monkeypatch.setattr(gemini.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(gemini, "gemini_api_key", lambda: "test-key")
    assert gemini.synthesize("xin chào", output_format="pcm") == b"\x00\x00"
    assert len(attempts) == 3


def test_gemini_does_not_retry_a_bad_request(monkeypatch):
    attempts = []

    def rejected(url, **kwargs):
        attempts.append(url)
        failure = TtsError("bad key")
        failure.status = 400
        raise failure

    monkeypatch.setattr(gemini, "request_json", rejected)
    monkeypatch.setattr(gemini, "gemini_api_key", lambda: "test-key")
    with pytest.raises(TtsError, match="bad key"):
        gemini.synthesize("xin chào")
    assert len(attempts) == 1


def test_output_format_defaults_to_wav_on_gemini_and_mp3_elsewhere():
    assert tts_command.resolve_output_format("gemini", None) == "wav"
    assert tts_command.resolve_output_format("vbee", None) == "mp3"
    assert tts_command.resolve_output_format("openai", None) == "mp3"


def test_output_format_follows_the_output_suffix():
    assert tts_command.resolve_output_format("gemini", None, "notes.mp3") == "mp3"
    assert tts_command.resolve_output_format("gemini", None, "notes.ogg") == "wav"


def test_an_explicit_output_format_wins():
    assert tts_command.resolve_output_format("gemini", "pcm", "notes.mp3") == "pcm"


def test_synthesize_applies_the_env_voice(monkeypatch):
    monkeypatch.delenv("EVO_TTS_VOICE", raising=False)
    monkeypatch.setenv("EVO_TTS_VOICE_OPENAI", "nova")
    seen = {}

    def fake_synthesize(text, **kwargs):
        seen["voice"] = kwargs["voice"]
        return b"x"

    monkeypatch.setattr(core.openai_tts, "synthesize", fake_synthesize)
    core.synthesize("xin chào", provider="openai")
    assert seen["voice"] == "nova"
