from __future__ import annotations

import json
import hmac
import logging
import os
from pathlib import Path
import re
import signal
import threading
import time
from collections.abc import Callable
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
import httpx

from .ack import load_cached_acknowledgement
from .config import Settings, validate_settings
from .config_store import ConfigConflictError, ConfigDocumentError, ConfigStore
from .endpointing import EndpointingConfig
from .llm import MockLLM, OpenAICompatibleLLM
from .languages import conversation_language
from .prompts import load_system_prompt
from .protocol import ProtocolError, event, parse_client_command
from .remote_tools import build_tool_registry
from .session import ConversationSession
from .skills import SkillCatalogManager, SkillTools, compose_system_prompt
from .stt import FasterWhisperSTT, FasterWhisperVAD, MockSTT, MockVAD
from .storage import SQLiteSessionStore, public_messages
from .tools import ToolExecutor, ToolRegistry
from .tts import (
    MockTTS,
    OpenAICompatibleSpeechTTS,
    load_voice_reference_profiles,
)
from . import __version__

log = logging.getLogger("uvicorn.error").getChild("voice_agent.app")
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_PROCESS_RESTART_FIELDS = {
    "host",
    "port",
    "session_db_path",
    "cuda_visible_devices",
    "tls_enabled",
    "tls_server_ip",
    "tls_certificate_path",
    "tls_private_key_path",
}


def _restart_fields(changed: tuple[str, ...]) -> list[str]:
    component = {
        name for name in changed
        if name.startswith(("llm_", "stt_", "vad_", "tts_", "ack_"))
    }
    return sorted(set(changed) & _PROCESS_RESTART_FIELDS | component)


def supervised_restart_scheduler() -> Callable[[str], None] | None:
    """Return a delayed SIGTERM scheduler only under the JOI supervisor."""
    if os.getenv("VOICE_AGENT_SUPERVISED") != "1":
        return None

    def schedule(request_id: str) -> None:
        def terminate() -> None:
            time.sleep(0.75)  # allow the HTTP response to reach the macOS client
            log.warning("controlled_restart request_id=%s pid=%d", request_id, os.getpid())
            os.kill(os.getpid(), signal.SIGTERM)

        threading.Thread(
            target=terminate,
            name=f"voice-agent-restart-{request_id[:8]}",
            daemon=True,
        ).start()

    return schedule


def _components(settings: Settings):
    llm = MockLLM() if settings.llm_mode == "mock" else OpenAICompatibleLLM(
        settings.llm_base_url, settings.llm_model, settings.llm_api_key
    )
    stt = MockSTT() if settings.stt_mode == "mock" else FasterWhisperSTT(
        settings.stt_model_path,
        settings.stt_device,
        settings.stt_device_index,
        settings.stt_compute_type,
        no_speech_threshold=settings.stt_no_speech_threshold,
        log_probability_threshold=settings.stt_log_probability_threshold,
        compression_ratio_threshold=settings.stt_compression_ratio_threshold,
    )
    if settings.tts_mode == "mock":
        tts = MockTTS()
    elif settings.tts_provider == "openai_http":
        tts = OpenAICompatibleSpeechTTS(
            settings.tts_http_base_url,
            settings.tts_http_model,
            api_key=settings.tts_http_api_key,
            reference_audio_path=settings.tts_voice_path,
            reference_text=settings.tts_reference_text,
            reference_profiles=load_voice_reference_profiles(
                settings.tts_voice_profiles_path
            ),
            temperature=settings.tts_temperature,
            top_k=settings.tts_http_top_k,
            max_new_tokens=settings.tts_http_max_new_tokens,
            timeout_seconds=settings.tts_http_timeout_seconds,
        )
    else:
        raise ValueError(f"Unsupported TTS provider: {settings.tts_provider}")
    return llm, stt, tts


def _new_vad(settings: Settings):
    # Rolling audio is session state. The ONNX model itself remains shared by
    # faster-whisper's process-wide cache, so this does not duplicate weights.
    return MockVAD() if settings.vad_mode == "mock" else FasterWhisperVAD()


def create_app(
    settings: Settings | None = None,
    *,
    session_store: SQLiteSessionStore | None = None,
    tool_executor: ToolExecutor | None = None,
    config_store: ConfigStore | None = None,
    restart_scheduler: Callable[[str], None] | None = None,
) -> FastAPI:
    if settings is None:
        config_store = config_store or ConfigStore.bootstrap()
        settings = config_store.load_settings()
    validate_settings(settings)
    # Speech libraries are lazy-imported later, so mask the selected GPU before
    # CUDA initializes; inside this process it is addressed as logical GPU 0.
    # The dedicated speech process must never fall back to the GPUs occupied by
    # the existing vLLM service. Persistent settings select one physical GPU
    # and intentionally override any inherited CUDA mask (default: physical 2).
    os.environ["CUDA_VISIBLE_DEVICES"] = settings.cuda_visible_devices
    app = FastAPI(title="Local Voice Agent", version=__version__)
    instance_id = uuid4().hex
    restart_scheduled = False
    # One lazy-loaded set per process avoids duplicating several GB of model
    # weights for every connected WebSocket session.
    llm, stt, tts = _components(settings)
    skill_manager = SkillCatalogManager(
        settings.skills_directory, settings.skills_manifest_path
    )
    base_system_prompt = load_system_prompt(
        settings.system_prompt_path, settings.system_prompt
    )
    current_system_prompt = lambda: compose_system_prompt(
        base_system_prompt, skill_manager.snapshot()
    )
    sessions = session_store or SQLiteSessionStore(settings.session_db_path)
    app.state.session_store = sessions
    app.state.tool_executor = tool_executor
    app.state.skill_catalog_manager = skill_manager
    app.state.config_store = config_store
    app.state.settings = settings
    active_session_ids: set[str] = set()
    acknowledgement = load_cached_acknowledgement(settings.ack_audio_path)
    if acknowledgement is None:
        log.warning("cached_ack status=disabled_missing path=%s", settings.ack_audio_path)
    else:
        log.info(
            "cached_ack status=ready path=%s bytes=%d duration=%.3f",
            acknowledgement.path,
            len(acknowledgement.data),
            acknowledgement.duration_seconds,
        )

    def has_client_access(authorization: str | None) -> bool:
        expected = settings.client_api_key
        if expected is None:
            # Development/mock apps remain usable without provisioning. A LAN
            # listener is rejected separately at process startup.
            return True
        if authorization is None or not authorization.startswith("Bearer "):
            return False
        supplied = authorization[len("Bearer "):]
        return bool(supplied) and hmac.compare_digest(supplied, expected)

    def require_client_access(
        authorization: str | None = Header(default=None)
    ) -> None:
        if not has_client_access(authorization):
            raise HTTPException(status_code=401, detail="Invalid client access key")

    @app.get("/health")
    async def health() -> dict[str, object]:
        voice_profiles = sorted(
            getattr(tts, "reference_profiles", {}).keys()
        )
        return {
            "status": "ok",
            "version": __version__,
            "instance_id": instance_id,
            "controlled_restart": restart_scheduler is not None,
            "cached_ack": "ready" if acknowledgement is not None else "missing",
            "tts_provider": settings.tts_provider,
            "tts_voice_profiles": voice_profiles,
            "sessions": "sqlite",
            "tool_mode": settings.tool_mode,
            "skills": len(skill_manager.snapshot().list()),
            "skills_revision": skill_manager.revision,
            "tts_streaming": not settings.tts_defer_until_llm_done,
            "transport": "tls" if settings.tls_enabled else "cleartext-loopback",
            "client_auth": settings.client_api_key is not None,
        }

    def config_snapshot() -> dict[str, object]:
        assert config_store is not None
        snapshot = config_store.public_snapshot()
        changed = config_store.changed_fields(settings)
        restart_fields = _restart_fields(changed)
        snapshot["application"] = {
            "status": "restart_required" if restart_fields else (
                "reload_available" if changed else "applied"
            ),
            "changed_fields": list(changed),
            "restart_fields": restart_fields,
            "controlled_restart": restart_scheduler is not None,
            "instance_id": instance_id,
        }
        return snapshot

    def session_tools(session_id: str) -> ToolExecutor:
        if tool_executor is None:
            return build_tool_registry(settings, session_id, skill_manager)
        registry = ToolRegistry()
        registry.extend(SkillTools(skill_manager))
        registry.extend(tool_executor)
        return registry

    @app.get("/tools", dependencies=[Depends(require_client_access)])
    async def list_tools() -> dict[str, object]:
        registry = session_tools("capability-probe")
        return {
            "mode": settings.tool_mode,
            "execution_boundary": "open_terminal",
            "web_search_provider": "searxng",
            "tools": [definition.name for definition in registry.definitions()],
        }

    def require_config_access(
        x_joi_config_key: str | None = Header(default=None)
    ) -> None:
        if config_store is None:
            raise HTTPException(status_code=404, detail="Configuration API is disabled")
        if x_joi_config_key is None or not hmac.compare_digest(
            x_joi_config_key, config_store.management_key()
        ):
            raise HTTPException(status_code=401, detail="Invalid configuration key")

    @app.get("/config", dependencies=[Depends(require_config_access)])
    async def get_config() -> dict[str, object]:
        return config_snapshot()

    @app.get("/config/schema", dependencies=[Depends(require_config_access)])
    async def get_config_schema() -> dict[str, object]:
        assert config_store is not None
        return config_store.schema()

    @app.patch("/config", dependencies=[Depends(require_config_access)])
    async def patch_config(payload: dict[str, object]) -> dict[str, object]:
        assert config_store is not None
        document = payload.get("document")
        changes = payload.get("changes")
        expected_revision = payload.get("expected_revision")
        if not isinstance(document, str) or not isinstance(changes, dict):
            raise HTTPException(
                status_code=422,
                detail="document and object-valued changes are required",
            )
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            raise HTTPException(status_code=422, detail="expected_revision is required")
        try:
            config_store.patch(document, changes, expected_revision)
            return config_snapshot()
        except ConfigConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (ConfigDocumentError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/config/secrets/{name}", dependencies=[Depends(require_config_access)])
    async def set_config_secret(
        name: str, payload: dict[str, object]
    ) -> dict[str, object]:
        assert config_store is not None
        value = payload.get("value")
        if value is not None and not isinstance(value, str):
            raise HTTPException(status_code=422, detail="value must be a string or null")
        try:
            config_store.set_secret(name, value)
            return config_snapshot()
        except ConfigDocumentError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/config/rollback", dependencies=[Depends(require_config_access)])
    async def rollback_config(payload: dict[str, object]) -> dict[str, object]:
        assert config_store is not None
        document = payload.get("document")
        if not isinstance(document, str):
            raise HTTPException(status_code=422, detail="document is required")
        try:
            config_store.rollback(document)
            return config_snapshot()
        except ConfigDocumentError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/config/reload", dependencies=[Depends(require_config_access)])
    async def reload_config() -> dict[str, object]:
        nonlocal settings, skill_manager, base_system_prompt
        assert config_store is not None
        changed = config_store.changed_fields(settings)
        blocked = _restart_fields(changed)
        if blocked:
            return {
                "status": "restart_required",
                "changed_fields": list(changed),
                "restart_fields": blocked,
            }
        candidate = config_store.load_settings()
        candidate_skill_manager = SkillCatalogManager(
            candidate.skills_directory, candidate.skills_manifest_path
        )
        candidate_prompt = load_system_prompt(
            candidate.system_prompt_path, candidate.system_prompt
        )
        settings = candidate
        skill_manager = candidate_skill_manager
        base_system_prompt = candidate_prompt
        app.state.settings = settings
        app.state.skill_catalog_manager = skill_manager
        return {
            "status": "applied",
            "changed_fields": list(changed),
            "skills_revision": skill_manager.revision,
        }

    @app.post("/config/restart", dependencies=[Depends(require_config_access)])
    async def restart_configured_backend() -> dict[str, object]:
        nonlocal restart_scheduled
        assert config_store is not None
        changed = config_store.changed_fields(settings)
        blocked = _restart_fields(changed)
        if not blocked:
            return {
                "status": "not_required",
                "instance_id": instance_id,
                "changed_fields": list(changed),
            }
        if restart_scheduler is None:
            raise HTTPException(
                status_code=503,
                detail="Controlled restart is unavailable; start the backend supervisor",
            )
        if active_session_ids:
            raise HTTPException(
                status_code=409,
                detail="Stop active conversations before restarting the backend",
            )
        if restart_scheduled:
            raise HTTPException(status_code=409, detail="Backend restart is already scheduled")
        request_id = uuid4().hex
        restart_scheduled = True
        restart_scheduler(request_id)
        return {
            "status": "restarting",
            "request_id": request_id,
            "instance_id": instance_id,
            "restart_fields": blocked,
        }

    @app.post("/config/test/{target}", dependencies=[Depends(require_config_access)])
    async def test_config_target(target: str) -> dict[str, object]:
        assert config_store is not None
        try:
            return await _test_config_target(target, config_store.load_settings())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (OSError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc


    @app.get("/skills", dependencies=[Depends(require_client_access)])
    async def list_skills() -> dict[str, object]:
        return {
            "skills": [skill.summary() for skill in skill_manager.snapshot().list()],
            "revision": skill_manager.revision,
            "manifest": str(skill_manager.manifest_path),
            "loading": "lazy",
            "content_tool": "read_skill",
        }

    @app.get("/sessions", dependencies=[Depends(require_client_access)])
    async def list_sessions(limit: int = 100) -> dict[str, object]:
        return {
            "sessions": [
                {
                    "id": item.id,
                    "title": item.title,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                    "reasoning_effort": item.reasoning_effort.value,
                    "conversation_language": item.conversation_language,
                    "message_count": item.message_count,
                    "last_message": item.last_message,
                }
                for item in sessions.list_sessions(limit)
            ]
        }

    @app.post(
        "/sessions", status_code=201, dependencies=[Depends(require_client_access)]
    )
    async def create_session(payload: dict[str, object] | None = None) -> dict[str, object]:
        requested = (payload or {}).get("session_id")
        if requested is not None and (
            not isinstance(requested, str) or not _SESSION_ID.fullmatch(requested)
        ):
            raise HTTPException(status_code=422, detail="Invalid session_id")
        opened = sessions.open(requested, current_system_prompt())
        if opened.resumed:
            raise HTTPException(status_code=409, detail="Session already exists")
        return {
            "id": opened.session.id,
            "created_at": opened.session.created_at,
            "reasoning_effort": opened.session.reasoning_effort.value,
            "conversation_language": opened.session.conversation_language,
        }

    @app.get(
        "/sessions/{session_id}", dependencies=[Depends(require_client_access)]
    )
    async def get_session(session_id: str) -> dict[str, object]:
        opened = sessions.get(session_id)
        if opened is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return {
            "id": opened.session.id,
            "title": opened.session.title,
            "created_at": opened.session.created_at,
            "updated_at": opened.session.updated_at,
            "reasoning_effort": opened.session.reasoning_effort.value,
            "conversation_language": opened.session.conversation_language,
            "messages": public_messages(opened.messages),
            "tool_activity": [
                {
                    "call_id": item.call_id,
                    "response_id": item.response_id,
                    "name": item.name,
                    "arguments": item.arguments,
                    "step": item.step,
                    "status": item.status,
                    "started_at": item.started_at,
                    "finished_at": item.finished_at,
                    "error": item.error,
                }
                for item in sessions.list_tool_activity(session_id)
            ],
        }

    @app.patch(
        "/sessions/{session_id}", dependencies=[Depends(require_client_access)]
    )
    async def rename_session(
        session_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        raw_title = payload.get("title")
        if not isinstance(raw_title, str):
            raise HTTPException(status_code=422, detail="title must be a string")
        title = " ".join(raw_title.split())
        if not title or len(title) > 120:
            raise HTTPException(
                status_code=422, detail="title must contain 1 to 120 characters"
            )
        updated = sessions.rename(session_id, title)
        if updated is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return {
            "id": updated.id,
            "title": updated.title,
            "created_at": updated.created_at,
            "updated_at": updated.updated_at,
            "reasoning_effort": updated.reasoning_effort.value,
            "conversation_language": updated.conversation_language,
            "message_count": updated.message_count,
        }

    @app.delete(
        "/sessions/{session_id}",
        status_code=204,
        dependencies=[Depends(require_client_access)],
    )
    async def delete_session(session_id: str) -> None:
        if session_id in active_session_ids:
            raise HTTPException(status_code=409, detail="Session is currently connected")
        if not sessions.delete(session_id):
            raise HTTPException(status_code=404, detail="Session not found")

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        if not has_client_access(websocket.headers.get("authorization")):
            client = websocket.client
            client_label = (
                f"{client.host}:{client.port}" if client is not None else "unknown"
            )
            log.warning("websocket_auth_rejected client=%s", client_label)
            await websocket.close(code=4401, reason="Unauthorized")
            return
        await websocket.accept()
        requested_session_id = websocket.query_params.get("session_id")
        requested_language = websocket.query_params.get("conversation_language")
        if requested_session_id is not None and not _SESSION_ID.fullmatch(
            requested_session_id
        ):
            await websocket.send_json(event(
                "error", code="invalid_session_id", message="Invalid session_id"
            ))
            await websocket.close(code=1008)
            return
        if requested_language is not None:
            try:
                requested_language = conversation_language(requested_language).code
            except ValueError as exc:
                await websocket.send_json(event(
                    "error", code="invalid_conversation_language", message=str(exc)
                ))
                await websocket.close(code=1008)
                return
        opened = sessions.open(requested_session_id, current_system_prompt())
        session_id = opened.session.id
        if requested_language is not None:
            sessions.update_conversation_language(session_id, requested_language)
        if session_id in active_session_ids:
            await websocket.send_json(event(
                "error",
                code="session_in_use",
                message="Session is already connected",
            ))
            await websocket.close(code=1008)
            return
        active_session_ids.add(session_id)
        client = websocket.client
        client_label = (
            f"{client.host}:{client.port}" if client is not None else "unknown"
        )
        disconnect_code = None
        disconnect_reason = None
        log.info("websocket_connected session_id=%s client=%s", session_id, client_label)
        session = ConversationSession(
            transport=websocket,
            llm=llm,
            stt=stt,
            vad=_new_vad(settings),
            tts=tts,
            system_prompt=current_system_prompt(),
            system_prompt_provider=current_system_prompt,
            context_components_provider=lambda: (
                base_system_prompt,
                skill_manager.snapshot().prompt_index(),
            ),
            context_size=settings.llm_context_size,
            session_id=session_id,
            resumed=opened.resumed,
            session_store=sessions,
            tool_executor=session_tools(session_id),
            max_tool_steps=settings.agent_max_tool_steps,
            messages=list(opened.messages),
            reasoning_effort=opened.session.reasoning_effort,
            conversation_language=(
                requested_language or opened.session.conversation_language
            ),
            max_audio_bytes=settings.max_audio_seconds * 16_000 * 2,
            tts_queue_max_segments=settings.tts_queue_max_segments,
            tts_segment_soft_limit=settings.tts_segment_soft_limit,
            tts_segment_hard_limit=settings.tts_segment_hard_limit,
            tts_segment_max_words=settings.tts_segment_max_words,
            tts_defer_until_llm_done=settings.tts_defer_until_llm_done,
            acknowledgement=acknowledgement,
            ack_min_characters=settings.ack_min_characters,
            ack_min_words=settings.ack_min_words,
            endpointing_config=EndpointingConfig(
                start_threshold=settings.vad_start_threshold,
                end_threshold=settings.vad_end_threshold,
                min_speech_ms=settings.vad_min_speech_ms,
                prefix_padding_ms=settings.vad_prefix_padding_ms,
                silence_ms=settings.vad_silence_ms,
                suffix_padding_ms=settings.vad_suffix_padding_ms,
                barge_in_threshold=settings.barge_in_threshold,
                barge_in_min_speech_ms=settings.barge_in_min_speech_ms,
                barge_in_prefix_ms=settings.barge_in_prefix_ms,
                min_rms_dbfs=settings.vad_min_rms_dbfs,
                max_audio_ms=settings.max_audio_seconds * 1_000,
            ),
        )
        await session.start()
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    disconnect_code = message.get("code")
                    disconnect_reason = message.get("reason")
                    break
                if message.get("bytes") is not None:
                    await session.append_audio(message["bytes"])
                    continue
                text = message.get("text")
                if text is None:
                    continue
                try:
                    command = parse_client_command(json.loads(text))
                    await session.handle_command(command)
                except (json.JSONDecodeError, ProtocolError) as exc:
                    await websocket.send_json(event("error", code="invalid_message", message=str(exc)))
        except WebSocketDisconnect as exc:
            disconnect_code = getattr(exc, "code", None)
            disconnect_reason = getattr(exc, "reason", None)
        except Exception:
            log.exception("WebSocket session failed")
        finally:
            await session.close()
            active_session_ids.discard(session_id)
            log.info(
                "websocket_disconnected session_id=%s client=%s code=%s reason=%s",
                session_id,
                client_label,
                disconnect_code,
                disconnect_reason,
            )

    return app


async def _test_config_target(target: str, settings: Settings) -> dict[str, object]:
    started = time.monotonic()
    if target == "stt":
        path = Path(settings.stt_model_path).expanduser()
        if not path.is_dir():
            raise ValueError(f"STT model directory does not exist: {path}")
        detail: dict[str, object] = {"path": str(path), "readable": os.access(path, os.R_OK)}
    elif target == "llm":
        headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(f"{settings.llm_base_url.rstrip('/')}/models", headers=headers)
            response.raise_for_status()
        detail = {"status_code": response.status_code, "model": settings.llm_model}
    elif target == "tts":
        if settings.tts_provider == "openai_http":
            base = settings.tts_http_base_url.rstrip("/")
            root = base[:-3] if base.endswith("/v1") else base
            headers = {"Authorization": f"Bearer {settings.tts_http_api_key}"} if settings.tts_http_api_key else {}
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.get(f"{root}/health", headers=headers)
                response.raise_for_status()
            profiles = load_voice_reference_profiles(settings.tts_voice_profiles_path)
            detail = {
                "status_code": response.status_code,
                "provider": "openai_http",
                "voice_profiles": sorted(profiles),
            }
        else:
            raise ValueError("Unsupported TTS provider")
    elif target == "openterminal":
        headers = {"X-Session-Id": "config-probe"}
        if settings.open_terminal_api_key:
            headers["Authorization"] = f"Bearer {settings.open_terminal_api_key}"
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(
                f"{settings.open_terminal_base_url.rstrip('/')}/health", headers=headers
            )
            response.raise_for_status()
        detail = {"status_code": response.status_code}
    elif target == "searxng":
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(f"{settings.searxng_base_url.rstrip('/')}/config")
            response.raise_for_status()
        detail = {"status_code": response.status_code}
    else:
        raise ValueError("target must be llm, stt, tts, openterminal, or searxng")
    return {
        "status": "ok",
        "target": target,
        "latency_ms": round((time.monotonic() - started) * 1_000),
        "detail": detail,
    }


def create_default_app() -> FastAPI:
    if os.getenv("JOI_CONFIG_DISABLED") == "1":
        return create_app(Settings.from_env())
    store = ConfigStore.bootstrap()
    return create_app(
        store.load_settings(),
        config_store=store,
        restart_scheduler=supervised_restart_scheduler(),
    )
