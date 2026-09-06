from __future__ import annotations

from array import array
from collections import deque
from dataclasses import dataclass
import math
import sys
from typing import Protocol
from uuid import uuid4


SAMPLE_RATE = 16_000
FRAME_SAMPLES = 512
FRAME_BYTES = FRAME_SAMPLES * 2
FRAME_MS = FRAME_SAMPLES * 1_000 // SAMPLE_RATE


class SpeechProbabilitySource(Protocol):
    async def speech_probability(
        self, pcm16: bytes, sample_rate: int = SAMPLE_RATE
    ) -> float: ...

    def reset(self) -> None: ...


@dataclass(frozen=True, slots=True)
class EndpointingConfig:
    start_threshold: float = 0.60
    end_threshold: float = 0.35
    min_speech_ms: int = 160
    prefix_padding_ms: int = 320
    silence_ms: int = 768
    suffix_padding_ms: int = 160
    barge_in_threshold: float = 0.72
    barge_in_min_speech_ms: int = 256
    barge_in_prefix_ms: int = 96
    min_rms_dbfs: float = -50.0
    max_audio_ms: int = 120_000

    def __post_init__(self) -> None:
        for name in ("start_threshold", "end_threshold", "barge_in_threshold"):
            value = getattr(self, name)
            if not 0 < value < 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.end_threshold >= self.start_threshold:
            raise ValueError("end_threshold must be lower than start_threshold")
        for name in (
            "min_speech_ms",
            "prefix_padding_ms",
            "silence_ms",
            "suffix_padding_ms",
            "barge_in_min_speech_ms",
            "barge_in_prefix_ms",
            "max_audio_ms",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.silence_ms <= 0 or self.max_audio_ms <= 0:
            raise ValueError("silence_ms and max_audio_ms must be positive")
        if self.suffix_padding_ms > self.silence_ms:
            raise ValueError("suffix_padding_ms cannot exceed silence_ms")
        if not -96.0 <= self.min_rms_dbfs <= 0.0:
            raise ValueError("min_rms_dbfs must be between -96 and 0")

    def protocol_value(self) -> dict[str, int | float | str]:
        return {
            "type": "server_vad",
            "start_threshold": self.start_threshold,
            "end_threshold": self.end_threshold,
            "min_speech_ms": self.min_speech_ms,
            "prefix_padding_ms": self.prefix_padding_ms,
            "silence_ms": self.silence_ms,
            "suffix_padding_ms": self.suffix_padding_ms,
            "barge_in_threshold": self.barge_in_threshold,
            "barge_in_min_speech_ms": self.barge_in_min_speech_ms,
            "barge_in_prefix_ms": self.barge_in_prefix_ms,
            "min_rms_dbfs": self.min_rms_dbfs,
        }


@dataclass(frozen=True, slots=True)
class AudioFrameStats:
    rms_dbfs: float
    peak: float
    zero_fraction: float


@dataclass(frozen=True, slots=True)
class EndpointSnapshot:
    probability: float
    rms_dbfs: float
    peak: float
    zero_fraction: float
    state: str
    speech_ms: int
    silence_ms: int
    buffered_ms: int
    processed_frames: int


def pcm16_stats(pcm16: bytes) -> AudioFrameStats:
    if len(pcm16) % 2:
        raise ValueError("PCM16 statistics require complete samples")
    if not pcm16:
        return AudioFrameStats(rms_dbfs=-96.0, peak=0.0, zero_fraction=1.0)
    samples = array("h")
    samples.frombytes(pcm16)
    if sys.byteorder != "little":
        samples.byteswap()
    count = len(samples)
    square_sum = sum(sample * sample for sample in samples)
    rms = math.sqrt(square_sum / count) / 32768.0
    peak = max(abs(sample) for sample in samples) / 32768.0
    zeros = sum(1 for sample in samples if sample == 0)
    rms_dbfs = 20.0 * math.log10(max(rms, 10 ** (-96.0 / 20.0)))
    return AudioFrameStats(
        rms_dbfs=max(-96.0, rms_dbfs),
        peak=peak,
        zero_fraction=zeros / count,
    )


@dataclass(frozen=True, slots=True)
class SpeechStarted:
    utterance_id: str
    barge_in: bool
    probability: float
    pre_roll_ms: int


@dataclass(frozen=True, slots=True)
class SpeechStopped:
    utterance_id: str
    silence_ms: int
    audio_ms: int


@dataclass(frozen=True, slots=True)
class EndpointCommit:
    utterance_id: str
    audio: bytes
    reason: str
    audio_ms: int


@dataclass(frozen=True, slots=True)
class EndpointUpdate:
    started: SpeechStarted | None = None
    stopped: SpeechStopped | None = None
    committed: EndpointCommit | None = None


def _frames_for(milliseconds: int) -> int:
    if milliseconds <= 0:
        return 0
    return (milliseconds + FRAME_MS - 1) // FRAME_MS


class SpeechEndpointDetector:
    """Turns per-frame Silero probabilities into complete conversational turns."""

    def __init__(self, source: SpeechProbabilitySource, config: EndpointingConfig):
        self.source = source
        self.config = config
        self._pending = bytearray()
        self._manual_audio = bytearray()
        self._history: deque[bytes] = deque()
        self._candidate: list[bytes] = []
        self._candidate_prefix: list[bytes] = []
        self._candidate_barge_in = False
        self._utterance = bytearray()
        self._utterance_id: str | None = None
        self._active = False
        self._silence_frames = 0
        self._speech_frames = 0
        self._processed_frames = 0
        self._last_probability = 0.0
        self._last_stats = AudioFrameStats(-96.0, 0.0, 1.0)

    @property
    def active(self) -> bool:
        return self._active

    @property
    def has_received_audio(self) -> bool:
        return bool(self._manual_audio or self._pending)

    @property
    def snapshot(self) -> EndpointSnapshot:
        if self._active:
            state = "waiting_for_silence" if self._silence_frames else "speech"
        elif self._candidate:
            state = "candidate"
        else:
            state = "silence"
        buffered_bytes = len(self._utterance) if self._active else len(self._manual_audio)
        return EndpointSnapshot(
            probability=self._last_probability,
            rms_dbfs=self._last_stats.rms_dbfs,
            peak=self._last_stats.peak,
            zero_fraction=self._last_stats.zero_fraction,
            state=state,
            speech_ms=self._speech_frames * FRAME_MS,
            silence_ms=self._silence_frames * FRAME_MS,
            buffered_ms=buffered_bytes * 1_000 // (SAMPLE_RATE * 2),
            processed_frames=self._processed_frames,
        )

    async def feed(self, pcm16: bytes, *, output_active: bool) -> list[EndpointUpdate]:
        if len(pcm16) % 2:
            raise ValueError("endpointing expects complete PCM16 samples")
        self._pending.extend(pcm16)
        if not output_active:
            self._manual_audio.extend(pcm16)
            max_manual_bytes = self.config.max_audio_ms * SAMPLE_RATE * 2 // 1_000
            if len(self._manual_audio) > max_manual_bytes:
                del self._manual_audio[:-max_manual_bytes]
        updates: list[EndpointUpdate] = []
        while len(self._pending) >= FRAME_BYTES:
            frame = bytes(self._pending[:FRAME_BYTES])
            del self._pending[:FRAME_BYTES]
            probability = await self.source.speech_probability(frame, SAMPLE_RATE)
            update = self._process_frame(frame, probability, output_active)
            if update is not None:
                updates.append(update)
                if update.committed is not None:
                    # Normal client frames are shorter than an endpoint window.
                    # Discard trailing bytes from an oversized frame rather than
                    # attributing two utterances to one WebSocket message.
                    self._pending.clear()
                    break
        return updates

    def force_commit(self) -> EndpointCommit | None:
        # A manual button may end a confirmed utterance, but it must never turn
        # arbitrary buffered silence into a Whisper request.  Short candidates
        # have not met the configured duration/energy gate and are rejected.
        if not self._active:
            self.reset()
            return None
        audio = bytes(self._utterance + self._pending)
        utterance_id = self._utterance_id or str(uuid4())
        commit = EndpointCommit(
            utterance_id=utterance_id,
            audio=audio,
            reason="manual",
            audio_ms=len(audio) * 1_000 // (SAMPLE_RATE * 2),
        )
        self.reset()
        return commit

    def reset(self) -> None:
        self._pending.clear()
        self._manual_audio.clear()
        self._history.clear()
        self._candidate.clear()
        self._candidate_prefix.clear()
        self._candidate_barge_in = False
        self._utterance.clear()
        self._utterance_id = None
        self._active = False
        self._silence_frames = 0
        self._speech_frames = 0
        self.source.reset()

    def _process_frame(
        self, frame: bytes, probability: float, output_active: bool
    ) -> EndpointUpdate | None:
        probability = max(0.0, min(1.0, float(probability)))
        stats = pcm16_stats(frame)
        self._last_probability = probability
        self._last_stats = stats
        self._processed_frames += 1
        if not self._active:
            return self._process_idle(
                frame, probability, stats.rms_dbfs, output_active
            )

        self._utterance.extend(frame)
        if (
            probability < self.config.end_threshold
            or stats.rms_dbfs < self.config.min_rms_dbfs
        ):
            self._silence_frames += 1
        else:
            self._silence_frames = 0
            self._speech_frames += 1

        audio_ms = len(self._utterance) * 1_000 // (SAMPLE_RATE * 2)
        silence_frames = _frames_for(self.config.silence_ms)
        if self._silence_frames >= silence_frames:
            suffix_frames = min(
                self._silence_frames, _frames_for(self.config.suffix_padding_ms)
            )
            trim_frames = self._silence_frames - suffix_frames
            if trim_frames:
                del self._utterance[-trim_frames * FRAME_BYTES :]
            utterance_id = self._utterance_id or str(uuid4())
            audio = bytes(self._utterance)
            trimmed_audio_ms = len(audio) * 1_000 // (SAMPLE_RATE * 2)
            update = EndpointUpdate(
                stopped=SpeechStopped(
                    utterance_id=utterance_id,
                    silence_ms=self._silence_frames * FRAME_MS,
                    audio_ms=trimmed_audio_ms,
                ),
                committed=EndpointCommit(
                    utterance_id=utterance_id,
                    audio=audio,
                    reason="silence",
                    audio_ms=trimmed_audio_ms,
                ),
            )
            self.reset()
            return update

        if audio_ms >= self.config.max_audio_ms:
            utterance_id = self._utterance_id or str(uuid4())
            audio = bytes(self._utterance)
            update = EndpointUpdate(
                stopped=SpeechStopped(
                    utterance_id=utterance_id,
                    silence_ms=self._silence_frames * FRAME_MS,
                    audio_ms=audio_ms,
                ),
                committed=EndpointCommit(
                    utterance_id=utterance_id,
                    audio=audio,
                    reason="max_duration",
                    audio_ms=audio_ms,
                ),
            )
            self.reset()
            return update
        return None

    def _process_idle(
        self,
        frame: bytes,
        probability: float,
        rms_dbfs: float,
        output_active: bool,
    ) -> EndpointUpdate | None:
        threshold = (
            self.config.barge_in_threshold if output_active else self.config.start_threshold
        )
        min_speech_ms = (
            self.config.barge_in_min_speech_ms
            if output_active
            else self.config.min_speech_ms
        )
        prefix_ms = (
            self.config.barge_in_prefix_ms
            if output_active
            else self.config.prefix_padding_ms
        )

        if self._candidate and output_active != self._candidate_barge_in:
            self._return_candidate_to_history()

        if probability >= threshold and rms_dbfs >= self.config.min_rms_dbfs:
            if not self._candidate:
                prefix_frames = _frames_for(prefix_ms)
                self._candidate_prefix = list(self._history)[-prefix_frames:]
                self._candidate_barge_in = output_active
                self._utterance_id = str(uuid4())
            self._candidate.append(frame)
            if len(self._candidate) >= _frames_for(min_speech_ms):
                self._active = True
                self._speech_frames = len(self._candidate)
                self._utterance = bytearray(
                    b"".join(self._candidate_prefix + self._candidate)
                )
                pre_roll_ms = len(self._candidate_prefix) * FRAME_MS
                self._history.clear()
                self._candidate.clear()
                self._candidate_prefix.clear()
                return EndpointUpdate(
                    started=SpeechStarted(
                        utterance_id=self._utterance_id or str(uuid4()),
                        barge_in=output_active,
                        probability=probability,
                        pre_roll_ms=pre_roll_ms,
                    )
                )
            return None

        self._return_candidate_to_history()
        self._append_history(frame, prefix_ms)
        return None

    def _return_candidate_to_history(self) -> None:
        if self._candidate:
            for frame in self._candidate:
                self._history.append(frame)
        self._candidate.clear()
        self._candidate_prefix.clear()
        self._utterance_id = None

    def _append_history(self, frame: bytes, prefix_ms: int) -> None:
        self._history.append(frame)
        max_frames = _frames_for(prefix_ms)
        while len(self._history) > max_frames:
            self._history.popleft()
