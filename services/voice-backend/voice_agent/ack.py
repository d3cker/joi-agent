from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path
import re
import wave


ACK_TEXT = "Jasne, już się tym zajmuję."
_TASK_VERB = re.compile(
    r"(?i)(?<!\w)(?:przeanalizuj|sprawdź|porównaj|zaplanuj|wyszukaj|przygotuj)(?!\w)"
)


@dataclass(frozen=True, slots=True)
class CachedAcknowledgement:
    data: bytes
    sample_rate: int
    duration_seconds: float
    path: str


def load_cached_acknowledgement(path: str | Path) -> CachedAcknowledgement | None:
    source = Path(path)
    if not source.is_file():
        return None
    data = source.read_bytes()
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
                return None
            sample_rate = wav.getframerate()
            frame_count = wav.getnframes()
            if sample_rate <= 0 or frame_count <= 0:
                return None
    except (EOFError, wave.Error):
        return None
    return CachedAcknowledgement(
        data=data,
        sample_rate=sample_rate,
        duration_seconds=frame_count / sample_rate,
        path=str(source),
    )


def should_send_ack(
    transcript: str,
    reasoning_effort: str,
    *,
    min_characters: int = 60,
    min_words: int = 12,
) -> bool:
    stripped = transcript.strip()
    return (
        len(stripped) >= min_characters
        or len(re.findall(r"\S+", stripped)) >= min_words
        or reasoning_effort in {"high", "max"}
        or _TASK_VERB.search(stripped) is not None
    )
