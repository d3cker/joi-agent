import asyncio
import os
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from voice_agent.stt import (
    FasterWhisperSTT,
    FasterWhisperVAD,
    _faster_whisper_worker,
    _get_faster_whisper_vad_model,
)


def fake_stt_worker(connection, *_configuration):
    connection.send(("ready", None))
    try:
        while True:
            command, payload = connection.recv()
            if command == "close":
                return
            connection.send((
                "ok",
                {
                    "text": f"{os.getpid()}:{len(payload['audio'])}:{payload['language']}",
                    "language": payload["language"],
                    "average_log_probability": -0.01,
                    "no_speech_probability": 0.01,
                    "compression_ratio": 1.0,
                },
            ))
    except EOFError:
        return
    finally:
        connection.close()


def crashing_stt_worker(connection, *_configuration):
    connection.send(("ready", None))
    connection.recv()
    os._exit(17)


def failing_stt_worker(connection, *_configuration):
    connection.send(("startup_error", "synthetic startup failure"))
    connection.close()


def hallucination_stt_worker(connection, *_configuration):
    connection.send(("ready", None))
    try:
        command, _payload = connection.recv()
        assert command == "transcribe"
        connection.send(("ok", {
            "text": "Dziękuję, nie ma za co",
            "average_log_probability": -1.6,
            "no_speech_probability": 0.91,
            "compression_ratio": 1.1,
        }))
        connection.recv()
    except EOFError:
        pass
    finally:
        connection.close()


def repetitive_stt_worker(connection, *_configuration):
    connection.send(("ready", None))
    try:
        command, _payload = connection.recv()
        assert command == "transcribe"
        connection.send(("ok", {
            "text": "test test test test",
            "average_log_probability": -0.2,
            "no_speech_probability": 0.01,
            "compression_ratio": 3.1,
        }))
        connection.recv()
    except EOFError:
        pass
    finally:
        connection.close()


def pcm16(*samples: int) -> bytes:
    return np.asarray(samples, dtype=np.int16).tobytes()


def test_real_worker_uses_requested_language_and_never_translation(monkeypatch):
    observed = {}

    class Segment:
        text = " in 1969"
        start = 0.0
        end = 1.0
        avg_logprob = -0.1
        no_speech_prob = 0.01
        compression_ratio = 1.0

    class Info:
        language = "en"
        language_probability = 0.99

    class FakeWhisperModel:
        def __init__(self, *args, **kwargs):
            observed["init"] = kwargs

        def transcribe(self, audio, **kwargs):
            observed["audio_size"] = audio.size
            observed["transcribe"] = kwargs
            return iter([Segment()]), Info()

    class Connection:
        def __init__(self):
            self.requests = iter([
                ("transcribe", {"audio": pcm16(1, 2), "language": "en"}),
                ("close", None),
            ])
            self.sent = []

        def recv(self):
            return next(self.requests)

        def send(self, value):
            self.sent.append(value)

        def close(self):
            pass

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    connection = Connection()

    _faster_whisper_worker(connection, "/local/model", "cpu", 0, "float32")

    assert connection.sent[0] == ("ready", None)
    assert connection.sent[1][0] == "ok"
    assert connection.sent[1][1]["text"] == "in 1969"
    assert observed["transcribe"]["language"] == "en"
    assert observed["transcribe"]["task"] == "transcribe"
    assert observed["transcribe"]["condition_on_previous_text"] is False


def test_faster_whisper_stt_reuses_spawn_worker_and_closes_it():
    stt = FasterWhisperSTT(
        "/local/model",
        startup_timeout=5,
        request_timeout=5,
        _worker_target=fake_stt_worker,
    )

    async def scenario():
        first, second = await asyncio.gather(
            stt.transcribe(pcm16(1, 2)),
            stt.transcribe(pcm16(3, 4, 5)),
        )
        return first, second

    try:
        first, second = asyncio.run(scenario())
        first_pid, first_size, first_language = first.text.split(":")
        second_pid, second_size, second_language = second.text.split(":")
        assert first.accepted and second.accepted
        assert first_pid == second_pid
        assert {first_size, second_size} == {"4", "6"}
        assert first_language == second_language == "pl"
        assert first.language == second.language == "pl"
        assert stt._process is not None
        assert stt._process.daemon
        process = stt._process
    finally:
        stt.close()
    assert not process.is_alive()


def test_faster_whisper_stt_forwards_explicit_english_language():
    stt = FasterWhisperSTT(
        "/local/model",
        startup_timeout=5,
        request_timeout=5,
        _worker_target=fake_stt_worker,
    )
    try:
        result = asyncio.run(stt.transcribe(pcm16(1, 2), language="en-US"))
        assert result.accepted
        assert result.text.endswith(":en")
        assert result.language == "en"
    finally:
        stt.close()


def test_faster_whisper_stt_detects_worker_death():
    stt = FasterWhisperSTT(
        "/local/model",
        startup_timeout=5,
        request_timeout=5,
        _worker_target=crashing_stt_worker,
    )
    try:
        with pytest.raises(RuntimeError, match=r"exited during transcription.*17"):
            asyncio.run(stt.transcribe(pcm16(1)))
    finally:
        stt.close()


def test_faster_whisper_stt_reports_startup_failure():
    stt = FasterWhisperSTT(
        "/local/model",
        startup_timeout=5,
        _worker_target=failing_stt_worker,
    )
    try:
        with pytest.raises(RuntimeError, match="synthetic startup failure"):
            asyncio.run(stt.transcribe(pcm16(1)))
    finally:
        stt.close()


def test_faster_whisper_stt_validates_audio_before_starting_worker():
    stt = FasterWhisperSTT("/local/model", _worker_target=fake_stt_worker)
    with pytest.raises(ValueError, match="16 kHz"):
        asyncio.run(stt.transcribe(pcm16(1), sample_rate=8_000))
    with pytest.raises(ValueError, match="complete PCM16"):
        asyncio.run(stt.transcribe(b"\x00"))
    assert stt._process is None


@pytest.mark.parametrize(
    ("worker", "reason"),
    [
        (hallucination_stt_worker, "no_speech"),
        (repetitive_stt_worker, "compression_ratio"),
    ],
)
def test_faster_whisper_stt_rejects_unreliable_transcripts(worker, reason):
    stt = FasterWhisperSTT(
        "/local/model",
        startup_timeout=5,
        request_timeout=5,
        _worker_target=worker,
    )
    try:
        result = asyncio.run(stt.transcribe(pcm16(*([100] * 512))))
        assert not result.accepted
        assert result.text == ""
        assert result.rejection_reason == reason
    finally:
        stt.close()


def test_faster_whisper_vad_accumulates_frames_and_resets_after_detection(monkeypatch):
    lengths = []

    def probabilities(audio):
        lengths.append(len(audio))
        value = 0.9 if len(audio) >= 800 else 0.1
        return np.full((len(audio) + 511) // 512, value, dtype=np.float32)

    vad = FasterWhisperVAD()
    monkeypatch.setattr(vad, "_speech_probabilities", probabilities)

    async def scenario():
        assert not await vad.contains_speech(pcm16(*([1] * 400)))
        assert await vad.contains_speech(pcm16(*([2] * 400)))
        assert not await vad.contains_speech(pcm16(*([3] * 400)))

    asyncio.run(scenario())
    assert lengths == [400, 800, 400]


def test_faster_whisper_vad_bounds_tail_and_ignores_old_speech(monkeypatch):
    probabilities_result = [0.9, 0.9, 0.1, 0.1]
    observed = []

    def probabilities(audio):
        observed.append(audio.copy())
        return np.asarray(probabilities_result, dtype=np.float32)

    vad = FasterWhisperVAD(window_ms=100, recent_ms=20)
    monkeypatch.setattr(vad, "_speech_probabilities", probabilities)

    async def scenario():
        assert not await vad.contains_speech(pcm16(*([1] * 1_600)))
        probabilities_result[:] = [0.1, 0.9, 0.9, 0.1]
        assert await vad.contains_speech(pcm16(*([2] * 400)))

    asyncio.run(scenario())
    assert len(observed[0]) == len(observed[1]) == 1_600
    assert np.allclose(observed[1][-400:], 2 / 32768.0)


def test_faster_whisper_vad_loads_bundled_onnx_without_importing_package(
    monkeypatch, tmp_path
):
    recorded = {}

    asset = tmp_path / "faster_whisper/assets/silero_vad_v6.onnx"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"onnx")

    class FakeDistribution:
        def locate_file(self, relative):
            recorded["relative"] = relative
            return Path(tmp_path, relative)

    class FakeSessionOptions:
        pass

    class FakeSession:
        def __init__(self, path, providers, sess_options):
            recorded["path"] = path
            recorded["providers"] = providers
            recorded["options"] = sess_options

    onnxruntime = types.ModuleType("onnxruntime")
    onnxruntime.SessionOptions = FakeSessionOptions
    onnxruntime.InferenceSession = FakeSession
    monkeypatch.setitem(sys.modules, "onnxruntime", onnxruntime)
    monkeypatch.delitem(sys.modules, "faster_whisper", raising=False)
    monkeypatch.setattr(
        "voice_agent.stt.importlib.metadata.distribution",
        lambda name: FakeDistribution(),
    )

    _get_faster_whisper_vad_model.cache_clear()
    try:
        model = _get_faster_whisper_vad_model()
        assert model.session is not None
    finally:
        _get_faster_whisper_vad_model.cache_clear()
    assert recorded["relative"] == "faster_whisper/assets/silero_vad_v6.onnx"
    assert recorded["path"] == str(asset)
    assert recorded["providers"] == ["CPUExecutionProvider"]
    assert "faster_whisper" not in sys.modules


def test_faster_whisper_vad_validates_audio_format():
    vad = FasterWhisperVAD()

    async def scenario():
        assert not await vad.contains_speech(b"")
        with pytest.raises(ValueError, match="16 kHz"):
            await vad.contains_speech(pcm16(1), sample_rate=8_000)
        with pytest.raises(ValueError, match="complete PCM16"):
            await vad.contains_speech(b"\x00")

    asyncio.run(scenario())


def test_streaming_vad_keeps_recurrent_state_per_instance_and_resets(monkeypatch):
    observed = []

    class FakeModel:
        def infer_frame(self, audio, context, hidden, cell):
            observed.append((audio.copy(), context.copy(), hidden.copy(), cell.copy()))
            return 0.75, audio[-64:].copy(), hidden + 1, cell + 2

    monkeypatch.setattr("voice_agent.stt._get_faster_whisper_vad_model", FakeModel)
    vad = FasterWhisperVAD()
    first = pcm16(*range(512))
    second = pcm16(*range(512, 1024))

    async def scenario():
        assert await vad.speech_probability(first) == pytest.approx(0.75)
        assert await vad.speech_probability(second) == pytest.approx(0.75)
        vad.reset()
        assert await vad.speech_probability(first) == pytest.approx(0.75)

    asyncio.run(scenario())
    assert len(observed) == 3
    assert np.all(observed[0][1] == 0)
    assert np.all(observed[0][2] == 0)
    assert np.allclose(observed[1][1], np.arange(448, 512) / 32768.0)
    assert np.all(observed[1][2] == 1)
    assert np.all(observed[1][3] == 2)
    assert np.all(observed[2][1] == 0)
    assert np.all(observed[2][2] == 0)


def test_streaming_vad_requires_exact_silero_frame():
    vad = FasterWhisperVAD()

    async def scenario():
        with pytest.raises(ValueError, match="16 kHz"):
            await vad.speech_probability(pcm16(*([0] * 512)), sample_rate=8_000)
        with pytest.raises(ValueError, match="exactly 512"):
            await vad.speech_probability(pcm16(0))

    asyncio.run(scenario())
