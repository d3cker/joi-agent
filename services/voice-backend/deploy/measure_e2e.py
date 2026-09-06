#!/usr/bin/env python3
"""Sprawdz rozmowe WebSocket 0.1.5 z pliku WAV, bez uzywania mikrofonu."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
import ssl
import time
import wave


SAMPLE_RATE = 16_000
SAMPLE_WIDTH = 2


def pcm_from_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as source:
        actual = (source.getnchannels(), source.getsampwidth(), source.getframerate())
        if actual != (1, SAMPLE_WIDTH, SAMPLE_RATE):
            raise ValueError("WAV musi byc PCM16 mono 16 kHz")
        return source.readframes(source.getnframes())


@dataclass
class ResponseTrace:
    response_id: str
    assistant: str = ""
    markers: int = 0
    binaries: int = 0
    audio_bytes: int = 0
    audio_ended: bool = False
    audio_cancelled: bool | None = None
    assistant_done: bool = False
    assistant_cancelled: bool | None = None
    cancellation_event: bool = False
    cancellation_reason: str | None = None
    indexes: list[int] = field(default_factory=list)
    cached_ack: bool = False

    @property
    def complete(self) -> bool:
        return (
            self.audio_ended
            and self.audio_cancelled is False
            and self.assistant_done
            and self.assistant_cancelled is False
        )


async def send_streamed_utterance(
    websocket,
    audio: bytes,
    *,
    chunk_ms: int,
    trailing_silence_ms: int,
    pace: bool,
    times: dict[str, float],
    label: str,
) -> None:
    frame_bytes = SAMPLE_RATE * SAMPLE_WIDTH * chunk_ms // 1_000
    if frame_bytes <= 0 or frame_bytes % SAMPLE_WIDTH:
        raise ValueError("chunk-ms musi dawac co najmniej jedna pelna probke PCM16")

    for offset in range(0, len(audio), frame_bytes):
        await websocket.send(audio[offset : offset + frame_bytes])
        if pace:
            await asyncio.sleep(chunk_ms / 1_000)
    times[f"{label}_audio_sent"] = time.monotonic()

    silence_bytes = SAMPLE_RATE * SAMPLE_WIDTH * trailing_silence_ms // 1_000
    silence_frame = b"\x00" * frame_bytes
    remaining = silence_bytes
    while remaining:
        frame = silence_frame[:remaining]
        await websocket.send(frame)
        remaining -= len(frame)
        if pace:
            await asyncio.sleep(len(frame) / (SAMPLE_RATE * SAMPLE_WIDTH))
    times[f"{label}_silence_sent"] = time.monotonic()


def require_response_id(event: dict, event_type: str) -> str:
    response_id = event.get("response_id")
    if not isinstance(response_id, str) or not response_id:
        raise RuntimeError(f"{event_type} nie zawiera poprawnego response_id: {event}")
    return response_id


def validate_first_audio_metrics(
    event: dict,
    server_metrics: dict[str, int | float],
) -> None:
    """Validate metrics that differ between cached audio and synthesized TTS."""
    if event.get("cached_ack") is True:
        required = ("duration_seconds", "endpoint_to_first_audio_ms", "transcript_to_ack_ms")
    else:
        required = ("tts_ms", "endpoint_to_first_audio_ms", "first_token_to_first_audio_ms")
    for key in required:
        value = event.get(key)
        if not isinstance(value, (int, float)) or value < 0:
            label = "ack" if event.get("cached_ack") is True else "TTS"
            raise RuntimeError(f"Niepoprawna telemetria {label} {key}: {event}")
        server_metrics[key] = value


def validate_context_metrics(event: dict) -> dict[str, int | float | bool | dict]:
    required_numbers = (
        "input_tokens", "output_tokens", "context_size",
        "context_used_tokens", "context_remaining_tokens",
    )
    for key in required_numbers:
        value = event.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise RuntimeError(f"Niepoprawna telemetria kontekstu {key}: {event}")
    categories = event.get("categories")
    required_categories = {"system_prompt", "skills", "tools", "session"}
    if not isinstance(categories, dict) or set(categories) != required_categories:
        raise RuntimeError(f"Niepoprawne kategorie kontekstu: {event}")
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0
           for value in categories.values()):
        raise RuntimeError(f"Niepoprawne wartości kategorii kontekstu: {event}")
    if sum(categories.values()) != event["context_used_tokens"]:
        raise RuntimeError(f"Suma kategorii nie zgadza się z użytym kontekstem: {event}")
    if event["context_remaining_tokens"] != max(
        0, event["context_size"] - event["context_used_tokens"]
    ):
        raise RuntimeError(f"Niepoprawny pozostały kontekst: {event}")
    return event


async def measure(
    url: str,
    audio: bytes,
    reasoning_effort: str,
    conversation_language: str,
    timeout: float,
    *,
    chunk_ms: int,
    trailing_silence_ms: int,
    min_segments: int,
    pace: bool,
    manual_commit: bool,
    barge_in_audio: bytes | None,
    require_ack: bool,
    client_access_key: str | None = None,
    ca_certificate: Path | None = None,
) -> None:
    try:
        from websockets.asyncio.client import connect
    except ImportError:
        from websockets import connect

    times: dict[str, float] = {}
    server_metrics: dict[str, int | float] = {}
    latest_context_metrics: dict[str, int | float | bool | dict] | None = None
    utterances: list[str] = []
    transcripts: list[str] = []
    responses: dict[str, ResponseTrace] = {}
    response_order: list[str] = []
    pending_marker: tuple[str, int, int] | None = None
    barge_task: asyncio.Task[None] | None = None
    barge_triggered = False

    connect_options = {"max_size": 32 * 1024 * 1024}
    if client_access_key:
        connect_options["additional_headers"] = {
            "Authorization": f"Bearer {client_access_key}"
        }
    if url.startswith("wss://"):
        connect_options["ssl"] = ssl.create_default_context(
            cafile=str(ca_certificate) if ca_certificate else None
        )
    async with connect(url, **connect_options) as websocket:
        turn_detection = None
        while True:
            initial_message = await asyncio.wait_for(websocket.recv(), timeout)
            if isinstance(initial_message, bytes):
                raise RuntimeError("Nieoczekiwane audio przed session.ready")
            initial = json.loads(initial_message)
            if initial.get("type") == "error":
                raise RuntimeError(f"Backend zwrocil blad startowy: {initial}")
            if initial.get("type") == "session.ready":
                turn_detection = initial.get("turn_detection")
            if initial.get("type") == "session.state" and initial.get("state") == "listening":
                break

        if not manual_commit and turn_detection is None:
            raise RuntimeError("session.ready nie potwierdzil turn_detection dla auto-endpointingu")

        await websocket.send(json.dumps({
            "type": "session.configure",
            "reasoning_effort": reasoning_effort,
            "conversation_language": conversation_language,
        }))
        await send_streamed_utterance(
            websocket,
            audio,
            chunk_ms=chunk_ms,
            trailing_silence_ms=0 if manual_commit else trailing_silence_ms,
            pace=pace,
            times=times,
            label="first",
        )
        if manual_commit:
            times["first_manual_commit"] = time.monotonic()
            await websocket.send(json.dumps({"type": "input_audio.commit"}))

        while True:
            message = await asyncio.wait_for(websocket.recv(), timeout)
            now = time.monotonic()
            if isinstance(message, bytes):
                if pending_marker is None:
                    raise RuntimeError("Binarna ramka audio przyszla bez poprzedzajacego audio.chunk")
                response_id, index, expected_length = pending_marker
                pending_marker = None
                if len(message) != expected_length:
                    raise RuntimeError(
                        f"audio.chunk {response_id}/{index}: oczekiwano {expected_length} B, "
                        f"otrzymano {len(message)} B"
                    )
                trace = responses[response_id]
                trace.binaries += 1
                trace.audio_bytes += len(message)
                times.setdefault("first_audio", now)

                if barge_in_audio is not None and not barge_triggered:
                    barge_triggered = True
                    times["barge_in_started"] = now
                    barge_task = asyncio.create_task(send_streamed_utterance(
                        websocket,
                        barge_in_audio,
                        chunk_ms=chunk_ms,
                        trailing_silence_ms=trailing_silence_ms,
                        pace=pace,
                        times=times,
                        label="barge",
                    ))
                continue

            if pending_marker is not None:
                response_id, index, _ = pending_marker
                raise RuntimeError(
                    f"Ramka JSON weszla miedzy audio.chunk {response_id}/{index} a jego WAV"
                )

            event = json.loads(message)
            event_type = event.get("type")
            if event_type == "error":
                raise RuntimeError(f"Backend zwrocil blad: {event}")

            if event_type == "context.metrics":
                latest_context_metrics = validate_context_metrics(event)
                prompt_rate = event.get("prompt_processing_tokens_per_second")
                if prompt_rate is not None and (
                    not isinstance(prompt_rate, (int, float)) or prompt_rate < 0
                ):
                    raise RuntimeError(f"Niepoprawne PP t/s: {event}")
            elif event_type == "input_audio.speech_started":
                utterance_id = event.get("utterance_id")
                if not isinstance(utterance_id, str) or not utterance_id:
                    raise RuntimeError(f"Niepoprawne input_audio.speech_started: {event}")
                utterances.append(utterance_id)
                times.setdefault(f"speech_started_{len(utterances)}", now)
            elif event_type == "input_audio.speech_stopped":
                times.setdefault(f"speech_stopped_{len(transcripts) + 1}", now)
            elif event_type == "input_audio.committed":
                reason = event.get("reason")
                if not manual_commit and reason != "silence":
                    raise RuntimeError(f"Auto-endpoint zakonczyl wypowiedz z powodem {reason!r}")
                times.setdefault(f"committed_{len(transcripts) + 1}", now)
            elif event_type == "transcript.final":
                transcripts.append(str(event.get("text", "")))
                times.setdefault(f"stt_final_{len(transcripts)}", now)
                if len(transcripts) == 1:
                    for key in ("stt_ms", "endpoint_to_stt_ms"):
                        value = event.get(key)
                        if not isinstance(value, (int, float)) or value < 0:
                            raise RuntimeError(f"Brak poprawnej telemetrii {key}: {event}")
                        server_metrics[key] = value
            elif event_type in {
                "assistant.delta", "assistant.done", "audio.start", "audio.chunk",
                "audio.end", "response.cancelled", "response.metrics",
            }:
                response_id = require_response_id(event, str(event_type))
                if response_id not in responses:
                    responses[response_id] = ResponseTrace(response_id)
                    response_order.append(response_id)
                trace = responses[response_id]

                if event_type == "assistant.delta":
                    times.setdefault("llm_first_token", now)
                    trace.assistant += str(event.get("text", ""))
                    if "llm_ttft_ms" in event:
                        for key in ("llm_ttft_ms", "endpoint_to_first_token_ms"):
                            value = event.get(key)
                            if not isinstance(value, (int, float)) or value < 0:
                                raise RuntimeError(f"Niepoprawna telemetria {key}: {event}")
                            server_metrics.setdefault(key, value)
                elif event_type == "assistant.done":
                    trace.assistant_done = True
                    trace.assistant_cancelled = bool(event.get("cancelled", False))
                    times.setdefault("llm_done", now)
                elif event_type == "audio.start":
                    times.setdefault("audio_start_event", now)
                elif event_type == "audio.chunk":
                    index = event.get("index")
                    length = event.get("byte_length")
                    if event.get("encoding") != "binary-next-frame":
                        raise RuntimeError(f"Niepoprawne kodowanie audio.chunk: {event}")
                    if not isinstance(index, int) or index != trace.markers:
                        raise RuntimeError(
                            f"Nieciagly index audio.chunk dla {response_id}: "
                            f"oczekiwano {trace.markers}, otrzymano {index!r}"
                        )
                    if not isinstance(length, int) or length <= 0:
                        raise RuntimeError(f"Niepoprawne byte_length audio.chunk: {event}")
                    if index == 0 and "endpoint_to_first_audio_ms" not in server_metrics:
                        validate_first_audio_metrics(event, server_metrics)
                    trace.markers += 1
                    trace.indexes.append(index)
                    if event.get("cached_ack") is True:
                        if index != 0:
                            raise RuntimeError("Cached ack musi miec index 0")
                        trace.cached_ack = True
                        times.setdefault("cached_ack", now)
                    pending_marker = (response_id, index, length)
                elif event_type == "audio.end":
                    trace.audio_ended = True
                    trace.audio_cancelled = bool(event.get("cancelled", False))
                    times.setdefault("audio_end", now)
                    # During the controlled barge-in test, keep the first
                    # response logically "playing" until the streamed speech
                    # has crossed the server-side VAD threshold.  A very short
                    # first response may otherwise finish generating before
                    # 256 ms of barge-in audio arrives, which would turn this
                    # into a playback-ACK test instead of a VAD cancellation
                    # test.
                    hold_first_ack = (
                        barge_in_audio is not None
                        and bool(response_order)
                        and response_id == response_order[0]
                        and not trace.cancellation_event
                    )
                    if trace.audio_cancelled is False and not hold_first_ack:
                        await websocket.send(json.dumps({
                            "type": "output_audio.playback.done",
                            "response_id": response_id,
                        }))
                elif event_type == "response.cancelled":
                    trace.cancellation_event = True
                    trace.cancellation_reason = str(event.get("reason", "")) or None
                elif event_type == "response.metrics":
                    for key in (
                        "llm_generation_ms", "completion_tokens",
                        "tokens_per_second", "tts_queue_depth", "tts_total_ms",
                        "tts_segments", "tts_max_queue_depth",
                    ):
                        value = event.get(key)
                        if isinstance(value, (int, float)):
                            server_metrics[key] = value

            if not response_order:
                continue
            if barge_in_audio is None:
                if responses[response_order[0]].complete:
                    break
            elif len(response_order) >= 2:
                first, second = (responses[response_order[0]], responses[response_order[1]])
                if first.cancellation_event and second.complete:
                    break

        if barge_task is not None:
            await barge_task

    if pending_marker is not None:
        raise RuntimeError("Polaczenie zakonczono z markerem audio.chunk bez ramki WAV")
    if not transcripts or not transcripts[0].strip():
        raise RuntimeError("Brak niepustej transkrypcji pierwszej wypowiedzi")
    if not utterances:
        raise RuntimeError("Brak input_audio.speech_started")

    successful = [trace for trace in responses.values() if trace.complete]
    if not successful:
        raise RuntimeError("Brak pelnej, nieanulowanej odpowiedzi audio")
    for trace in responses.values():
        if trace.markers != trace.binaries:
            raise RuntimeError(
                f"Odpowiedz {trace.response_id}: markery={trace.markers}, WAV={trace.binaries}"
            )
    for trace in successful:
        if trace.markers < min_segments:
            raise RuntimeError(
                f"Pelna odpowiedz {trace.response_id} zawierala {trace.markers} "
                f"segment(y); wymagane minimum to {min_segments}"
            )
    if require_ack and not any(trace.cached_ack for trace in responses.values()):
        raise RuntimeError("Długa wypowiedź nie otrzymała wymaganego cached ack")

    if barge_in_audio is not None:
        if len(transcripts) < 2 or not transcripts[1].strip():
            raise RuntimeError("Barge-in nie zostal automatycznie zatwierdzony i przepisany")
        first = responses[response_order[0]]
        if not first.cancellation_event:
            raise RuntimeError("Brak response.cancelled po kontrolowanym barge-in")
        if first.cancellation_reason != "barge_in":
            raise RuntimeError(
                f"Kontrolowany barge-in ma niepoprawny powod anulowania: "
                f"{first.cancellation_reason!r}"
            )

    first_commit = times.get("committed_1", times.get("first_manual_commit"))
    first_stt = times.get("stt_final_1")
    first_audio = times.get("first_audio")
    first_token = times.get("llm_first_token")
    if None in (first_commit, first_stt, first_audio, first_token):
        raise RuntimeError("Brak zdarzen potrzebnych do pomiaru opoznien")
    required_server_metrics = {
        "stt_ms",
        "endpoint_to_stt_ms",
        "llm_ttft_ms",
        "endpoint_to_first_token_ms",
        "endpoint_to_first_audio_ms",
    }
    if not any(trace.cached_ack for trace in responses.values()):
        required_server_metrics.update({"tts_ms", "first_token_to_first_audio_ms"})
    missing_metrics = sorted(required_server_metrics - server_metrics.keys())
    if missing_metrics:
        raise RuntimeError(f"Brak telemetrii serwera: {', '.join(missing_metrics)}")
    if latest_context_metrics is None:
        raise RuntimeError("Brak context.metrics dla panelu Etapu C")

    print(f"Transkrypcja: {transcripts[0]}")
    print(f"Odpowiedz content: {successful[-1].assistant.strip()}")
    print(f"VAD commit -> STT final (serwer): {server_metrics['endpoint_to_stt_ms']} ms")
    print(f"Czyste STT (serwer): {server_metrics['stt_ms']} ms")
    print(f"LLM time-to-first-token (serwer): {server_metrics['llm_ttft_ms']} ms")
    print(
        "Context: "
        f"input={latest_context_metrics['input_tokens']}, "
        f"output={latest_context_metrics['output_tokens']}, "
        f"used={latest_context_metrics['context_used_tokens']}/"
        f"{latest_context_metrics['context_size']}, "
        f"PP={latest_context_metrics.get('prompt_processing_tokens_per_second', 'brak')} t/s, "
        f"categories={latest_context_metrics['categories']}"
    )
    if "llm_generation_ms" in server_metrics:
        token_source = "usage" if "completion_tokens" in server_metrics else "estymacja"
        print(
            "LLM generation: "
            f"{server_metrics['llm_generation_ms']} ms, "
            f"{server_metrics.get('tokens_per_second', 'brak')} t/s ({token_source})"
        )
    elif "tokens_per_second" in server_metrics:
        print(
            "LLM throughput raportowany przez stary backend (obejmuje TTS): "
            f"{server_metrics['tokens_per_second']} t/s"
        )
    print(
        "VAD commit -> first audio (serwer): "
        f"{server_metrics['endpoint_to_first_audio_ms']} ms"
    )
    if "first_token_to_first_audio_ms" in server_metrics:
        print(
            "First token -> first audio (serwer): "
            f"{server_metrics['first_token_to_first_audio_ms']} ms"
        )
    else:
        print("First token -> first audio: nie dotyczy (cache ack poprzedza LLM)")
    if "tts_ms" in server_metrics:
        print(f"Pierwszy segment TTS (serwer): {server_metrics['tts_ms']} ms")
    else:
        print("Pierwszy segment audio: cache ack, bez inferencji TTS")
    if "transcript_to_ack_ms" in server_metrics:
        print(
            "Cached ack: "
            f"commit→ack={server_metrics['endpoint_to_first_audio_ms']} ms, "
            f"transcript→ack={server_metrics['transcript_to_ack_ms']} ms"
        )
    if "tts_total_ms" in server_metrics:
        print(
            "TTS lacznie: "
            f"{server_metrics['tts_total_ms']} ms, "
            f"segmenty={server_metrics.get('tts_segments', 'brak')}, "
            f"max kolejka={server_metrics.get('tts_max_queue_depth', 'brak')}"
        )
    print(f"Endpoint -> first audio bytes (obserwacja klienta): {first_audio - first_commit:.3f} s")
    for trace in responses.values():
        print(
            f"Response {trace.response_id}: segmenty={trace.binaries}, "
            f"audio={trace.audio_bytes} B, cancelled={trace.audio_cancelled}"
            f", cached_ack={trace.cached_ack}"
        )
    if barge_in_audio is not None:
        print("Kontrolowany barge-in: PASS (nie jest testem akustycznego AEC)")
    print("Kontrakt marker -> WAV, response_id, audio.end i playback.done: PASS")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path, help="polska wypowiedz jako PCM16 mono 16 kHz WAV")
    parser.add_argument("--url", required=True, help="Explicit target WebSocket endpoint")
    parser.add_argument("--client-key-file", type=Path)
    parser.add_argument("--ca-certificate", type=Path)
    parser.add_argument("--reasoning-effort", choices=("none", "low", "high", "max"), default="none")
    parser.add_argument(
        "--conversation-language",
        choices=("en", "pl"),
        default="pl",
        help="jawny jezyk STT, odpowiedzi LLM i TTS",
    )
    parser.add_argument("--timeout", type=float, default=180.0, help="limit oczekiwania na pojedyncza ramke")
    parser.add_argument("--chunk-ms", type=int, default=20, help="rozmiar porcji PCM; domyslnie 20 ms")
    parser.add_argument(
        "--trailing-silence-ms", type=int, default=1_400,
        help="cisza dopisywana do uruchomienia auto-endpointu",
    )
    parser.add_argument(
        "--min-segments", type=int, default=2,
        help="minimalna liczba WAV w pelnej odpowiedzi; domyslnie 2",
    )
    parser.add_argument("--no-pace", action="store_true", help="wyslij PCM bez tempa czasu rzeczywistego")
    parser.add_argument(
        "--manual-commit", action="store_true",
        help="awaryjny test starego input_audio.commit zamiast auto-endpointu",
    )
    parser.add_argument(
        "--barge-in-wav", type=Path,
        help="druga polska wypowiedz wyslana po pierwszym WAV odpowiedzi",
    )
    parser.add_argument(
        "--require-ack", action="store_true",
        help="wymagaj cached ack jako segmentu 0",
    )
    args = parser.parse_args()
    if args.chunk_ms <= 0 or args.trailing_silence_ms <= 0 or args.min_segments <= 0:
        parser.error("--chunk-ms, --trailing-silence-ms i --min-segments musza byc dodatnie")
    if args.manual_commit and args.barge_in_wav is not None:
        parser.error("--manual-commit nie moze byc laczony z --barge-in-wav")
    asyncio.run(measure(
        args.url,
        pcm_from_wav(args.wav),
        args.reasoning_effort,
        args.conversation_language,
        args.timeout,
        chunk_ms=args.chunk_ms,
        trailing_silence_ms=args.trailing_silence_ms,
        min_segments=args.min_segments,
        pace=not args.no_pace,
        manual_commit=args.manual_commit,
        barge_in_audio=pcm_from_wav(args.barge_in_wav) if args.barge_in_wav else None,
        require_ack=args.require_ack,
        client_access_key=(
            args.client_key_file.read_text(encoding="utf-8").strip()
            if args.client_key_file else None
        ),
        ca_certificate=args.ca_certificate,
    ))


if __name__ == "__main__":
    main()
