from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping

from .languages import conversation_language


class ProtocolError(ValueError):
    pass


class ReasoningEffort(StrEnum):
    NONE = "none"
    LOW = "low"
    HIGH = "high"
    MAX = "max"


class SessionPhase(StrEnum):
    CONNECTED = "connected"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"


@dataclass(frozen=True, slots=True)
class ClientCommand:
    type: str
    reasoning_effort: ReasoningEffort | None = None
    conversation_language: str | None = None
    response_id: str | None = None


def parse_client_command(value: Any) -> ClientCommand:
    if not isinstance(value, Mapping):
        raise ProtocolError("message must be a JSON object")
    message_type = value.get("type")
    if message_type not in {
        "session.configure",
        "response.cancel",
        "input_audio.commit",
        "output_audio.playback.done",
        "output_audio.playback_done",
        "ping",
    }:
        raise ProtocolError(f"unsupported message type: {message_type!r}")
    if message_type == "session.configure":
        raw = value.get("reasoning_effort")
        if raw is None:
            raise ProtocolError("session.configure requires reasoning_effort")
        try:
            effort = ReasoningEffort(raw)
        except ValueError as exc:
            raise ProtocolError("reasoning_effort must be one of: none, low, high, max") from exc
        raw_language = value.get("conversation_language")
        language = None
        if raw_language is not None:
            if not isinstance(raw_language, str):
                raise ProtocolError("conversation_language must be a string")
            try:
                language = conversation_language(raw_language).code
            except ValueError as exc:
                raise ProtocolError(str(exc)) from exc
        return ClientCommand(
            message_type,
            reasoning_effort=effort,
            conversation_language=language,
        )
    if message_type in {"output_audio.playback.done", "output_audio.playback_done"}:
        response_id = value.get("response_id")
        if not isinstance(response_id, str) or not response_id.strip():
            raise ProtocolError("output_audio.playback.done requires response_id")
        return ClientCommand(
            "output_audio.playback.done", response_id=response_id
        )
    return ClientCommand(message_type)


def event(message_type: str, **payload: Any) -> dict[str, Any]:
    return {"type": message_type, **payload}
