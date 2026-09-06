import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voice_agent.app import create_app
from voice_agent.config import Settings
from voice_agent.storage import SQLiteSessionStore


def test_session_http_lifecycle_and_websocket_resume():
    store = SQLiteSessionStore(":memory:")
    settings = Settings(
        llm_mode="mock",
        stt_mode="mock",
        vad_mode="mock",
        tts_mode="mock",
        session_db_path=":memory:",
        system_prompt_path="prompts/default-system.md",
    )
    app = create_app(settings, session_store=store)

    with TestClient(app) as client:
        created = client.post("/sessions", json={"session_id": "session-api-1"})
        assert created.status_code == 201
        assert created.json()["id"] == "session-api-1"

        duplicate = client.post("/sessions", json={"session_id": "session-api-1"})
        assert duplicate.status_code == 409
        assert client.get("/sessions").json()["sessions"][0]["id"] == "session-api-1"

        renamed = client.patch(
            "/sessions/session-api-1", json={"title": "  Planning   session  "}
        )
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "Planning session"

        with client.websocket_connect(
            "/ws?session_id=session-api-1&conversation_language=en"
        ) as websocket:
            ready = websocket.receive_json()
            history = websocket.receive_json()
            assert ready["type"] == "session.ready"
            assert ready["session_id"] == "session-api-1"
            assert ready["resumed"] is True
            assert ready["conversation_language"] == "en"
            assert history == {
                "type": "session.history",
                "session_id": "session-api-1",
                "messages": [],
            }
            context = websocket.receive_json()
            assert context["type"] == "context.metrics"
            assert context["context_size"] == settings.llm_context_size
            assert sum(context["categories"].values()) == context["input_tokens"]
            assert websocket.receive_json()["state"] == "connected"
            assert websocket.receive_json()["state"] == "listening"
            websocket.send_json({"type": "ping"})
            assert websocket.receive_json() == {"type": "pong"}
            assert client.delete("/sessions/session-api-1").status_code == 409

        fetched = client.get("/sessions/session-api-1")
        assert fetched.status_code == 200
        assert fetched.json()["messages"] == []
        assert fetched.json()["tool_activity"] == []
        assert fetched.json()["conversation_language"] == "en"
        assert client.delete("/sessions/session-api-1").status_code == 204
        assert client.get("/sessions/session-api-1").status_code == 404


def test_session_rename_validation_and_missing_session():
    settings = Settings(
        llm_mode="mock", stt_mode="mock", vad_mode="mock", tts_mode="mock",
        system_prompt_path="prompts/default-system.md",
    )
    with TestClient(create_app(settings, session_store=SQLiteSessionStore(":memory:"))) as client:
        client.post("/sessions", json={"session_id": "rename-me"})
        assert client.patch("/sessions/rename-me", json={"title": ""}).status_code == 422
        assert client.patch("/sessions/rename-me", json={"title": 7}).status_code == 422
        assert client.patch("/sessions/missing", json={"title": "Title"}).status_code == 404


def test_invalid_websocket_session_id_is_rejected():
    settings = Settings(
        llm_mode="mock",
        stt_mode="mock",
        vad_mode="mock",
        tts_mode="mock",
        system_prompt_path="prompts/default-system.md",
    )
    with TestClient(create_app(settings, session_store=SQLiteSessionStore(":memory:"))) as client:
        with client.websocket_connect("/ws?session_id=not%20valid") as websocket:
            assert websocket.receive_json()["code"] == "invalid_session_id"


def test_invalid_websocket_conversation_language_is_rejected():
    settings = Settings(
        llm_mode="mock",
        stt_mode="mock",
        vad_mode="mock",
        tts_mode="mock",
        system_prompt_path="prompts/default-system.md",
    )
    with TestClient(
        create_app(settings, session_store=SQLiteSessionStore(":memory:"))
    ) as client:
        with client.websocket_connect(
            "/ws?session_id=valid&conversation_language=xx"
        ) as websocket:
            assert websocket.receive_json()["code"] == "invalid_conversation_language"


def test_tool_capabilities_endpoint_reports_remote_only_yolo_tools():
    settings = Settings(
        llm_mode="mock", stt_mode="mock", vad_mode="mock", tts_mode="mock",
        tool_mode="yolo", system_prompt_path="prompts/default-system.md",
    )
    with TestClient(create_app(settings, session_store=SQLiteSessionStore(":memory:"))) as client:
        response = client.get("/tools")
        assert response.status_code == 200
        payload = response.json()
        assert payload["mode"] == "yolo"
        assert payload["execution_boundary"] == "open_terminal"
        assert payload["web_search_provider"] == "searxng"
        assert set(payload["tools"]) >= {"execute", "read", "grep", "web_search", "web_fetch"}
        assert set(payload["tools"]) >= {"list_skills", "read_skill"}
        assert set(payload["tools"]) >= {
            "validate_skill", "create_skill", "update_skill", "reload_skills"
        }

        skills = client.get("/skills")
        assert skills.status_code == 200
        skill_payload = skills.json()
        assert skill_payload["loading"] == "lazy"
        assert skill_payload["content_tool"] == "read_skill"
        assert skill_payload["revision"] == 1
        assert skill_payload["manifest"].endswith("/skills/manifest.json")
        assert {item["slug"] for item in skill_payload["skills"]} >= {
            "web-research", "open-terminal-work", "skill-authoring"
        }
        health = client.get("/health").json()
        from voice_agent import __version__
        assert health["version"] == __version__
        assert isinstance(health["instance_id"], str)
        assert health["controlled_restart"] is False
        assert health["skills"] >= 4
        assert health["skills_revision"] == 1
        assert health["tts_streaming"] is True


def test_client_key_protects_sessions_tools_skills_and_websocket():
    settings = Settings(
        llm_mode="mock", stt_mode="mock", vad_mode="mock", tts_mode="mock",
        client_api_key="conversation-secret",
        system_prompt_path="prompts/default-system.md",
    )
    app = create_app(settings, session_store=SQLiteSessionStore(":memory:"))
    authorized = {"Authorization": "Bearer conversation-secret"}
    with TestClient(app) as client:
        for path in ("/sessions", "/tools", "/skills"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
            assert client.get(path, headers=authorized).status_code == 200
        assert client.post("/sessions", json={"session_id": "denied"}).status_code == 401
        assert client.post(
            "/sessions", json={"session_id": "allowed"}, headers=authorized
        ).status_code == 201
        with pytest.raises(WebSocketDisconnect) as denied:
            with client.websocket_connect("/ws?session_id=denied"):
                pass
        assert denied.value.code == 4401
        with client.websocket_connect(
            "/ws?session_id=allowed", headers=authorized
        ) as websocket:
            assert websocket.receive_json()["type"] == "session.ready"


def test_client_authorization_parser_rejects_non_bearer_forms():
    settings = Settings(
        llm_mode="mock", stt_mode="mock", vad_mode="mock", tts_mode="mock",
        client_api_key="conversation-secret",
        system_prompt_path="prompts/default-system.md",
    )
    with TestClient(
        create_app(settings, session_store=SQLiteSessionStore(":memory:"))
    ) as client:
        for value in ("conversation-secret", "Basic conversation-secret", "Bearer ", "bearer conversation-secret"):
            assert client.get("/sessions", headers={"Authorization": value}).status_code == 401
