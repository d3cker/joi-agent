#!/usr/bin/env python3
"""Verify that streamed digital silence never reaches STT or the LLM."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import ssl


async def verify(
    url: str,
    timeout: float,
    client_access_key: str | None = None,
    ca_certificate: Path | None = None,
) -> None:
    try:
        from websockets.asyncio.client import connect
    except ImportError:
        from websockets import connect

    speech_started = False
    transcript_seen = False
    last_vad = None
    rejected = None
    connect_options = {"max_size": 4 * 1024 * 1024}
    if client_access_key:
        connect_options["additional_headers"] = {
            "Authorization": f"Bearer {client_access_key}"
        }
    if url.startswith("wss://"):
        connect_options["ssl"] = ssl.create_default_context(
            cafile=str(ca_certificate) if ca_certificate else None
        )
    async with connect(url, **connect_options) as websocket:
        while True:
            raw = await asyncio.wait_for(websocket.recv(), timeout)
            if isinstance(raw, bytes):
                raise RuntimeError("unexpected binary frame before ready")
            event = json.loads(raw)
            if event.get("type") == "session.state" and event.get("state") == "listening":
                break

        await websocket.send(json.dumps({
            "type": "session.configure",
            "reasoning_effort": "none",
        }))
        frame = b"\x00\x00" * 512
        for _ in range(63):
            await websocket.send(frame)
            await asyncio.sleep(0.032)

        # Silence must not auto-commit; the emergency button must reject it.
        await websocket.send(json.dumps({"type": "input_audio.commit"}))
        while rejected is None:
            raw = await asyncio.wait_for(websocket.recv(), timeout)
            if isinstance(raw, bytes):
                raise RuntimeError("silence unexpectedly produced audio")
            event = json.loads(raw)
            event_type = event.get("type")
            if event_type == "input_audio.speech_started":
                speech_started = True
            elif event_type == "transcript.final":
                transcript_seen = True
            elif event_type == "input_audio.vad":
                last_vad = event
            elif event_type == "input_audio.rejected":
                rejected = event
            elif event_type == "error":
                raise RuntimeError(f"backend error: {event}")

    if speech_started:
        raise RuntimeError("Silero reported speech_started for digital silence")
    if transcript_seen:
        raise RuntimeError("digital silence reached STT")
    if rejected.get("reason") != "no_speech":
        raise RuntimeError(f"unexpected rejection: {rejected}")
    if not last_vad or last_vad.get("zero_fraction") != 1.0:
        raise RuntimeError(f"missing/invalid VAD telemetry: {last_vad}")
    print(json.dumps({
        "status": "PASS",
        "reason": rejected.get("reason"),
        "message": rejected.get("message"),
        "vad_probability": last_vad.get("probability"),
        "rms_dbfs": last_vad.get("rms_dbfs"),
        "zero_fraction": last_vad.get("zero_fraction"),
    }, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8765/ws")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--client-key-file", type=Path)
    parser.add_argument("--ca-certificate", type=Path)
    args = parser.parse_args()
    asyncio.run(verify(
        args.url,
        args.timeout,
        args.client_key_file.read_text(encoding="utf-8").strip()
            if args.client_key_file else None,
        args.ca_certificate,
    ))


if __name__ == "__main__":
    main()
