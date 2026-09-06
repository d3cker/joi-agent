import io
from pathlib import Path
import wave

from voice_agent.ack import load_cached_acknowledgement, should_send_ack


def _wav(path: Path):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24_000)
        wav.writeframes(b"\0\0" * 2_400)
    path.write_bytes(output.getvalue())


def test_ack_heuristic_for_length_reasoning_and_task_verbs():
    assert should_send_ack("x" * 60, "none")
    assert should_send_ack("raz dwa trzy cztery pięć sześć siedem osiem dziewięć dziesięć jedenaście dwanaście", "none")
    assert should_send_ack("Cześć", "high")
    assert should_send_ack("Porównaj te opcje", "none")
    assert not should_send_ack("Jak się masz?", "none")


def test_ack_cache_loader_validates_wav_and_missing_is_disabled(tmp_path):
    assert load_cached_acknowledgement(tmp_path / "missing.wav") is None
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not wav")
    assert load_cached_acknowledgement(bad) is None
    valid = tmp_path / "ack.wav"
    _wav(valid)
    loaded = load_cached_acknowledgement(valid)
    assert loaded is not None
    assert loaded.sample_rate == 24_000
    assert loaded.duration_seconds == 0.1
