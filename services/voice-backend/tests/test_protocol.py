import pytest

from voice_agent.protocol import ProtocolError, ReasoningEffort, parse_client_command


@pytest.mark.parametrize("effort", ["none", "low", "high", "max"])
def test_configure_accepts_every_supported_reasoning_effort(effort):
    command = parse_client_command({
        "type": "session.configure",
        "reasoning_effort": effort,
        "conversation_language": "en",
    })
    assert command.reasoning_effort is ReasoningEffort(effort)
    assert command.conversation_language == "en"


def test_configure_requires_explicit_reasoning_effort():
    with pytest.raises(ProtocolError, match="requires"):
        parse_client_command({"type": "session.configure"})


def test_configure_rejects_unsupported_conversation_language():
    with pytest.raises(ProtocolError, match="unsupported conversation language"):
        parse_client_command({
            "type": "session.configure",
            "reasoning_effort": "low",
            "conversation_language": "xx",
        })


def test_unknown_message_is_rejected():
    with pytest.raises(ProtocolError, match="unsupported"):
        parse_client_command({"type": "surprise"})


@pytest.mark.parametrize(
    "message_type", ["output_audio.playback.done", "output_audio.playback_done"]
)
def test_playback_done_requires_response_id_and_normalizes_alias(message_type):
    command = parse_client_command(
        {"type": message_type, "response_id": "response-7"}
    )
    assert command.type == "output_audio.playback.done"
    assert command.response_id == "response-7"

    with pytest.raises(ProtocolError, match="requires response_id"):
        parse_client_command({"type": message_type})
