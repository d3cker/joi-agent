from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
from threading import RLock
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from .config import Settings, validate_settings


SCHEMA_VERSION = 1
_RETIRED_TTS_FIELDS = {"model_path","device","cfg_weight","repetition_penalty","pronunciations_path","trim_threshold_dbfs","trim_minimum_quiet_ms","trim_keep_quiet_ms","trim_fade_ms"}
_PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SECRET_FIELDS = {
    "client_api_key": "client-api-key",
    "llm_api_key": "llm-api-key",
    "tts_http_api_key": "tts-api-key",
    "open_terminal_api_key": "open-terminal-api-key",
}
_MANAGEMENT_KEY_FILE = "config-api-key"
_MODEL_FIELDS = {
    "llm_base_url",
    "llm_model",
    "llm_context_size",
    "llm_reasoning_levels",
    "llm_default_reasoning",
}
_UNPERSISTED_FIELDS = set(_SECRET_FIELDS) | _MODEL_FIELDS | {"system_prompt"}

_SECTION_FIELDS: dict[str, dict[str, str]] = {
    "server": {
        "host": "host",
        "port": "port",
        "max_audio_seconds": "max_audio_seconds",
    },
    "security": {
        "tls_enabled": "tls_enabled",
        "server_ip": "tls_server_ip",
        "certificate_path": "tls_certificate_path",
        "private_key_path": "tls_private_key_path",
    },
    "llm": {"mode": "llm_mode"},
    "stt": {
        "mode": "stt_mode",
        "model_path": "stt_model_path",
        "device": "stt_device",
        "device_index": "stt_device_index",
        "compute_type": "stt_compute_type",
        "no_speech_threshold": "stt_no_speech_threshold",
        "log_probability_threshold": "stt_log_probability_threshold",
        "compression_ratio_threshold": "stt_compression_ratio_threshold",
    },
    "vad": {
        "mode": "vad_mode",
        "start_threshold": "vad_start_threshold",
        "end_threshold": "vad_end_threshold",
        "min_speech_ms": "vad_min_speech_ms",
        "prefix_padding_ms": "vad_prefix_padding_ms",
        "silence_ms": "vad_silence_ms",
        "suffix_padding_ms": "vad_suffix_padding_ms",
        "barge_in_threshold": "barge_in_threshold",
        "barge_in_min_speech_ms": "barge_in_min_speech_ms",
        "barge_in_prefix_ms": "barge_in_prefix_ms",
        "min_rms_dbfs": "vad_min_rms_dbfs",
    },
    "tts": {
        "mode": "tts_mode",
        "provider": "tts_provider",
        "voice_path": "tts_voice_path",
        "queue_max_segments": "tts_queue_max_segments",
        "segment_soft_limit": "tts_segment_soft_limit",
        "segment_hard_limit": "tts_segment_hard_limit",
        "segment_max_words": "tts_segment_max_words",
        "defer_until_llm_done": "tts_defer_until_llm_done",
        "temperature": "tts_temperature",
        "http_base_url": "tts_http_base_url",
        "http_model": "tts_http_model",
        "reference_text": "tts_reference_text",
        "voice_profiles_path": "tts_voice_profiles_path",
        "http_top_k": "tts_http_top_k",
        "http_max_new_tokens": "tts_http_max_new_tokens",
        "http_timeout_seconds": "tts_http_timeout_seconds",
    },
    "acknowledgement": {
        "audio_path": "ack_audio_path",
        "min_characters": "ack_min_characters",
        "min_words": "ack_min_words",
    },
    "agent": {
        "system_prompt_path": "system_prompt_path",
        "skills_directory": "skills_directory",
        "skills_manifest_path": "skills_manifest_path",
        "session_db_path": "session_db_path",
        "max_tool_steps": "agent_max_tool_steps",
    },
    "tools": {
        "mode": "tool_mode",
        "open_terminal_base_url": "open_terminal_base_url",
        "searxng_base_url": "searxng_base_url",
        "searxng_language": "searxng_language",
        "web_fetch_user_agent": "web_fetch_user_agent",
    },
    "runtime": {"cuda_visible_devices": "cuda_visible_devices"},
}

_ALL_DOCUMENT_FIELDS = {
    field_name for section in _SECTION_FIELDS.values() for field_name in section.values()
}
_KNOWN_SETTINGS_FIELDS = {item.name for item in fields(Settings)}
if _ALL_DOCUMENT_FIELDS | _UNPERSISTED_FIELDS != _KNOWN_SETTINGS_FIELDS:
    missing = _KNOWN_SETTINGS_FIELDS - (_ALL_DOCUMENT_FIELDS | _UNPERSISTED_FIELDS)
    extra = (_ALL_DOCUMENT_FIELDS | _UNPERSISTED_FIELDS) - _KNOWN_SETTINGS_FIELDS
    raise RuntimeError(f"Invalid configuration field map; missing={missing}, extra={extra}")


class ConfigError(ValueError):
    pass


class ConfigConflictError(ConfigError):
    pass


class ConfigDocumentError(ConfigError):
    pass


class ConfigStore:
    """Versioned, atomically-written configuration rooted at ~/.config/joi."""

    def __init__(self, root: Path, data_root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.data_root = data_root.expanduser().resolve()
        self.backend_root = self.root / "backend"
        self.settings_path = self.backend_root / "settings.json"
        self.models_path = self.backend_root / "models.json"
        self.secrets_root = self.backend_root / "secrets"
        self.prompts_root = self.root / "prompts"
        self.skills_root = self.root / "skills"
        self._lock = RLock()

    @classmethod
    def discover(cls) -> "ConfigStore":
        config_override = os.getenv("JOI_CONFIG_HOME")
        if config_override:
            config_root = Path(config_override)
        else:
            config_base = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
            config_root = config_base / "joi"
        data_override = os.getenv("JOI_DATA_HOME")
        if data_override:
            data_root = Path(data_override)
        else:
            data_base = Path(
                os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share")
            )
            data_root = data_base / "joi"
        return cls(config_root, data_root)

    @classmethod
    def bootstrap(
        cls,
        bootstrap_settings: Settings | None = None,
        *,
        root: Path | None = None,
        data_root: Path | None = None,
        source_root: Path | None = None,
    ) -> "ConfigStore":
        discovered = cls.discover()
        store = cls(root or discovered.root, data_root or discovered.data_root)
        store._create_directories()
        if store.settings_path.exists() and store.models_path.exists():
            store._ensure_management_key()
            store._ensure_client_key()
            store._ensure_additive_settings()
            store.load_settings()
            return store
        imported = bootstrap_settings or Settings.from_env()
        if not store.settings_path.exists() or not store.models_path.exists():
            store._initialize(imported, source_root=source_root)
        store._ensure_management_key()
        store._ensure_client_key()
        # Loading here makes startup fail before models are touched when files
        # were edited manually into an invalid state.
        store.load_settings()
        return store

    def _initialize(self, imported: Settings, *, source_root: Path | None) -> None:
        validate_settings(imported)
        source_root = (source_root or Path(__file__).resolve().parent.parent).resolve()
        self._create_directories()
        migrated = self._migrate_files(imported, source_root)
        settings_doc = _settings_document(migrated, revision=1)
        models_doc = _models_document(imported, revision=1)
        with self._lock:
            if not self.settings_path.exists():
                _atomic_json_write(self.settings_path, settings_doc, backup=False)
            if not self.models_path.exists():
                _atomic_json_write(self.models_path, models_doc, backup=False)
            self._write_secrets(imported)
            self._ensure_management_key()
            self._ensure_client_key()
            receipt = self.backend_root / "migration.json"
            if not receipt.exists():
                _atomic_json_write(
                    receipt,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "created_at": datetime.now(UTC).isoformat(),
                        "source": "environment-and-release-files",
                        "secrets_imported": {
                            key: getattr(imported, key) is not None
                            for key in _SECRET_FIELDS
                        },
                    },
                    backup=False,
                )

    def _create_directories(self) -> None:
        for path in (
            self.root,
            self.backend_root,
            self.secrets_root,
            self.prompts_root,
            self.skills_root,
            self.data_root,
        ):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _migrate_files(self, imported: Settings, source_root: Path) -> Settings:
        prompt_target = self.prompts_root / "default-system.md"
        if not prompt_target.exists():
            if imported.system_prompt and imported.system_prompt.strip():
                _atomic_write(prompt_target, imported.system_prompt.strip().encode() + b"\n", 0o640)
            else:
                prompt_source = _resolve_source(imported.system_prompt_path, source_root)
                if not prompt_source.is_file():
                    raise FileNotFoundError(f"System prompt not found: {prompt_source}")
                _atomic_write(prompt_target, prompt_source.read_bytes(), 0o640)

        skill_source = _resolve_source(imported.skills_directory, source_root)
        if skill_source != self.skills_root:
            _copy_tree_missing(skill_source, self.skills_root)
        manifest = self.skills_root / "manifest.json"
        if not manifest.is_file():
            raise FileNotFoundError(f"Skills manifest not found after migration: {manifest}")

        session_target = self.data_root / "sessions.sqlite3"
        if imported.session_db_path == ":memory:":
            session_path = ":memory:"
        else:
            session_source = _resolve_source(imported.session_db_path, source_root)
            if not session_target.exists() and session_source.is_file():
                _sqlite_backup(session_source, session_target)
            session_path = str(session_target)

        return replace(
            imported,
            system_prompt=None,
            system_prompt_path=str(prompt_target),
            skills_directory=str(self.skills_root),
            skills_manifest_path=str(manifest),
            session_db_path=session_path,
        )

    def _write_secrets(self, settings: Settings) -> None:
        for field_name, file_name in _SECRET_FIELDS.items():
            target = self.secrets_root / file_name
            value = getattr(settings, field_name)
            if value and not target.exists():
                _atomic_write(target, (value.strip() + "\n").encode(), 0o600)

    def _ensure_management_key(self) -> None:
        target = self.secrets_root / _MANAGEMENT_KEY_FILE
        if not target.exists():
            _atomic_write(target, (secrets.token_urlsafe(32) + "\n").encode(), 0o600)

    def _ensure_client_key(self) -> None:
        target = self.secrets_root / _SECRET_FIELDS["client_api_key"]
        if not target.exists():
            _atomic_write(target, (secrets.token_urlsafe(32) + "\n").encode(), 0o600)

    def _ensure_additive_settings(self) -> None:
        """Persist new optional sections/fields without invalidating schema-v1 installs."""
        with self._lock:
            document = _read_document(self.settings_path, "settings")
            changed = False
            tts = document.get("tts", {})
            if tts.get("provider") == "chatterbox":
                raise ConfigDocumentError("Chatterbox was retired; select openai_http before upgrading")
            for key in _RETIRED_TTS_FIELDS & set(tts):
                del tts[key]
                changed = True
            defaults = Settings()
            for section_name, mapping in _SECTION_FIELDS.items():
                section = document.get(section_name)
                if section is None:
                    document[section_name] = {
                        public_name: getattr(defaults, field_name)
                        for public_name, field_name in mapping.items()
                    }
                    changed = True
                    continue
                if not isinstance(section, dict):
                    continue
                for public_name, field_name in mapping.items():
                    if public_name not in section:
                        section[public_name] = getattr(defaults, field_name)
                        changed = True
            if changed:
                document["revision"] = int(document.get("revision", 0)) + 1
                _atomic_json_write(self.settings_path, document, backup=True)

    def management_key(self) -> str:
        self._ensure_management_key()
        return (self.secrets_root / _MANAGEMENT_KEY_FILE).read_text(
            encoding="utf-8"
        ).strip()

    def load_documents(self) -> tuple[dict[str, Any], dict[str, Any]]:
        with self._lock:
            settings_doc = _read_document(self.settings_path, "settings")
            models_doc = _read_document(self.models_path, "models")
            _validate_models_document(models_doc)
            # Also validate the combination rather than the files separately.
            self._settings_from_documents(settings_doc, models_doc)
            return settings_doc, models_doc

    def load_settings(self) -> Settings:
        settings_doc, models_doc = self.load_documents()
        return self._settings_from_documents(settings_doc, models_doc)

    def _settings_from_documents(
        self, settings_doc: dict[str, Any], models_doc: dict[str, Any]
    ) -> Settings:
        values: dict[str, Any] = {}
        for section_name, mapping in _SECTION_FIELDS.items():
            section = settings_doc.get(section_name)
            if section is None:
                section = {}
            if not isinstance(section, dict):
                raise ConfigDocumentError(f"settings.{section_name} must be an object")
            unknown = set(section) - set(mapping)
            if section_name == "tts":
                unknown -= _RETIRED_TTS_FIELDS
            if unknown:
                raise ConfigDocumentError(
                    f"Unknown settings.{section_name} fields: {', '.join(sorted(unknown))}"
                )
            for public_name, field_name in mapping.items():
                if public_name not in section:
                    # Additive schema-v1 fields remain readable until bootstrap
                    # persists them atomically with a revision bump.
                    values[field_name] = getattr(Settings(), field_name)
                    continue
                values[field_name] = _coerce_like(
                    section[public_name], getattr(Settings(), field_name),
                    f"settings.{section_name}.{public_name}",
                )

        active = models_doc["active_profile"]
        profile = next(
            item for item in models_doc["profiles"] if item["id"] == active
        )
        values.update(
            llm_base_url=profile["base_url"].rstrip("/"),
            llm_model=profile["model"],
            llm_context_size=profile["context_size"],
            llm_reasoning_levels=tuple(profile["reasoning_levels"]),
            llm_default_reasoning=profile["default_reasoning"],
            system_prompt=None,
        )
        for field_name, file_name in _SECRET_FIELDS.items():
            path = self.secrets_root / file_name
            values[field_name] = path.read_text(encoding="utf-8").strip() if path.is_file() else None
        settings = Settings(**values)
        validate_settings(settings)
        return settings

    def public_snapshot(self) -> dict[str, Any]:
        settings_doc, models_doc = self.load_documents()
        return {
            "schema_version": SCHEMA_VERSION,
            "config_root": str(self.root),
            "data_root": str(self.data_root),
            "settings": settings_doc,
            "models": models_doc,
            "secrets": {
                name: {"configured": (self.secrets_root / file_name).is_file()}
                for name, file_name in _SECRET_FIELDS.items()
            },
        }

    def schema(self) -> dict[str, Any]:
        defaults = Settings()
        sections: dict[str, Any] = {}
        for section_name, mapping in _SECTION_FIELDS.items():
            sections[section_name] = {
                public_name: _field_schema(field_name, getattr(defaults, field_name))
                for public_name, field_name in mapping.items()
            }
        return {
            "schema_version": SCHEMA_VERSION,
            "documents": {
                "settings": {"sections": sections},
                "models": {
                    "active_profile": {"type": "string"},
                    "profiles": {"type": "array", "item": "model_profile"},
                },
            },
            "model_profile": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "base_url": {"type": "url"},
                "model": {"type": "string"},
                "context_size": {"type": "integer", "minimum": 1},
                "reasoning_levels": {"type": "string_array", "minimum_items": 1},
                "default_reasoning": {"type": "string"},
            },
            "secrets": {
                "client_api_key": {"write_only": True},
                "llm_api_key": {"write_only": True},
                "tts_http_api_key": {"write_only": True},
                "open_terminal_api_key": {"write_only": True},
            },
            "locked": {"runtime.cuda_visible_devices": self.load_settings().cuda_visible_devices},
        }

    def patch(
        self, document: str, changes: dict[str, Any], expected_revision: int
    ) -> dict[str, Any]:
        if document not in {"settings", "models"}:
            raise ConfigDocumentError("document must be 'settings' or 'models'")
        path = self.settings_path if document == "settings" else self.models_path
        with self._lock:
            current = _read_document(path, document)
            if current["revision"] != expected_revision:
                raise ConfigConflictError(
                    f"Stale {document} revision; expected {current['revision']}"
                )
            candidate = _merge_document(current, changes)
            if document == "settings" and candidate.get("runtime") != current.get("runtime"):
                raise ConfigDocumentError("GPU selection is installation-only; cannot change it over the API")
            candidate["schema_version"] = SCHEMA_VERSION
            candidate["revision"] = current["revision"] + 1
            other = _read_document(
                self.models_path if document == "settings" else self.settings_path,
                "models" if document == "settings" else "settings",
            )
            settings_doc = candidate if document == "settings" else other
            models_doc = candidate if document == "models" else other
            _validate_models_document(models_doc)
            self._settings_from_documents(settings_doc, models_doc)
            _atomic_json_write(path, candidate, backup=True)
        return self.public_snapshot()

    def set_secret(self, name: str, value: str | None) -> dict[str, Any]:
        file_name = _SECRET_FIELDS.get(name)
        if file_name is None:
            raise ConfigDocumentError(f"Unknown secret: {name}")
        target = self.secrets_root / file_name
        with self._lock:
            if value is None or not value.strip():
                if target.exists():
                    target.unlink()
            else:
                _atomic_write(target, (value.strip() + "\n").encode(), 0o600)
        return self.public_snapshot()

    def rollback(self, document: str) -> dict[str, Any]:
        if document not in {"settings", "models"}:
            raise ConfigDocumentError("document must be 'settings' or 'models'")
        path = self.settings_path if document == "settings" else self.models_path
        previous_path = _previous_path(path)
        with self._lock:
            if not previous_path.is_file():
                raise ConfigDocumentError(f"No previous {document} configuration")
            current = _read_document(path, document)
            previous = _read_document(previous_path, document)
            previous["revision"] = current["revision"] + 1
            other = _read_document(
                self.models_path if document == "settings" else self.settings_path,
                "models" if document == "settings" else "settings",
            )
            settings_doc = previous if document == "settings" else other
            models_doc = previous if document == "models" else other
            _validate_models_document(models_doc)
            self._settings_from_documents(settings_doc, models_doc)
            _atomic_json_write(path, previous, backup=True)
        return self.public_snapshot()

    def changed_fields(self, active: Settings) -> tuple[str, ...]:
        candidate = self.load_settings()
        return tuple(
            item.name
            for item in fields(Settings)
            if item.name not in _SECRET_FIELDS
            and getattr(active, item.name) != getattr(candidate, item.name)
        )


def _settings_document(settings: Settings, *, revision: int) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "revision": revision,
    }
    for section_name, mapping in _SECTION_FIELDS.items():
        document[section_name] = {
            public_name: getattr(settings, field_name)
            for public_name, field_name in mapping.items()
        }
    return document


def _models_document(settings: Settings, *, revision: int) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": revision,
        "active_profile": "default",
        "profiles": [
            {
                "id": "default",
                "name": settings.llm_model,
                "base_url": settings.llm_base_url,
                "model": settings.llm_model,
                "context_size": settings.llm_context_size,
                "reasoning_levels": list(settings.llm_reasoning_levels),
                "default_reasoning": settings.llm_default_reasoning,
            }
        ],
    }


def _validate_models_document(document: dict[str, Any]) -> None:
    allowed = {"schema_version", "revision", "active_profile", "profiles"}
    unknown = set(document) - allowed
    if unknown:
        raise ConfigDocumentError(f"Unknown models fields: {', '.join(sorted(unknown))}")
    active = document.get("active_profile")
    profiles = document.get("profiles")
    if not isinstance(active, str) or not _PROFILE_ID.fullmatch(active):
        raise ConfigDocumentError("models.active_profile is invalid")
    if not isinstance(profiles, list) or not profiles:
        raise ConfigDocumentError("models.profiles must be a non-empty array")
    ids: list[str] = []
    required = {
        "id", "name", "base_url", "model", "context_size",
        "reasoning_levels", "default_reasoning",
    }
    for index, profile in enumerate(profiles):
        label = f"models.profiles[{index}]"
        if not isinstance(profile, dict) or set(profile) != required:
            raise ConfigDocumentError(f"{label} has invalid fields")
        profile_id = profile["id"]
        if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
            raise ConfigDocumentError(f"{label}.id is invalid")
        ids.append(profile_id)
        if not isinstance(profile["name"], str) or not profile["name"].strip():
            raise ConfigDocumentError(f"{label}.name must not be empty")
        if not isinstance(profile["model"], str) or not profile["model"].strip():
            raise ConfigDocumentError(f"{label}.model must not be empty")
        parsed = urlparse(profile["base_url"] if isinstance(profile["base_url"], str) else "")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigDocumentError(f"{label}.base_url must be an HTTP URL")
        if isinstance(profile["context_size"], bool) or not isinstance(profile["context_size"], int) or profile["context_size"] < 1:
            raise ConfigDocumentError(f"{label}.context_size must be positive")
        levels = profile["reasoning_levels"]
        if not isinstance(levels, list) or not levels or not all(
            isinstance(item, str) and item.strip() for item in levels
        ) or len(levels) != len(set(levels)):
            raise ConfigDocumentError(f"{label}.reasoning_levels is invalid")
        if profile["default_reasoning"] not in levels:
            raise ConfigDocumentError(f"{label}.default_reasoning is not supported")
    if len(ids) != len(set(ids)):
        raise ConfigDocumentError("models.profiles contains duplicate ids")
    if active not in ids:
        raise ConfigDocumentError("models.active_profile does not exist")


def _read_document(path: Path, name: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigDocumentError(f"Missing {name} configuration: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigDocumentError(f"Invalid {name} JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ConfigDocumentError(f"{name} configuration must be an object")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ConfigDocumentError(f"{name} requires schema_version {SCHEMA_VERSION}")
    revision = value.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ConfigDocumentError(f"{name}.revision must be a positive integer")
    return value


def _merge_document(current: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(changes, dict):
        raise ConfigDocumentError("changes must be an object")
    candidate = json.loads(json.dumps(current))
    for key, value in changes.items():
        if key in {"schema_version", "revision"}:
            raise ConfigDocumentError(f"{key} cannot be patched")
        if key not in candidate:
            raise ConfigDocumentError(f"Unknown field: {key}")
        if isinstance(candidate[key], dict):
            if not isinstance(value, dict):
                raise ConfigDocumentError(f"{key} changes must be an object")
            unknown = set(value) - set(candidate[key])
            if unknown:
                raise ConfigDocumentError(
                    f"Unknown {key} fields: {', '.join(sorted(unknown))}"
                )
            candidate[key].update(value)
        else:
            candidate[key] = value
    return candidate


def _coerce_like(value: Any, default: Any, label: str) -> Any:
    if default is None:
        if value is None or isinstance(value, str):
            return value
        raise ConfigDocumentError(f"{label} must be a string or null")
    if isinstance(default, bool):
        if type(value) is not bool:
            raise ConfigDocumentError(f"{label} must be a boolean")
        return value
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigDocumentError(f"{label} must be an integer")
        return value
    if isinstance(default, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigDocumentError(f"{label} must be a number")
        return float(value)
    if isinstance(default, str):
        if not isinstance(value, str):
            raise ConfigDocumentError(f"{label} must be a string")
        return value
    raise ConfigDocumentError(f"Unsupported configuration type for {label}")


def _field_schema(field_name: str, default: Any) -> dict[str, Any]:
    if isinstance(default, bool):
        result: dict[str, Any] = {"type": "boolean"}
    elif isinstance(default, int):
        result = {"type": "integer"}
    elif isinstance(default, float):
        result = {"type": "number"}
    elif default is None:
        result = {"type": "nullable_string"}
    else:
        result = {"type": "string"}
    result["default"] = default
    if field_name in {"host", "port", "cuda_visible_devices"}:
        result["apply"] = "process_restart"
    elif field_name.startswith(("llm_", "stt_", "vad_", "tts_", "ack_")):
        result["apply"] = "component_reload"
    else:
        result["apply"] = "next_session"
    if field_name == "cuda_visible_devices":
        result["read_only"] = True
    return result


def _resolve_source(value: str, source_root: Path) -> Path:
    requested = Path(value).expanduser()
    return requested.resolve() if requested.is_absolute() else (source_root / requested).resolve()


def _copy_tree_missing(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(f"Configuration directory not found: {source}")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    for item in sorted(source.rglob("*")):
        if item.is_symlink():
            continue
        relative = item.relative_to(source)
        destination = target / relative
        if item.is_dir():
            destination.mkdir(parents=True, exist_ok=True, mode=0o750)
        elif item.is_file() and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
            _atomic_write(destination, item.read_bytes(), 0o640)


def _sqlite_backup(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as source_db:
            with sqlite3.connect(temporary) as target_db:
                source_db.backup(target_db)
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _previous_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.previous{path.suffix}")


def _atomic_json_write(path: Path, value: dict[str, Any], *, backup: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if backup and path.is_file():
        _atomic_write(_previous_path(path), path.read_bytes(), 0o600)
    encoded = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    _atomic_write(path, encoded, 0o600)


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        if temporary.exists():
            temporary.unlink()
