from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
import contextlib
from dataclasses import dataclass, field
import json
import logging
import math
import re
import time
from typing import Any, Protocol, Sequence
from uuid import uuid4

from .ack import ACK_TEXT, CachedAcknowledgement, should_send_ack
from .endpointing import EndpointingConfig, SpeechEndpointDetector
from .llm import ChatMessage, LLM, ToolCall
from .languages import (
    DEFAULT_CONVERSATION_LANGUAGE,
    conversation_language,
    language_directive,
    localized_message,
)
from .protocol import ClientCommand, ReasoningEffort, SessionPhase, event
from .speech_text import MarkdownSpeechRenderer
from .speech_semantics import sentence_abbreviations
from .stt import STT, VAD
from .storage import SQLiteSessionStore, public_messages
from .tools import ToolExecutionResult, ToolExecutor, decode_tool_arguments
from .tts import TTS


log = logging.getLogger("uvicorn.error").getChild("voice_agent.session")


_CONTEXT_CATEGORIES = ("system_prompt", "skills", "tools", "session")


def estimate_text_tokens(text: str) -> int:
    """Return a deterministic tokenizer-free estimate for UI composition.

    The upstream API remains authoritative for the total prompt/completion
    counts.  This estimate is used only to split that total into understandable
    UI categories because OpenAI-compatible usage does not expose per-message
    or per-component token counts.
    """

    value = text.strip()
    return max(1, math.ceil(len(value) / 4)) if value else 0


def context_usage_payload(
    *,
    base_system_prompt: str,
    skills_prompt: str,
    messages: Sequence[ChatMessage],
    tools: Sequence[dict[str, Any]],
    context_size: int,
    input_tokens: int | None = None,
    output_tokens: int = 0,
    prompt_processing_seconds: float | None = None,
) -> dict[str, Any]:
    """Build the protocol payload for the current model request.

    Category sizes are estimates.  When the LLM reports an exact prompt token
    count, the estimates are proportionally reconciled so their sum is exactly
    equal to ``input_tokens``.  This keeps the context bar internally
    consistent while making the estimation boundary explicit.
    """

    session_messages = [
        message.api_value() for message in messages if message.role != "system"
    ]
    raw = {
        "system_prompt": estimate_text_tokens(base_system_prompt),
        "skills": estimate_text_tokens(skills_prompt),
        "tools": estimate_text_tokens(
            json.dumps(list(tools), ensure_ascii=False, separators=(",", ":"))
        ) if tools else 0,
        "session": estimate_text_tokens(
            json.dumps(session_messages, ensure_ascii=False, separators=(",", ":"))
        ) if session_messages else 0,
    }
    raw_total = sum(raw.values())
    exact_input = input_tokens is not None and input_tokens >= 0
    effective_input = input_tokens if exact_input else raw_total
    assert effective_input is not None

    if exact_input and raw_total > 0:
        scaled = {
            key: (raw[key] * effective_input / raw_total) for key in _CONTEXT_CATEGORIES
        }
        categories = {key: math.floor(scaled[key]) for key in _CONTEXT_CATEGORIES}
        remainder = effective_input - sum(categories.values())
        for key in sorted(
            _CONTEXT_CATEGORIES,
            key=lambda item: (scaled[item] - categories[item], raw[item]),
            reverse=True,
        )[:remainder]:
            categories[key] += 1
    else:
        categories = raw

    used = sum(categories.values())
    payload: dict[str, Any] = {
        "input_tokens": effective_input,
        "input_tokens_estimated": not exact_input,
        "output_tokens": max(0, output_tokens),
        "context_size": max(1, context_size),
        "context_used_tokens": used,
        "context_remaining_tokens": max(0, context_size - used),
        "context_breakdown_estimated": True,
        "categories": categories,
    }
    if prompt_processing_seconds is not None and prompt_processing_seconds > 0:
        payload["prompt_processing_tokens_per_second"] = round(
            effective_input / prompt_processing_seconds, 2
        )
        # Wall-clock TTFT includes scheduling and LAN latency, so this is an
        # honest approximation even when the prompt token count itself is exact.
        payload["prompt_processing_estimated"] = True
    return payload


class SessionTransport(Protocol):
    async def send_json(self, value: dict[str, Any]) -> None: ...
    async def send_bytes(self, value: bytes) -> None: ...


class TextSegmenter:
    """Collect streamed tokens into speech-sized, sentence-aware pieces."""

    def __init__(
        self,
        soft_limit: int = 78,
        hard_limit: int = 84,
        max_words: int = 12,
        language: str = "pl",
    ):
        self.buffer = ""
        self.soft_limit = soft_limit
        self.hard_limit = hard_limit
        self.max_words = max_words
        alternatives = "|".join(
            re.escape(value) for value in sentence_abbreviations(language)
        )
        self._abbreviation_end = re.compile(
            rf"(?i)(?<!\w)(?:{alternatives})\.$"
        )

    def push(self, text: str) -> list[str]:
        self.buffer += text
        ready: list[str] = []
        while True:
            split_at = self._next_split()
            if split_at is not None:
                ready.append(self.buffer[:split_at].strip())
                self.buffer = self.buffer[split_at:].lstrip()
                continue
            return [part for part in ready if part]

    def _next_split(self) -> int | None:
        word_matches = list(re.finditer(r"\S+", self.buffer))
        over_words = len(word_matches) > self.max_words
        over_chars = len(self.buffer) > self.soft_limit
        maximum = min(len(self.buffer), self.hard_limit)
        if over_words:
            maximum = min(maximum, word_matches[self.max_words - 1].end())

        for match in re.finditer(r"[.!?…]+(?=\s|$)", self.buffer):
            end = match.end()
            if self._abbreviation_end.search(self.buffer[:end]):
                continue
            if end <= maximum:
                return end
            break

        if not over_words and not over_chars:
            return None

        punctuation = [
            match.end() for match in re.finditer(r"[,;:](?=\s)", self.buffer[:maximum])
        ]
        if punctuation and punctuation[-1] >= min(24, maximum):
            return punctuation[-1]
        whitespace = [
            match.start() for match in re.finditer(r"\s+", self.buffer[: maximum + 1])
        ]
        if whitespace:
            return whitespace[-1]
        # A URL or another indivisible token may exceed the soft limit. Keep it
        # intact until whitespace arrives instead of corrupting it.
        return None

    def flush(self) -> str:
        result = self.buffer.strip()
        self.buffer = ""
        return result


class StreamingMarkdownSpeechSegmenter:
    """Render growing Markdown and release only stable spoken segments.

    The model-facing Markdown remains untouched.  We repeatedly render the
    accumulated document without inventing terminal punctuation, then advance
    an immutable spoken prefix whenever ``TextSegmenter`` finds a sentence or a
    safe size boundary.  Incomplete fenced code, emphasis, links, and ordinary
    parentheses delay emission rather than leaking control characters into TTS.
    """

    def __init__(
        self,
        renderer: MarkdownSpeechRenderer,
        *,
        soft_limit: int,
        hard_limit: int,
        max_words: int,
    ) -> None:
        self.renderer = renderer
        self.soft_limit = soft_limit
        self.hard_limit = hard_limit
        self.max_words = max_words
        self.source = ""
        self._committed_rendered = ""

    def push(self, markdown: str) -> list[str]:
        self.source += markdown
        if not self._markup_is_stable(self.source):
            return []
        return self._drain(final=False)

    def flush(self) -> list[str]:
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> list[str]:
        rendered = self.renderer.render(
            self.source, finalize_document=final
        )
        if not rendered.startswith(self._committed_rendered):
            # Already-spoken audio cannot be retracted.  This guard is
            # intentionally conservative if a malformed Markdown suffix would
            # rewrite an earlier rendered prefix.
            return []
        tail_start = len(self._committed_rendered)
        while tail_start < len(rendered) and rendered[tail_start].isspace():
            tail_start += 1
        tail = rendered[tail_start:]
        segmenter = TextSegmenter(
            soft_limit=self.soft_limit,
            hard_limit=self.hard_limit,
            max_words=self.max_words,
            language=self.renderer.language,
        )
        ready = segmenter.push(tail)
        if final:
            final_segment = segmenter.flush()
            if final_segment:
                ready.append(final_segment)
        if not ready:
            return []

        cursor = tail_start
        for segment in ready:
            start = rendered.find(segment, cursor)
            if start < 0:
                return []
            cursor = start + len(segment)
            while cursor < len(rendered) and rendered[cursor].isspace():
                cursor += 1
        self._committed_rendered = rendered[:cursor]
        return ready

    @staticmethod
    def _markup_is_stable(source: str) -> bool:
        # Escaped Markdown controls do not participate in delimiter balance.
        value = re.sub(r"\\.", "", source)
        if value.count("```") % 2:
            return False
        value = value.replace("```", "")
        if value.count("`") % 2:
            return False
        if value.count("**") % 2 or value.count("__") % 2:
            return False
        if value.count("[") != value.count("]"):
            return False
        if value.count("(") != value.count(")"):
            return False
        # A pipe table can be recognized by markdown-it only after its complete
        # separator row has arrived.  Before that moment the same bytes render
        # as an ordinary paragraph (including the separator dashes).  Never
        # commit such a mutable block: wait for a blank line that closes it, or
        # let flush(final=True) render the completed document at end-of-stream.
        tail_block = re.split(r"\n[ \t]*\n", value)[-1]
        if any(line.count("|") >= 2 for line in tail_block.splitlines()):
            return False
        return True


@dataclass(frozen=True, slots=True)
class _TTSSegment:
    text: str
    index: int


@dataclass(slots=True)
class _TTSWorkerState:
    audio_started: bool = False
    total_ms: int = 0
    completed_segments: int = 0
    max_queue_depth: int = 0


class _OrderedTTSBuffer:
    """Non-blocking producer in front of one bounded, ordered asyncio queue.

    The LLM consumer never awaits synthesis or queue capacity. A short in-memory
    backlog (already represented in response_text) feeds the bounded worker queue
    as slots open. Exactly one worker consumes it, so GPU inference is serialized.
    """

    _SENTINEL = object()

    def __init__(self, maxsize: int):
        self.queue: asyncio.Queue[_TTSSegment | object] = asyncio.Queue(
            maxsize=max(1, maxsize)
        )
        self.pending: deque[_TTSSegment | object] = deque()
        self._segment_depth = 0

    @property
    def depth(self) -> int:
        return self._segment_depth

    def submit(self, segment: _TTSSegment) -> None:
        self.pending.append(segment)
        self._segment_depth += 1
        self._pump()

    def finish(self) -> None:
        self.pending.append(self._SENTINEL)
        self._pump()

    async def get(self) -> _TTSSegment | object:
        item = await self.queue.get()
        if isinstance(item, _TTSSegment):
            self._segment_depth -= 1
        self._pump()
        return item

    def clear(self) -> None:
        self.pending.clear()
        self._segment_depth = 0
        while True:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _pump(self) -> None:
        while self.pending and not self.queue.full():
            self.queue.put_nowait(self.pending.popleft())


@dataclass(slots=True)
class ConversationSession:
    transport: SessionTransport
    llm: LLM
    stt: STT
    vad: VAD
    tts: TTS
    system_prompt: str
    system_prompt_provider: Callable[[], str] | None = None
    context_components_provider: Callable[[], tuple[str, str]] | None = None
    context_size: int = 128_000
    max_audio_bytes: int = 16_000 * 2 * 120
    endpointing_config: EndpointingConfig = field(default_factory=EndpointingConfig)
    tts_queue_max_segments: int = 32
    tts_segment_soft_limit: int = 78
    tts_segment_hard_limit: int = 84
    tts_segment_max_words: int = 12
    tts_defer_until_llm_done: bool = False
    speech_renderer: MarkdownSpeechRenderer = field(
        default_factory=MarkdownSpeechRenderer
    )
    acknowledgement: CachedAcknowledgement | None = None
    ack_min_characters: int = 60
    ack_min_words: int = 12
    session_id: str = field(default_factory=lambda: str(uuid4()))
    resumed: bool = False
    session_store: SQLiteSessionStore | None = None
    tool_executor: ToolExecutor | None = None
    max_tool_steps: int = 8
    reasoning_effort: ReasoningEffort = ReasoningEffort.NONE
    conversation_language: str = DEFAULT_CONVERSATION_LANGUAGE
    phase: SessionPhase = SessionPhase.CONNECTED
    messages: list[ChatMessage] = field(default_factory=list)
    _response_task: asyncio.Task[None] | None = None
    _send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _endpoint: SpeechEndpointDetector = field(init=False)
    _active_response_id: str | None = None
    _playback_response_id: str | None = None
    _closed: bool = False
    _received_audio_messages: int = 0
    _received_audio_bytes: int = 0
    _audio_started_at: float | None = None
    _last_audio_at: float | None = None
    _last_vad_emit_at: float = 0.0
    _last_audio_log_at: float = 0.0

    def __post_init__(self) -> None:
        self.conversation_language = conversation_language(
            self.conversation_language
        ).code
        self._endpoint = SpeechEndpointDetector(self.vad, self.endpointing_config)
        if not self.messages:
            self.messages.append(ChatMessage("system", self.system_prompt))

    @property
    def response_active(self) -> bool:
        return self._response_task is not None and not self._response_task.done()

    @property
    def output_active(self) -> bool:
        return self.response_active or self._playback_response_id is not None

    async def start(self) -> None:
        self._closed = False
        await self._send_json(event(
            "session.ready",
            session_id=self.session_id,
            resumed=self.resumed,
            message_count=len(public_messages(self.messages)),
            audio_format={"encoding": "pcm_s16le", "sample_rate": 16_000, "channels": 1},
            reasoning_effort=self.reasoning_effort.value,
            conversation_language=self.conversation_language,
            turn_detection=self.endpointing_config.protocol_value(),
        ))
        await self._send_json(event(
            "session.history",
            session_id=self.session_id,
            messages=public_messages(self.messages),
        ))
        await self._send_context_metrics(output_tokens=0)
        await self._send_json(event("session.state", state=SessionPhase.CONNECTED.value))
        await self._set_phase(SessionPhase.LISTENING)

    async def close(self) -> None:
        self._closed = True
        await self._cancel_response(notify=False)
        self._endpoint.reset()

    async def append_audio(self, pcm16: bytes) -> None:
        if len(pcm16) % 2:
            await self._send_json(event("error", code="invalid_audio", message="PCM16 frame has odd byte length"))
            return
        if len(pcm16) > self.max_audio_bytes:
            self._endpoint.reset()
            await self._send_json(event("error", code="audio_too_long", message="Audio frame exceeds buffer limit"))
            return
        now = time.monotonic()
        self._received_audio_messages += 1
        self._received_audio_bytes += len(pcm16)
        if self._audio_started_at is None:
            self._audio_started_at = now
        self._last_audio_at = now
        processed_before = self._endpoint.snapshot.processed_frames
        try:
            updates = await self._endpoint.feed(pcm16, output_active=self.output_active)
        except Exception as exc:
            self._endpoint.reset()
            await self._fail("vad_failed", exc)
            return
        snapshot = self._endpoint.snapshot
        if (
            snapshot.processed_frames != processed_before
            and now - self._last_vad_emit_at >= 0.16
        ):
            elapsed = max(0.001, now - (self._audio_started_at or now))
            await self._send_json(event(
                "input_audio.vad",
                probability=round(snapshot.probability, 4),
                rms_dbfs=round(snapshot.rms_dbfs, 2),
                peak=round(snapshot.peak, 5),
                zero_fraction=round(snapshot.zero_fraction, 4),
                state=snapshot.state,
                speech_ms=snapshot.speech_ms,
                silence_ms=snapshot.silence_ms,
                buffered_ms=snapshot.buffered_ms,
                processed_frames=snapshot.processed_frames,
                received_messages=self._received_audio_messages,
                received_bytes=self._received_audio_bytes,
                received_bytes_per_second=round(self._received_audio_bytes / elapsed),
                last_message_bytes=len(pcm16),
            ))
            self._last_vad_emit_at = now
        if now - self._last_audio_log_at >= 1.0:
            log.info(
                "audio_input session_id=%s messages=%d bytes=%d last_message_bytes=%d rms_dbfs=%.2f peak=%.5f zero_fraction=%.4f vad_probability=%.4f vad_state=%s buffered_ms=%d",
                self.session_id,
                self._received_audio_messages,
                self._received_audio_bytes,
                len(pcm16),
                snapshot.rms_dbfs,
                snapshot.peak,
                snapshot.zero_fraction,
                snapshot.probability,
                snapshot.state,
                snapshot.buffered_ms,
            )
            self._last_audio_log_at = now
        for update in updates:
            if update.started is not None:
                started = update.started
                await self._send_json(event(
                    "input_audio.speech_started",
                    utterance_id=started.utterance_id,
                    barge_in=started.barge_in,
                    probability=started.probability,
                    pre_roll_ms=started.pre_roll_ms,
                ))
                log.info(
                    "speech_started session_id=%s utterance_id=%s barge_in=%s probability=%.3f",
                    self.session_id,
                    started.utterance_id,
                    started.barge_in,
                    started.probability,
                )
                if started.barge_in:
                    await self._cancel_response(notify=True, source="vad_barge_in")
            if update.stopped is not None:
                stopped = update.stopped
                await self._send_json(event(
                    "input_audio.speech_stopped",
                    utterance_id=stopped.utterance_id,
                    silence_ms=stopped.silence_ms,
                    audio_ms=stopped.audio_ms,
                ))
                log.info(
                    "speech_stopped session_id=%s utterance_id=%s silence_ms=%d audio_ms=%d",
                    self.session_id,
                    stopped.utterance_id,
                    stopped.silence_ms,
                    stopped.audio_ms,
                )
            if update.committed is not None:
                commit = update.committed
                committed_at = time.monotonic()
                await self._send_json(event(
                    "input_audio.committed",
                    utterance_id=commit.utterance_id,
                    reason=commit.reason,
                    audio_ms=commit.audio_ms,
                ))
                log.info(
                    "input_committed session_id=%s utterance_id=%s reason=%s audio_ms=%d",
                    self.session_id,
                    commit.utterance_id,
                    commit.reason,
                    commit.audio_ms,
                )
                await self._commit_pcm(
                    commit.audio,
                    utterance_id=commit.utterance_id,
                    committed_at=committed_at,
                )

    async def handle_command(self, command: ClientCommand) -> None:
        if command.type == "session.configure":
            assert command.reasoning_effort is not None
            self.reasoning_effort = command.reasoning_effort
            if command.conversation_language is not None:
                self.conversation_language = conversation_language(
                    command.conversation_language
                ).code
            if self.session_store is not None:
                self.session_store.update_reasoning_effort(
                    self.session_id, self.reasoning_effort
                )
                self.session_store.update_conversation_language(
                    self.session_id, self.conversation_language
                )
            log.info(
                "session_configured session_id=%s reasoning_effort=%s conversation_language=%s",
                self.session_id,
                self.reasoning_effort.value,
                self.conversation_language,
            )
            await self._send_json(event(
                "session.ready",
                reasoning_effort=self.reasoning_effort.value,
                conversation_language=self.conversation_language,
            ))
        elif command.type == "response.cancel":
            await self._cancel_response(notify=True, source="client_command")
        elif command.type == "input_audio.commit":
            await self.commit_audio()
        elif command.type == "output_audio.playback.done":
            assert command.response_id is not None
            await self._playback_done(command.response_id)
        elif command.type == "ping":
            await self._send_json(event("pong"))

    async def commit_audio(self) -> None:
        had_audio = self._endpoint.has_received_audio
        snapshot = self._endpoint.snapshot
        commit = self._endpoint.force_commit()
        if commit is None:
            reason = "no_speech" if had_audio else "empty_audio"
            message = localized_message(
                self.conversation_language,
                "no_speech" if had_audio else "empty_audio",
            )
            log.info(
                "input_rejected session_id=%s reason=%s buffered_ms=%d rms_dbfs=%.2f vad_probability=%.4f",
                self.session_id,
                reason,
                snapshot.buffered_ms,
                snapshot.rms_dbfs,
                snapshot.probability,
            )
            await self._send_json(event(
                "input_audio.rejected",
                reason=reason,
                message=message,
                rms_dbfs=round(snapshot.rms_dbfs, 2),
                vad_probability=round(snapshot.probability, 4),
                buffered_ms=snapshot.buffered_ms,
            ))
            return
        committed_at = time.monotonic()
        await self._send_json(event(
            "input_audio.committed",
            utterance_id=commit.utterance_id,
            reason=commit.reason,
            audio_ms=commit.audio_ms,
        ))
        log.info(
            "input_committed session_id=%s utterance_id=%s reason=%s audio_ms=%d",
            self.session_id,
            commit.utterance_id,
            commit.reason,
            commit.audio_ms,
        )
        await self._commit_pcm(
            commit.audio,
            utterance_id=commit.utterance_id,
            committed_at=committed_at,
        )

    async def _commit_pcm(
        self,
        pcm: bytes,
        *,
        utterance_id: str,
        committed_at: float,
    ) -> None:
        await self._cancel_response(notify=False, source="new_utterance")
        await self._set_phase(SessionPhase.TRANSCRIBING)
        stt_started_at = time.monotonic()
        try:
            result = await self.stt.transcribe(
                pcm, language=self.conversation_language
            )
        except Exception as exc:
            await self._fail("stt_failed", exc)
            return
        transcript_at = time.monotonic()
        stt_ms = round((transcript_at - stt_started_at) * 1_000)
        endpoint_to_stt_ms = round((transcript_at - committed_at) * 1_000)
        log.info(
            "transcript_final session_id=%s utterance_id=%s language=%s stt_language=%s stt_ms=%d endpoint_to_stt_ms=%d chars=%d",
            self.session_id,
            utterance_id,
            self.conversation_language,
            result.language,
            stt_ms,
            endpoint_to_stt_ms,
            len(result.text),
        )
        await self._send_json(event(
            "transcript.metrics",
            utterance_id=utterance_id,
            accepted=result.accepted,
            text=result.text,
            confidence=result.confidence,
            no_speech_probability=result.no_speech_probability,
            average_log_probability=result.average_log_probability,
            compression_ratio=result.compression_ratio,
            rejection_reason=result.rejection_reason,
            language=result.language or self.conversation_language,
            stt_ms=stt_ms,
            endpoint_to_stt_ms=endpoint_to_stt_ms,
        ))
        if not result.accepted:
            log.info(
                "transcript_rejected session_id=%s utterance_id=%s reason=%s no_speech_probability=%s average_log_probability=%s compression_ratio=%s",
                self.session_id,
                utterance_id,
                result.rejection_reason,
                result.no_speech_probability,
                result.average_log_probability,
                result.compression_ratio,
            )
            await self._send_json(event(
                "input_audio.rejected",
                reason=result.rejection_reason or "low_confidence",
                message=localized_message(self.conversation_language, "no_speech"),
            ))
            await self._set_phase(SessionPhase.LISTENING)
            return
        transcript = result.text
        transcript_event = event(
            "transcript.final",
            utterance_id=utterance_id,
            text=transcript,
            stt_ms=stt_ms,
            endpoint_to_stt_ms=endpoint_to_stt_ms,
            confidence=result.confidence,
            no_speech_probability=result.no_speech_probability,
            average_log_probability=result.average_log_probability,
            compression_ratio=result.compression_ratio,
            language=result.language or self.conversation_language,
        )
        if not transcript:
            await self._send_json(transcript_event)
            await self._set_phase(SessionPhase.LISTENING)
            return
        self._append_message(ChatMessage("user", transcript))
        await self._send_json(transcript_event)
        await self._set_phase(SessionPhase.THINKING)
        response_id = str(uuid4())
        self._active_response_id = response_id
        # The deployed cached acknowledgement is Polish. Never leak it into an
        # English conversation; English responses start with their first real
        # TTS segment until a separately verified English cache is provisioned.
        ack_requested = self.conversation_language == "pl" and should_send_ack(
            transcript,
            self.reasoning_effort.value,
            min_characters=self.ack_min_characters,
            min_words=self.ack_min_words,
        )
        ack_sent = False
        if ack_requested and self.acknowledgement is not None:
            await self._send_cached_ack(
                response_id,
                committed_at=committed_at,
                transcript_at=transcript_at,
            )
            ack_sent = True
        elif ack_requested:
            log.warning(
                "cached_ack status=disabled_missing session_id=%s response_id=%s",
                self.session_id,
                response_id,
            )
        self._response_task = asyncio.create_task(
            self._run_response(
                response_id,
                committed_at,
                initial_chunk_index=1 if ack_sent else 0,
                audio_already_started=ack_sent,
            ),
            name=f"voice-agent-response-{response_id}",
        )

    async def wait_for_response(self) -> None:
        task = self._response_task
        if task:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run_response(
        self,
        response_id: str,
        committed_at: float,
        *,
        initial_chunk_index: int = 0,
        audio_already_started: bool = False,
    ) -> None:
        response_language = self.conversation_language
        response_text = ""
        reasoning_text = ""
        speech_renderer = MarkdownSpeechRenderer(response_language)
        streaming_speech = StreamingMarkdownSpeechSegmenter(
            speech_renderer,
            soft_limit=self.tts_segment_soft_limit,
            hard_limit=self.tts_segment_hard_limit,
            max_words=self.tts_segment_max_words,
        )
        chunk_index = initial_chunk_index
        llm_started_at = time.monotonic()
        first_content_at: float | None = None
        completion_tokens: int | None = None
        llm_generation_seconds = 0.0
        text_done_sent = False
        tts_buffer = _OrderedTTSBuffer(self.tts_queue_max_segments)
        tts_state = _TTSWorkerState(audio_started=audio_already_started)
        tts_worker = asyncio.create_task(
            self._run_tts_worker(
                tts_buffer,
                tts_state,
                response_id=response_id,
                response_language=response_language,
                committed_at=committed_at,
                first_content_at=lambda: first_content_at,
            ),
            name=f"voice-agent-tts-{response_id}",
        )
        try:
            tool_steps = 0
            final_round_text = ""
            latest_context_metrics: dict[str, Any] = {}
            await self._send_context_metrics(output_tokens=0)
            while True:
                round_text = ""
                round_prompt_tokens: int | None = None
                round_completion_tokens: int | None = None
                round_first_output_at: float | None = None
                assembled_calls: dict[int, dict[str, str]] = {}
                round_started_at = time.monotonic()
                # Once the configured tool budget has been consumed, make one
                # final model round without tools.  Leaving definitions enabled
                # here lets a tool-happy model start an extra network call and
                # only then fail the budget check, which can stall the whole
                # response until the upstream LLM timeout.
                current_tool_definitions = tuple(
                    definition.openai_value()
                    for definition in (
                        self.tool_executor.definitions()
                        if self.tool_executor is not None
                        else ()
                    )
                )
                round_tool_definitions = (
                    current_tool_definitions
                    if tool_steps < self.max_tool_steps
                    else ()
                )
                round_messages = self._messages_for_llm()
                if round_tool_definitions:
                    stream = self.llm.stream(
                        round_messages,
                        self.reasoning_effort.value,
                        round_tool_definitions,
                    )
                else:
                    # Preserve compatibility with simple two-argument adapters
                    # and fixtures when no executable tools are configured.
                    stream = self.llm.stream(
                        round_messages, self.reasoning_effort.value
                    )
                async for delta in stream:
                    if round_first_output_at is None and (
                        delta.reasoning or delta.content or delta.tool_calls
                    ):
                        round_first_output_at = time.monotonic()
                    reasoning_text += delta.reasoning
                    if delta.prompt_tokens is not None:
                        round_prompt_tokens = delta.prompt_tokens
                    if delta.completion_tokens is not None:
                        round_completion_tokens = delta.completion_tokens
                    for tool_delta in delta.tool_calls:
                        item = assembled_calls.setdefault(
                            tool_delta.index,
                            {"id": "", "name": "", "arguments": ""},
                        )
                        item["id"] += tool_delta.id
                        item["name"] += tool_delta.name
                        item["arguments"] += tool_delta.arguments
                    if not delta.content:
                        continue
                    round_text += delta.content
                    response_text += delta.content
                    delta_metrics = {}
                    if first_content_at is None:
                        first_content_at = time.monotonic()
                        llm_ttft_ms = round((first_content_at - llm_started_at) * 1_000)
                        endpoint_to_first_token_ms = round(
                            (first_content_at - committed_at) * 1_000
                        )
                        delta_metrics = {
                            "llm_ttft_ms": llm_ttft_ms,
                            "endpoint_to_first_token_ms": endpoint_to_first_token_ms,
                        }
                        log.info(
                            "llm_first_token session_id=%s response_id=%s llm_ttft_ms=%d endpoint_to_first_token_ms=%d",
                            self.session_id,
                            response_id,
                            llm_ttft_ms,
                            endpoint_to_first_token_ms,
                        )
                        await self._send_json(event(
                            "response.metrics",
                            response_id=response_id,
                            llm_ttft_ms=llm_ttft_ms,
                            endpoint_to_first_token_ms=endpoint_to_first_token_ms,
                        ))
                    await self._send_json(event(
                        "assistant.delta",
                        response_id=response_id,
                        text=delta.content,
                        **delta_metrics,
                    ))
                    if not self.tts_defer_until_llm_done:
                        for spoken in streaming_speech.push(delta.content):
                            if not spoken:
                                continue
                            tts_buffer.submit(_TTSSegment(spoken, chunk_index))
                            chunk_index += 1
                            tts_state.max_queue_depth = max(
                                tts_state.max_queue_depth, tts_buffer.depth
                            )
                            await self._send_json(event(
                                "response.metrics",
                                response_id=response_id,
                                tts_queue_depth=tts_buffer.depth,
                            ))
                round_finished_at = time.monotonic()
                llm_generation_seconds += max(0.001, round_finished_at - round_started_at)
                if round_completion_tokens is not None:
                    completion_tokens = (
                        (completion_tokens or 0) + round_completion_tokens
                    )
                current_output_tokens = completion_tokens if completion_tokens is not None else (
                    estimate_text_tokens(response_text)
                )
                latest_context_metrics = await self._send_context_metrics(
                    messages=round_messages,
                    tools=round_tool_definitions,
                    input_tokens=round_prompt_tokens,
                    output_tokens=current_output_tokens,
                    prompt_processing_seconds=max(
                        0.001,
                        (round_first_output_at or round_finished_at) - round_started_at,
                    ),
                )

                calls = tuple(
                    ToolCall(
                        id=item["id"] or f"call_{uuid4().hex}",
                        name=item["name"],
                        arguments=item["arguments"] or "{}",
                    )
                    for _, item in sorted(assembled_calls.items())
                )
                if not calls:
                    final_round_text = round_text
                    break
                if not round_tool_definitions:
                    raise RuntimeError(
                        "Model returned a tool call after the tool budget was exhausted"
                    )
                tool_steps += 1
                self._append_message(ChatMessage(
                    "assistant", round_text.strip(), tool_calls=calls
                ))
                for call in calls:
                    result = await self._execute_tool_call(
                        call, response_id=response_id, step=tool_steps
                    )
                    self._append_message(ChatMessage(
                        "tool",
                        result.content,
                        name=call.name,
                        tool_call_id=call.id,
                    ))

            llm_finished_at = time.monotonic()
            if self.tts_defer_until_llm_done:
                spoken_response = speech_renderer.render(response_text)
                spoken_segmenter = TextSegmenter(
                    soft_limit=self.tts_segment_soft_limit,
                    hard_limit=self.tts_segment_hard_limit,
                    max_words=self.tts_segment_max_words,
                )
                complete_segments = spoken_segmenter.push(spoken_response)
                final_segment = spoken_segmenter.flush()
                if final_segment:
                    complete_segments.append(final_segment)
            else:
                complete_segments = streaming_speech.flush()
            for segment in complete_segments:
                tts_buffer.submit(_TTSSegment(segment, chunk_index))
                chunk_index += 1
                tts_state.max_queue_depth = max(
                    tts_state.max_queue_depth, tts_buffer.depth
                )
            tts_buffer.finish()
            final_text = final_round_text.strip()
            if final_text:
                self._append_message(ChatMessage("assistant", final_text))
            llm_generation_seconds = max(0.001, llm_generation_seconds)
            estimated_tokens = (
                max(1, round(len(response_text) / 4)) if response_text else 0
            )
            token_count = completion_tokens if completion_tokens is not None else estimated_tokens
            tokens_per_second = round(token_count / llm_generation_seconds, 2)
            llm_generation_ms = round(llm_generation_seconds * 1_000)
            log.info(
                "llm_complete session_id=%s response_id=%s llm_generation_ms=%d completion_tokens=%s estimated_tokens=%d tokens_per_second=%.2f tokens_estimated=%s tts_queue_depth=%d",
                self.session_id,
                response_id,
                llm_generation_ms,
                completion_tokens,
                estimated_tokens,
                tokens_per_second,
                completion_tokens is None,
                tts_buffer.depth,
            )
            await self._send_json(event(
                "response.metrics",
                response_id=response_id,
                text_characters=len(response_text),
                estimated_tokens=estimated_tokens,
                completion_tokens=completion_tokens,
                tokens_per_second=tokens_per_second,
                tokens_estimated=completion_tokens is None,
                llm_generation_ms=llm_generation_ms,
                tts_queue_depth=tts_buffer.depth,
                tts_streaming=not self.tts_defer_until_llm_done,
                input_tokens=latest_context_metrics.get("input_tokens"),
                input_tokens_estimated=latest_context_metrics.get("input_tokens_estimated"),
                output_tokens=token_count,
                context_size=self.context_size,
                prompt_processing_tokens_per_second=latest_context_metrics.get(
                    "prompt_processing_tokens_per_second"
                ),
                prompt_processing_estimated=latest_context_metrics.get(
                    "prompt_processing_estimated", True
                ),
            ))
            await self._send_json(event(
                "assistant.done",
                response_id=response_id,
                text=response_text.strip(),
                cancelled=False,
            ))
            text_done_sent = True

            # Only the audio lifecycle waits for the one ordered TTS worker.
            # LLM deltas, throughput metrics and assistant.done are already out.
            await tts_worker
            log.info(
                "tts_complete session_id=%s response_id=%s segments=%d tts_total_ms=%d max_queue_depth=%d",
                self.session_id,
                response_id,
                tts_state.completed_segments,
                tts_state.total_ms,
                tts_state.max_queue_depth,
            )
            await self._send_json(event(
                "response.metrics",
                response_id=response_id,
                tts_queue_depth=0,
                tts_total_ms=tts_state.total_ms,
                tts_segments=tts_state.completed_segments,
                tts_max_queue_depth=tts_state.max_queue_depth,
                cached_ack=audio_already_started,
            ))
            if tts_state.audio_started:
                await self._send_json(event(
                    "audio.end", response_id=response_id, cancelled=False
                ))
            if not tts_state.audio_started or self._playback_response_id != response_id:
                await self._set_phase(SessionPhase.LISTENING)
        except asyncio.CancelledError:
            tts_buffer.clear()
            if not tts_worker.done():
                tts_worker.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await tts_worker
            if tts_state.audio_started:
                await self._send_json(event(
                    "audio.end", response_id=response_id, cancelled=True
                ))
            self._playback_response_id = None
            if not text_done_sent:
                await self._send_json(event(
                    "assistant.done",
                    response_id=response_id,
                    text=response_text.strip(),
                    cancelled=True,
                ))
            await self._set_phase(SessionPhase.LISTENING)
            raise
        except Exception as exc:
            tts_buffer.clear()
            if not tts_worker.done():
                tts_worker.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await tts_worker
            if tts_state.audio_started:
                await self._send_json(event(
                    "audio.end", response_id=response_id, cancelled=True
                ))
            self._playback_response_id = None
            await self._fail("response_failed", exc)
        finally:
            if self._response_task is asyncio.current_task():
                self._response_task = None
            if self._active_response_id == response_id:
                self._active_response_id = None
            # Make intentional separation explicit and avoid retaining hidden CoT.
            reasoning_text = ""

    async def _execute_tool_call(
        self, call: ToolCall, *, response_id: str, step: int
    ) -> ToolExecutionResult:
        if self.session_store is not None:
            self.session_store.start_tool_call(
                self.session_id,
                call_id=call.id,
                response_id=response_id,
                name=call.name,
                arguments=call.arguments,
                step=step,
            )
        await self._send_json(event(
            "tool.call.started",
            session_id=self.session_id,
            response_id=response_id,
            tool_call_id=call.id,
            name=call.name,
            arguments=call.arguments,
            step=step,
        ))
        arguments, decode_error = decode_tool_arguments(call.arguments)
        if decode_error is not None:
            result = ToolExecutionResult(decode_error, is_error=True)
        elif self.tool_executor is None:
            result = ToolExecutionResult(
                f"No executor is configured for tool: {call.name}", is_error=True
            )
        else:
            assert arguments is not None
            result = await self.tool_executor.execute(call.name, arguments)
        event_type = "tool.call.failed" if result.is_error else "tool.call.completed"
        lifecycle_payload: dict[str, object] = {
            "session_id": self.session_id,
            "response_id": response_id,
            "tool_call_id": call.id,
            "name": call.name,
            "step": step,
        }
        # Successful file/command output remains in the server-side agent
        # context. The macOS client only needs progress, not the potentially
        # large or sensitive result body. Failures stay visible for diagnosis.
        if result.is_error:
            lifecycle_payload["content"] = result.content
        if self.session_store is not None:
            self.session_store.finish_tool_call(
                self.session_id,
                call.id,
                is_error=result.is_error,
                error=result.content if result.is_error else None,
            )
        await self._send_json(event(event_type, **lifecycle_payload))
        log.info(
            "tool_call session_id=%s response_id=%s tool_call_id=%s name=%s step=%d status=%s",
            self.session_id,
            response_id,
            call.id,
            call.name,
            step,
            "failed" if result.is_error else "completed",
        )
        return result

    def _append_message(self, message: ChatMessage) -> None:
        self.messages.append(message)
        if self.session_store is not None:
            self.session_store.append_message(self.session_id, message)

    def _messages_for_llm(self) -> tuple[ChatMessage, ...]:
        """Overlay the current prompt without rewriting durable history.

        Existing sessions keep their original stored system row for audit and
        migration compatibility. Before every LLM round we replace only that
        transient API message with the latest base prompt plus skill index, so
        reload_skills is visible on the next round and in already-open sessions.
        """

        prompt = (
            self.system_prompt_provider()
            if self.system_prompt_provider is not None
            else self.system_prompt
        )
        prompt = (
            f"{prompt.rstrip()}\n\n## Active conversation language\n\n"
            f"{language_directive(self.conversation_language)}"
        )
        messages = list(self.messages)
        if messages and messages[0].role == "system":
            messages[0] = ChatMessage("system", prompt)
        else:
            messages.insert(0, ChatMessage("system", prompt))
        return tuple(messages)

    async def _send_context_metrics(
        self,
        *,
        messages: Sequence[ChatMessage] | None = None,
        tools: Sequence[dict[str, Any]] | None = None,
        input_tokens: int | None = None,
        output_tokens: int = 0,
        prompt_processing_seconds: float | None = None,
    ) -> dict[str, Any]:
        if self.context_components_provider is not None:
            base_prompt, skills_prompt = self.context_components_provider()
        else:
            base_prompt, skills_prompt = self.system_prompt, ""
        if messages is None:
            messages = self._messages_for_llm()
        if tools is None:
            tools = tuple(
                definition.openai_value()
                for definition in (
                    self.tool_executor.definitions()
                    if self.tool_executor is not None
                    else ()
                )
            )
        payload = context_usage_payload(
            base_system_prompt=base_prompt,
            skills_prompt=skills_prompt,
            messages=messages,
            tools=tools,
            context_size=self.context_size,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prompt_processing_seconds=prompt_processing_seconds,
        )
        await self._send_json(event(
            "context.metrics",
            session_id=self.session_id,
            **payload,
        ))
        return payload

    async def _run_tts_worker(
        self,
        buffer: _OrderedTTSBuffer,
        state: _TTSWorkerState,
        *,
        response_id: str,
        response_language: str,
        committed_at: float,
        first_content_at: Callable[[], float | None],
    ) -> None:
        while True:
            item = await buffer.get()
            if item is buffer._SENTINEL:
                return
            assert isinstance(item, _TTSSegment)
            if not state.audio_started:
                state.audio_started = True
                self._playback_response_id = response_id
                await self._set_phase(SessionPhase.SPEAKING)
                await self._send_json(event(
                    "audio.start",
                    response_id=response_id,
                    format="wav",
                    segment_encoding="binary",
                ))
            await self._send_json(event(
                "response.metrics",
                response_id=response_id,
                tts_queue_depth=buffer.depth,
            ))
            tts_ms = await self._speak_segment(
                item.text,
                item.index,
                response_id,
                response_language=response_language,
                committed_at=committed_at,
                first_content_at=first_content_at(),
            )
            state.total_ms += tts_ms
            state.completed_segments += 1

    async def _send_cached_ack(
        self,
        response_id: str,
        *,
        committed_at: float,
        transcript_at: float,
    ) -> None:
        assert self.acknowledgement is not None
        ack = self.acknowledgement
        ready_at = time.monotonic()
        ack_from_commit_ms = round((ready_at - committed_at) * 1_000)
        ack_from_transcript_ms = round((ready_at - transcript_at) * 1_000)
        self._playback_response_id = response_id
        await self._set_phase(SessionPhase.SPEAKING)
        await self._send_json(event(
            "audio.start",
            response_id=response_id,
            format="wav",
            segment_encoding="binary",
        ))
        marker = event(
            "audio.chunk",
            response_id=response_id,
            index=0,
            byte_length=len(ack.data),
            mime_type="audio/wav",
            sample_rate=ack.sample_rate,
            text=ACK_TEXT,
            encoding="binary-next-frame",
            cached_ack=True,
            duration_seconds=round(ack.duration_seconds, 3),
            endpoint_to_first_audio_ms=ack_from_commit_ms,
            transcript_to_ack_ms=ack_from_transcript_ms,
        )

        async def send_pair() -> None:
            async with self._send_lock:
                if self._closed:
                    return
                await self.transport.send_json(marker)
                await self.transport.send_bytes(ack.data)

        await asyncio.shield(send_pair())
        await self._send_json(event(
            "response.metrics",
            response_id=response_id,
            cached_ack=True,
            endpoint_to_first_audio_ms=ack_from_commit_ms,
            transcript_to_ack_ms=ack_from_transcript_ms,
        ))
        log.info(
            "cached_ack status=sent session_id=%s response_id=%s bytes=%d duration=%.3f endpoint_to_ack_ms=%d transcript_to_ack_ms=%d",
            self.session_id,
            response_id,
            len(ack.data),
            ack.duration_seconds,
            ack_from_commit_ms,
            ack_from_transcript_ms,
        )

    async def _speak_segment(
        self,
        text: str,
        index: int,
        response_id: str,
        *,
        response_language: str,
        committed_at: float,
        first_content_at: float | None,
    ) -> int:
        started_at = time.monotonic()
        audio = await self.tts.synthesize(text, language=response_language)
        audio_ready_at = time.monotonic()
        tts_ms = round((audio_ready_at - started_at) * 1_000)
        timing = {}
        if index == 0:
            timing["endpoint_to_first_audio_ms"] = round(
                (audio_ready_at - committed_at) * 1_000
            )
            if first_content_at is not None:
                timing["first_token_to_first_audio_ms"] = round(
                    (audio_ready_at - first_content_at) * 1_000
                )
        marker = event(
            "audio.chunk",
            response_id=response_id,
            index=index,
            byte_length=len(audio.data),
            mime_type=audio.mime_type,
            sample_rate=audio.sample_rate,
            text=text,
            speak_text=audio.speak_text or text,
            voice_profile=audio.voice_profile,
            encoding="binary-next-frame",
            tts_ms=tts_ms,
            duration_seconds=audio.duration_seconds,
            trim_ms=audio.trim_ms,
            **timing,
        )
        if index == 0:
            await self._send_json(event(
                "response.metrics",
                response_id=response_id,
                tts_ms=tts_ms,
                **timing,
            ))
        # A single lock prevents another JSON event from appearing between the
        # binary-next-frame marker and its complete WAV frame.
        async def send_pair() -> None:
            async with self._send_lock:
                if self._closed:
                    return
                await self.transport.send_json(marker)
                await self.transport.send_bytes(audio.data)

        # shield keeps cancellation from splitting the marker/binary pair.
        # The cancellation handler's audio.end waits for the same lock, so it
        # cannot overtake the WAV frame already announced to the client.
        await asyncio.shield(send_pair())
        log.info(
            "audio_segment session_id=%s response_id=%s language=%s voice_profile=%s index=%d bytes=%d tts_ms=%d duration=%s trim_ms=%d visible_text=%r speak_text=%r endpoint_to_first_audio_ms=%s",
            self.session_id,
            response_id,
            response_language,
            audio.voice_profile,
            index,
            len(audio.data),
            tts_ms,
            audio.duration_seconds,
            audio.trim_ms,
            text,
            audio.speak_text or text,
            timing.get("endpoint_to_first_audio_ms"),
        )
        return tts_ms

    async def _cancel_response(self, notify: bool, source: str = "session_close") -> None:
        task = self._response_task
        playback_response_id = self._playback_response_id
        response_id = self._active_response_id or playback_response_id
        task_was_active = task is not None and not task.done()
        if task_was_active:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._response_task = None
        self._active_response_id = None
        self._playback_response_id = None
        had_response = task_was_active or playback_response_id is not None
        if had_response:
            log.info(
                "response_cancelled session_id=%s response_id=%s source=%s",
                self.session_id,
                response_id,
                source,
            )
        if notify and had_response:
            reason = "barge_in" if source == "vad_barge_in" else "client"
            await self._send_json(event(
                "response.cancelled", response_id=response_id, reason=reason
            ))
        if notify and not task_was_active:
            await self._set_phase(SessionPhase.LISTENING)

    async def _playback_done(self, response_id: str) -> None:
        if response_id != self._playback_response_id:
            log.info(
                "playback_ack session_id=%s response_id=%s status=stale expected=%s",
                self.session_id,
                response_id,
                self._playback_response_id,
            )
            return
        self._playback_response_id = None
        log.info(
            "playback_ack session_id=%s response_id=%s status=ok",
            self.session_id,
            response_id,
        )
        if not self._endpoint.active:
            self._endpoint.reset()
        if not self.response_active:
            await self._set_phase(SessionPhase.LISTENING)

    async def _set_phase(self, phase: SessionPhase) -> None:
        self.phase = phase
        await self._send_json(event("session.state", state=phase.value))

    async def _fail(self, code: str, exc: Exception) -> None:
        await self._send_json(event("error", code=code, message=str(exc)))
        await self._set_phase(SessionPhase.LISTENING)

    async def _send_json(self, value: dict[str, Any]) -> None:
        async with self._send_lock:
            if self._closed:
                return
            await self.transport.send_json(value)
