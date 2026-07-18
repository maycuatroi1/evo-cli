import io
import re
import wave

SEPARATORS = (r"\n{2,}", r"(?<=[.!?…])\s+", r"(?<=[,;:])\s+", r"\s+")


def _split_pieces(text, limit, separators):
    if len(text) <= limit:
        return [text]
    if not separators:
        return [text[index : index + limit] for index in range(0, len(text), limit)]
    parts = [part.strip() for part in re.split(separators[0], text) if part.strip()]
    if len(parts) < 2:
        return _split_pieces(text, limit, separators[1:])
    pieces = []
    for part in parts:
        pieces.extend(_split_pieces(part, limit, separators[1:]))
    return pieces


def split_text(text, limit):
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    chunks = []
    for piece in _split_pieces(text, limit, SEPARATORS):
        if chunks and len(chunks[-1]) + 1 + len(piece) <= limit:
            chunks[-1] = f"{chunks[-1]} {piece}"
        else:
            chunks.append(piece)
    return chunks


def _join_wav(parts):
    buffer = io.BytesIO()
    writer = None
    try:
        for part in parts:
            with wave.open(io.BytesIO(part), "rb") as reader:
                if writer is None:
                    writer = wave.open(buffer, "wb")
                    writer.setparams(reader.getparams())
                writer.writeframes(reader.readframes(reader.getnframes()))
    finally:
        if writer is not None:
            writer.close()
    return buffer.getvalue()


def join_audio(parts, output_format):
    parts = [part for part in parts if part]
    if not parts:
        return b""
    if len(parts) == 1:
        return parts[0]
    if output_format == "wav":
        return _join_wav(parts)
    return b"".join(parts)
