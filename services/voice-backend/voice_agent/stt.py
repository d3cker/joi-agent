from __future__ import annotations

import asyncio
from dataclasses import dataclass
import functools
import importlib.metadata
import math
import multiprocessing
import threading
from typing import Protocol

from .languages import conversation_language


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    accepted: bool
    confidence: float | None = None
    no_speech_probability: float | None = None
    average_log_probability: float | None = None
    compression_ratio: float | None = None
    rejection_reason: str | None = None
    language: str | None = None


class STT(Protocol):
    async def transcribe(
        self, pcm16: bytes, sample_rate: int = 16_000, language: str = "pl"
    ) -> TranscriptionResult: ...


class VAD(Protocol):
    async def contains_speech(self, pcm16: bytes, sample_rate: int = 16_000) -> bool: ...

    async def speech_probability(
        self, pcm16: bytes, sample_rate: int = 16_000
    ) -> float: ...

    def reset(self) -> None: ...


class MockSTT:
    def __init__(self, transcript: str = "Test mikrofonu"):
        self.transcript = transcript

    async def transcribe(
        self, pcm16: bytes, sample_rate: int = 16_000, language: str = "pl"
    ) -> TranscriptionResult:
        language = conversation_language(language).code
        text = self.transcript if pcm16 else ""
        return TranscriptionResult(
            text=text,
            accepted=bool(text),
            confidence=0.99 if text else 0.0,
            no_speech_probability=0.01 if text else 1.0,
            average_log_probability=-0.01 if text else -2.0,
            compression_ratio=1.0,
            rejection_reason=None if text else "empty",
            language=language,
        )


class MockVAD:
    def __init__(self, speech: bool = True):
        self.speech = speech

    async def contains_speech(self, pcm16: bytes, sample_rate: int = 16_000) -> bool:
        return bool(pcm16) and self.speech

    async def speech_probability(
        self, pcm16: bytes, sample_rate: int = 16_000
    ) -> float:
        if sample_rate != 16_000:
            raise ValueError("VAD expects PCM16 mono at 16 kHz")
        return 1.0 if pcm16 and self.speech else 0.0

    def reset(self) -> None:
        pass


def _faster_whisper_worker(
    connection,
    model_path: str,
    device: str,
    device_index: int,
    compute_type: str,
) -> None:
    """Own CTranslate2 and its CUDA libraries for the worker lifetime."""
    try:
        import numpy as np
        from faster_whisper import WhisperModel

        model = WhisperModel(
            model_path,
            device=device,
            device_index=device_index,
            compute_type=compute_type,
            local_files_only=True,
        )
        connection.send(("ready", None))
    except Exception as exc:
        try:
            connection.send(("startup_error", f"{type(exc).__name__}: {exc}"))
        finally:
            connection.close()
        return

    try:
        while True:
            try:
                command, payload = connection.recv()
            except EOFError:
                return
            if command == "close":
                return
            if command != "transcribe" or not isinstance(payload, dict):
                connection.send(("error", "invalid STT worker request"))
                continue
            try:
                audio_bytes = payload.get("audio")
                language = payload.get("language")
                if not isinstance(audio_bytes, bytes) or not isinstance(language, str):
                    raise ValueError("invalid STT worker payload")
                language = conversation_language(language).stt_language
                audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                segments, info = model.transcribe(
                    audio,
                    language=language,
                    task="transcribe",
                    beam_size=5,
                    vad_filter=False,  # Silero is managed explicitly by the session.
                    condition_on_previous_text=False,
                )
                segments = list(segments)
                text = " ".join(segment.text.strip() for segment in segments).strip()
                weights = [max(0.01, float(segment.end) - float(segment.start)) for segment in segments]
                weight_sum = sum(weights)
                average_log_probability = (
                    sum(float(segment.avg_logprob) * weight for segment, weight in zip(segments, weights))
                    / weight_sum
                    if segments
                    else None
                )
                no_speech_probability = (
                    sum(float(segment.no_speech_prob) * weight for segment, weight in zip(segments, weights))
                    / weight_sum
                    if segments
                    else 1.0
                )
                compression_ratio = (
                    max(float(segment.compression_ratio) for segment in segments)
                    if segments
                    else None
                )
                connection.send(("ok", {
                    "text": text,
                    "average_log_probability": average_log_probability,
                    "no_speech_probability": no_speech_probability,
                    "compression_ratio": compression_ratio,
                    "language_probability": getattr(info, "language_probability", None),
                    "language": getattr(info, "language", language),
                }))
            except Exception as exc:
                connection.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


class FasterWhisperSTT:
    """Persistent spawn worker isolates CTranslate2 from the TTS PyTorch process."""

    def __init__(
        self,
        model_path: str,
        device: str = "cuda",
        device_index: int = 0,
        compute_type: str = "float16",
        *,
        startup_timeout: float = 180,
        request_timeout: float = 300,
        no_speech_threshold: float = 0.60,
        log_probability_threshold: float = -1.0,
        compression_ratio_threshold: float = 2.4,
        _worker_target=None,
    ):
        self.model_path = model_path
        self.device = device
        self.device_index = device_index
        self.compute_type = compute_type
        self.startup_timeout = startup_timeout
        self.request_timeout = request_timeout
        self.no_speech_threshold = no_speech_threshold
        self.log_probability_threshold = log_probability_threshold
        self.compression_ratio_threshold = compression_ratio_threshold
        self._worker_target = _worker_target or _faster_whisper_worker
        self._context = multiprocessing.get_context("spawn")
        self._connection = None
        self._process = None
        self._lock = threading.Lock()

    def _stop_worker_locked(self) -> None:
        connection, process = self._connection, self._process
        self._connection = None
        self._process = None
        if connection is not None:
            if process is not None and process.is_alive():
                try:
                    connection.send(("close", None))
                except (BrokenPipeError, EOFError, OSError):
                    pass
            connection.close()
        if process is not None:
            process.join(timeout=2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)

    def _receive_locked(self, timeout: float, operation: str):
        assert self._connection is not None and self._process is not None
        connection, process = self._connection, self._process
        if not connection.poll(timeout):
            exit_code = process.exitcode
            self._stop_worker_locked()
            if exit_code is not None:
                raise RuntimeError(
                    f"STT worker exited during {operation} (exit code {exit_code})"
                )
            raise TimeoutError(f"STT worker timed out during {operation}")
        try:
            message = connection.recv()
        except (EOFError, OSError) as exc:
            # EOF can become visible a fraction before multiprocessing has
            # reaped the worker and populated ``exitcode``.  Stop/join first,
            # then report the stable value; otherwise diagnostics are racy and
            # may misleadingly say ``None`` for a process that exited itself.
            self._stop_worker_locked()
            exit_code = process.exitcode
            raise RuntimeError(
                f"STT worker exited during {operation} (exit code {exit_code})"
            ) from exc
        if not isinstance(message, tuple) or len(message) != 2:
            self._stop_worker_locked()
            raise RuntimeError(f"STT worker sent an invalid response during {operation}")
        return message

    def _ensure_worker_locked(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        self._stop_worker_locked()
        parent_connection, child_connection = self._context.Pipe(duplex=True)
        process = self._context.Process(
            target=self._worker_target,
            args=(
                child_connection,
                self.model_path,
                self.device,
                self.device_index,
                self.compute_type,
            ),
            name="voice-agent-stt",
            daemon=True,
        )
        try:
            process.start()
        except Exception:
            parent_connection.close()
            child_connection.close()
            raise
        child_connection.close()
        self._connection = parent_connection
        self._process = process
        status, payload = self._receive_locked(self.startup_timeout, "startup")
        if status != "ready":
            self._stop_worker_locked()
            raise RuntimeError(
                f"STT worker failed to start: {payload or 'invalid startup response'}"
            )

    def _transcribe_sync(self, pcm16: bytes, language: str) -> TranscriptionResult:
        language = conversation_language(language).code
        with self._lock:
            self._ensure_worker_locked()
            assert self._connection is not None and self._process is not None
            if not self._process.is_alive():
                exit_code = self._process.exitcode
                self._stop_worker_locked()
                raise RuntimeError(f"STT worker exited (exit code {exit_code})")
            try:
                self._connection.send((
                    "transcribe",
                    {"audio": pcm16, "language": language},
                ))
            except (BrokenPipeError, EOFError, OSError) as exc:
                exit_code = self._process.exitcode
                self._stop_worker_locked()
                raise RuntimeError(f"STT worker exited (exit code {exit_code})") from exc
            status, payload = self._receive_locked(self.request_timeout, "transcription")
            if status == "ok" and isinstance(payload, dict):
                text = str(payload.get("text") or "").strip()
                average_log_probability = payload.get("average_log_probability")
                no_speech_probability = payload.get("no_speech_probability")
                compression_ratio = payload.get("compression_ratio")
                low_confidence_silence = (
                    isinstance(no_speech_probability, (int, float))
                    and isinstance(average_log_probability, (int, float))
                    and no_speech_probability > self.no_speech_threshold
                    and average_log_probability < self.log_probability_threshold
                )
                excessive_compression = (
                    isinstance(compression_ratio, (int, float))
                    and compression_ratio > self.compression_ratio_threshold
                )
                if not text:
                    rejection_reason = "empty"
                elif low_confidence_silence:
                    rejection_reason = "no_speech"
                elif excessive_compression:
                    rejection_reason = "compression_ratio"
                else:
                    rejection_reason = None
                confidence = (
                    max(0.0, min(1.0, math.exp(float(average_log_probability))))
                    if isinstance(average_log_probability, (int, float))
                    else None
                )
                return TranscriptionResult(
                    text=text if rejection_reason is None else "",
                    accepted=rejection_reason is None,
                    confidence=confidence,
                    no_speech_probability=(
                        float(no_speech_probability)
                        if isinstance(no_speech_probability, (int, float))
                        else None
                    ),
                    average_log_probability=(
                        float(average_log_probability)
                        if isinstance(average_log_probability, (int, float))
                        else None
                    ),
                    compression_ratio=(
                        float(compression_ratio)
                        if isinstance(compression_ratio, (int, float))
                        else None
                    ),
                    rejection_reason=rejection_reason,
                    language=str(payload.get("language") or language),
                )
            # Compatibility for deliberately minimal test workers and overlays.
            if status == "ok" and isinstance(payload, str):
                return TranscriptionResult(
                    text=payload, accepted=bool(payload), language=language
                )
            if status == "error":
                raise RuntimeError(f"STT worker transcription failed: {payload}")
            self._stop_worker_locked()
            raise RuntimeError("STT worker sent an invalid transcription response")

    async def transcribe(
        self, pcm16: bytes, sample_rate: int = 16_000, language: str = "pl"
    ) -> TranscriptionResult:
        if sample_rate != 16_000:
            raise ValueError("STT expects PCM16 mono at 16 kHz")
        if len(pcm16) % 2:
            raise ValueError("STT expects complete PCM16 samples")
        return await asyncio.to_thread(self._transcribe_sync, pcm16, language)

    def close(self) -> None:
        with self._lock:
            self._stop_worker_locked()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class _FasterWhisperSileroModel:
    """ONNX runner adapted from faster-whisper v1.2.1's SileroVADModel."""

    def __init__(self, path):
        import onnxruntime

        options = onnxruntime.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        options.log_severity_level = 4
        self.session = onnxruntime.InferenceSession(
            str(path),
            providers=["CPUExecutionProvider"],
            sess_options=options,
        )

    def __call__(self, audio, num_samples: int = 512, context_size_samples: int = 64):
        import numpy as np

        if audio.ndim != 1 or audio.shape[0] % num_samples:
            raise ValueError("Silero VAD input must contain complete 512-sample windows")
        hidden = np.zeros((1, 1, 128), dtype=np.float32)
        cell = np.zeros((1, 1, 128), dtype=np.float32)
        batched_audio = audio.reshape(-1, num_samples)
        context = batched_audio[..., -context_size_samples:]
        context[-1] = 0
        context = np.roll(context, 1, axis=0)
        batched_audio = np.concatenate([context, batched_audio], axis=1)
        outputs = []
        for start in range(0, len(batched_audio), 10_000):
            output, hidden, cell = self.session.run(
                None,
                {
                    "input": batched_audio[start : start + 10_000],
                    "h": hidden,
                    "c": cell,
                },
            )
            outputs.append(output)
        return np.concatenate(outputs, axis=0)

    def infer_frame(self, audio, context, hidden, cell):
        """Infer one 32 ms window while the caller owns recurrent state."""
        import numpy as np

        if audio.shape != (512,) or context.shape != (64,):
            raise ValueError("Silero stream expects 512 samples and 64 samples of context")
        model_input = np.concatenate((context, audio))[None, :].astype(
            np.float32, copy=False
        )
        output, hidden, cell = self.session.run(
            None,
            {"input": model_input, "h": hidden, "c": cell},
        )
        probability = float(np.asarray(output).reshape(-1)[-1])
        return probability, audio[-64:].copy(), hidden, cell


@functools.lru_cache(maxsize=1)
def _get_faster_whisper_vad_model():
    distribution = importlib.metadata.distribution("faster-whisper")
    model_path = distribution.locate_file(
        "faster_whisper/assets/silero_vad_v6.onnx"
    )
    if not model_path.is_file():
        raise RuntimeError(f"faster-whisper Silero VAD asset is missing: {model_path}")
    return _FasterWhisperSileroModel(model_path)


class FasterWhisperVAD:
    """Per-session VAD using faster-whisper's ONNX asset without importing it."""

    def __init__(
        self,
        threshold: float = 0.6,
        window_ms: int = 512,
        min_speech_duration_ms: int = 48,
        recent_ms: int = 96,
    ):
        if not 0 < threshold < 1:
            raise ValueError("VAD threshold must be between 0 and 1")
        if min(window_ms, min_speech_duration_ms, recent_ms) <= 0:
            raise ValueError("VAD durations must be positive")
        if recent_ms > window_ms:
            raise ValueError("VAD recent window cannot exceed rolling window")

        self.threshold = threshold
        self.window_ms = window_ms
        self.min_speech_duration_ms = min_speech_duration_ms
        self.recent_ms = recent_ms
        self._tail = bytearray()
        self._lock = threading.Lock()
        self._stream_context = None
        self._stream_hidden = None
        self._stream_cell = None

    @staticmethod
    def _speech_probabilities(audio):
        import numpy as np

        padding = (-len(audio)) % 512
        if padding:
            audio = np.pad(audio, (0, padding))
        return _get_faster_whisper_vad_model()(audio)

    async def contains_speech(self, pcm16: bytes, sample_rate: int = 16_000) -> bool:
        if sample_rate != 16_000:
            raise ValueError("VAD expects PCM16 mono at 16 kHz")
        if len(pcm16) % 2:
            raise ValueError("VAD expects complete PCM16 samples")
        if not pcm16:
            return False

        def run() -> bool:
            import numpy as np

            with self._lock:
                self._tail.extend(pcm16)
                max_samples = sample_rate * self.window_ms // 1_000
                max_bytes = max_samples * 2
                if len(self._tail) > max_bytes:
                    del self._tail[:-max_bytes]

                audio = (
                    np.frombuffer(bytes(self._tail), dtype=np.int16).astype(np.float32)
                    / 32768.0
                )
                probabilities = self._speech_probabilities(audio)
                recent_samples = sample_rate * self.recent_ms // 1_000
                recent_start = max(0, len(audio) - recent_samples)
                min_speech_samples = (
                    sample_rate * self.min_speech_duration_ms // 1_000
                )
                active_samples = 0
                detected = False
                for index, probability in enumerate(probabilities):
                    window_start = index * 512
                    window_end = min(len(audio), window_start + 512)
                    probability = float(np.asarray(probability).reshape(-1)[0])
                    if probability >= self.threshold:
                        active_samples += max(0, window_end - window_start)
                        if (
                            active_samples >= min_speech_samples
                            and window_end > recent_start
                        ):
                            detected = True
                            break
                    else:
                        active_samples = 0
                if detected:
                    self._tail.clear()
                return detected

        return await asyncio.to_thread(run)

    async def speech_probability(
        self, pcm16: bytes, sample_rate: int = 16_000
    ) -> float:
        if sample_rate != 16_000:
            raise ValueError("VAD expects PCM16 mono at 16 kHz")
        if len(pcm16) != 512 * 2:
            raise ValueError("streaming VAD expects exactly 512 PCM16 samples")

        def run() -> float:
            import numpy as np

            audio = (
                np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
            )
            with self._lock:
                if self._stream_context is None:
                    self._stream_context = np.zeros(64, dtype=np.float32)
                    self._stream_hidden = np.zeros((1, 1, 128), dtype=np.float32)
                    self._stream_cell = np.zeros((1, 1, 128), dtype=np.float32)
                (
                    probability,
                    self._stream_context,
                    self._stream_hidden,
                    self._stream_cell,
                ) = _get_faster_whisper_vad_model().infer_frame(
                    audio,
                    self._stream_context,
                    self._stream_hidden,
                    self._stream_cell,
                )
                return probability

        return await asyncio.to_thread(run)

    def reset(self) -> None:
        with self._lock:
            self._tail.clear()
            self._stream_context = None
            self._stream_hidden = None
            self._stream_cell = None
