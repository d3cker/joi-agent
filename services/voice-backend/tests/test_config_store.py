from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from voice_agent.app import create_app
from voice_agent.config import Settings
from voice_agent.config_store import ConfigConflictError, ConfigDocumentError, ConfigStore
from voice_agent.storage import SQLiteSessionStore


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_retired_tts_settings_migrate_without_resetting_supported_values(tmp_path):
    store = new_store(tmp_path)
    doc = json.loads(store.settings_path.read_text())
    doc["tts"].update({"cfg_weight": 0, "model_path": "/old/weights", "temperature": 0.7})
    store.settings_path.write_text(json.dumps(doc))
    reopened = new_store(tmp_path)
    updated = reopened.public_snapshot()["settings"]
    assert updated["revision"] == doc["revision"] + 1
    assert updated["tts"]["temperature"] == 0.7
    assert "cfg_weight" not in updated["tts"] and "model_path" not in updated["tts"]
    assert new_store(tmp_path).public_snapshot()["settings"]["revision"] == updated["revision"]


def test_retired_provider_fails_explicitly(tmp_path):
    store = new_store(tmp_path)
    doc = json.loads(store.settings_path.read_text())
    doc["tts"]["provider"] = "chatterbox"
    store.settings_path.write_text(json.dumps(doc))
    with pytest.raises(ConfigDocumentError, match="retired"):
        new_store(tmp_path)


def test_installation_gpu_choice_remains_read_only_over_api(tmp_path):
    store = new_store(tmp_path, mock_settings(cuda_visible_devices="0"))
    assert store.schema()["locked"] == {"runtime.cuda_visible_devices": "0"}
    with pytest.raises(ConfigDocumentError, match="installation-only"):
        store.patch("settings", {"runtime": {"cuda_visible_devices": "2"}}, 1)


def mock_settings(**changes) -> Settings:
    values = {
        "llm_mode": "mock",
        "stt_mode": "mock",
        "vad_mode": "mock",
        "tts_mode": "mock",
        "session_db_path": ":memory:",
        "system_prompt_path": "prompts/default-system.md",
        "skills_directory": "skills",
        "open_terminal_api_key": "terminal-secret",
    }
    values.update(changes)
    return Settings(**values)


def new_store(tmp_path: Path, settings: Settings | None = None) -> ConfigStore:
    return ConfigStore.bootstrap(
        settings or mock_settings(),
        root=tmp_path / "config" / "joi",
        data_root=tmp_path / "data" / "joi",
        source_root=PROJECT_ROOT,
    )


def test_bootstrap_creates_standard_tree_and_redacts_secrets(tmp_path: Path):
    store = new_store(tmp_path)
    loaded = store.load_settings()

    assert store.root == (tmp_path / "config" / "joi").resolve()
    assert loaded.system_prompt_path == str(store.root / "prompts" / "default-system.md")
    assert loaded.skills_directory == str(store.root / "skills")
    assert loaded.skills_manifest_path == str(store.root / "skills" / "manifest.json")
    assert loaded.open_terminal_api_key == "terminal-secret"
    assert (store.root / "skills" / "web-research" / "SKILL.md").is_file()
    assert store.management_key()
    assert loaded.client_api_key

    snapshot = store.public_snapshot()
    encoded = json.dumps(snapshot)
    assert "terminal-secret" not in encoded
    assert snapshot["secrets"]["open_terminal_api_key"] == {"configured": True}
    assert snapshot["models"]["profiles"][0]["context_size"] == 128_000
    assert snapshot["models"]["profiles"][0]["reasoning_levels"] == [
        "none", "low", "high", "max"
    ]
    assert os.stat(store.settings_path).st_mode & 0o777 == 0o600
    assert os.stat(store.secrets_root / "open-terminal-api-key").st_mode & 0o777 == 0o600
    assert os.stat(store.secrets_root / "client-api-key").st_mode & 0o777 == 0o600


def test_bootstrap_uses_sqlite_backup_and_never_overwrites_existing_config(tmp_path: Path):
    source = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample(value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('preserved')")
    store = new_store(tmp_path, mock_settings(session_db_path=str(source)))
    migrated = Path(store.load_settings().session_db_path)
    with sqlite3.connect(migrated) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "preserved"

    original = store.settings_path.read_bytes()
    ConfigStore.bootstrap(
        mock_settings(port=9999),
        root=store.root,
        data_root=store.data_root,
        source_root=PROJECT_ROOT,
    )
    assert store.settings_path.read_bytes() == original
    assert store.load_settings().port == 8765


def test_existing_json_does_not_reparse_legacy_environment(
    monkeypatch, tmp_path: Path
):
    store = new_store(tmp_path)
    monkeypatch.setenv("VOICE_AGENT_OPEN_TERMINAL_API_KEY", "inline")
    monkeypatch.setenv(
        "VOICE_AGENT_OPEN_TERMINAL_API_KEY_FILE", str(tmp_path / "missing.key")
    )
    reopened = ConfigStore.bootstrap(
        root=store.root,
        data_root=store.data_root,
        source_root=PROJECT_ROOT,
    )
    assert reopened.load_settings().open_terminal_api_key == "terminal-secret"


def test_existing_schema_v1_config_without_voice_profiles_remains_readable(tmp_path: Path):
    store = new_store(tmp_path)
    document = json.loads(store.settings_path.read_text(encoding="utf-8"))
    del document["tts"]["voice_profiles_path"]
    store.settings_path.write_text(json.dumps(document), encoding="utf-8")

    assert store.load_settings().tts_voice_profiles_path is None


def test_bootstrap_adds_security_section_to_existing_schema_v1(tmp_path: Path):
    store = new_store(tmp_path)
    document = json.loads(store.settings_path.read_text(encoding="utf-8"))
    del document["security"]
    old_revision = document["revision"]
    store.settings_path.write_text(json.dumps(document), encoding="utf-8")

    reopened = ConfigStore.bootstrap(
        root=store.root, data_root=store.data_root, source_root=PROJECT_ROOT
    )
    migrated = reopened.public_snapshot()["settings"]
    assert migrated["revision"] == old_revision + 1
    assert migrated["security"] == {
        "tls_enabled": False,
        "server_ip": "",
        "certificate_path": "",
        "private_key_path": "",
    }


def test_patch_is_revisioned_validated_and_rollback_is_monotonic(tmp_path: Path):
    store = new_store(tmp_path)
    updated = store.patch("settings", {"tools": {"searxng_language": "en-US"}}, 1)
    assert updated["settings"]["revision"] == 2
    assert store.load_settings().searxng_language == "en-US"
    assert store.settings_path.with_name("settings.previous.json").is_file()

    with pytest.raises(ConfigConflictError, match="Stale"):
        store.patch("settings", {"tools": {"searxng_language": "pl-PL"}}, 1)
    with pytest.raises(ConfigDocumentError, match="Unknown tools fields"):
        store.patch("settings", {"tools": {"surprise": True}}, 2)
    with pytest.raises(ValueError, match="installation-only"):
        store.patch("settings", {"runtime": {"cuda_visible_devices": "0"}}, 2)

    rolled_back = store.rollback("settings")
    assert rolled_back["settings"]["revision"] == 3
    assert store.load_settings().searxng_language == "pl-PL"


def test_model_profiles_are_validated_and_selected(tmp_path: Path):
    store = new_store(tmp_path)
    profiles = [
        {
            "id": "other",
            "name": "Other local model",
            "base_url": "http://10.0.0.2:9000/v1",
            "model": "other-model",
            "context_size": 64_000,
            "reasoning_levels": ["none", "medium"],
            "default_reasoning": "medium",
        }
    ]
    store.patch(
        "models", {"active_profile": "other", "profiles": profiles}, 1
    )
    settings = store.load_settings()
    assert settings.llm_model == "other-model"
    assert settings.llm_context_size == 64_000
    assert settings.llm_reasoning_levels == ("none", "medium")

    with pytest.raises(ConfigDocumentError, match="default_reasoning"):
        invalid = [{**profiles[0], "default_reasoning": "max"}]
        store.patch("models", {"profiles": invalid}, 2)


def test_configuration_api_requires_key_and_never_returns_secret(tmp_path: Path):
    store = new_store(tmp_path)
    settings = store.load_settings()
    restart_requests: list[str] = []
    app = create_app(
        settings,
        config_store=store,
        session_store=SQLiteSessionStore(":memory:"),
        restart_scheduler=restart_requests.append,
    )
    headers = {"X-JOI-Config-Key": store.management_key()}
    with TestClient(app) as client:
        assert client.get("/config").status_code == 401
        response = client.get("/config", headers=headers)
        assert response.status_code == 200
        assert "terminal-secret" not in response.text
        assert response.json()["application"] == {
            "status": "applied",
            "changed_fields": [],
            "restart_fields": [],
            "controlled_restart": True,
            "instance_id": response.json()["application"]["instance_id"],
        }
        assert client.get("/config/schema", headers=headers).json()["locked"] == {
            "runtime.cuda_visible_devices": "2"
        }

        patched = client.patch(
            "/config",
            headers=headers,
            json={
                "document": "settings",
                "expected_revision": 1,
                "changes": {"agent": {"max_tool_steps": 12}},
            },
        )
        assert patched.status_code == 200
        assert patched.json()["settings"]["agent"]["max_tool_steps"] == 12
        applied = client.post("/config/reload", headers=headers)
        assert applied.status_code == 200
        assert applied.json()["status"] == "applied"

        changed_model = client.patch(
            "/config",
            headers=headers,
            json={
                "document": "models",
                "expected_revision": 1,
                "changes": {"profiles": [{
                    **store.public_snapshot()["models"]["profiles"][0],
                    "model": "next-model",
                }]},
            },
        )
        assert changed_model.status_code == 200
        reload_result = client.post("/config/reload", headers=headers).json()
        assert reload_result["status"] == "restart_required"
        assert "llm_model" in reload_result["restart_fields"]
        pending = client.get("/config", headers=headers).json()["application"]
        assert pending["status"] == "restart_required"
        assert "llm_model" in pending["restart_fields"]
        restarting = client.post("/config/restart", headers=headers)
        assert restarting.status_code == 200
        assert restarting.json()["status"] == "restarting"
        assert restart_requests == [restarting.json()["request_id"]]


def test_configuration_restart_requires_supervisor(tmp_path: Path):
    store = new_store(tmp_path)
    app = create_app(
        store.load_settings(),
        config_store=store,
        session_store=SQLiteSessionStore(":memory:"),
    )
    headers = {"X-JOI-Config-Key": store.management_key()}
    with TestClient(app) as client:
        profile = store.public_snapshot()["models"]["profiles"][0]
        client.patch(
            "/config",
            headers=headers,
            json={
                "document": "models",
                "expected_revision": 1,
                "changes": {"profiles": [{**profile, "model": "next-model"}]},
            },
        ).raise_for_status()
        response = client.post("/config/restart", headers=headers)
        assert response.status_code == 503
        assert "supervisor" in response.json()["detail"]


def test_stt_configuration_probe_checks_local_path_without_loading_model(tmp_path: Path):
    model = tmp_path / "whisper-model"
    model.mkdir()
    store = new_store(tmp_path, mock_settings(stt_model_path=str(model)))
    app = create_app(
        store.load_settings(),
        config_store=store,
        session_store=SQLiteSessionStore(":memory:"),
    )
    headers = {"X-JOI-Config-Key": store.management_key()}
    with TestClient(app) as client:
        payload = client.post("/config/test/stt", headers=headers).json()
        assert payload["status"] == "ok"
        assert payload["detail"]["path"] == str(model)
