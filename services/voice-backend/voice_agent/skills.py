from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
from threading import RLock
from typing import Any
from uuid import uuid4

from .tools import ToolDefinition, ToolExecutionResult

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_MAX_SKILL_BYTES = 256 * 1024
_MANIFEST_NAME = "manifest.json"
_SECRET_ASSIGNMENT = re.compile(
    r"(?im)^\s*(?:api[_ -]?key|password|passwd|secret|access[_ -]?token)"
    r"\s*[:=]\s*['\"]?[A-Za-z0-9_+./=-]{8,}"
)
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")


def _object_schema(
    properties: dict[str, Any], required: tuple[str, ...] = ()
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        value["required"] = list(required)
    return value


@dataclass(frozen=True, slots=True)
class Skill:
    slug: str
    name: str
    description: str
    version: str
    content: str

    def summary(self) -> dict[str, str]:
        return {
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "version": self.version,
        }


class SkillCatalog:
    """Read-only catalog of operator-controlled Markdown instructions."""

    def __init__(self, skills: tuple[Skill, ...] = ()) -> None:
        self._skills = {skill.slug: skill for skill in skills}

    @classmethod
    def load(cls, directory: str, enabled: str = "*") -> "SkillCatalog":
        root = _resolve_directory(directory)
        if not root.is_dir():
            raise FileNotFoundError(f"Skills directory not found: {root}")
        allow = _parse_enabled(enabled)
        return cls._load_root(root, allow)

    @classmethod
    def _load_root(
        cls, root: Path, allow: set[str] | None
    ) -> "SkillCatalog":
        skills: list[Skill] = []
        for candidate in sorted(root.iterdir(), key=lambda item: item.name):
            if not candidate.is_dir() or candidate.is_symlink():
                continue
            slug = candidate.name
            if not _SLUG.fullmatch(slug) or (allow is not None and slug not in allow):
                continue
            path = candidate / "SKILL.md"
            if not path.is_file() or path.is_symlink():
                continue
            resolved = path.resolve()
            if root not in resolved.parents:
                continue
            if path.stat().st_size > _MAX_SKILL_BYTES:
                raise ValueError(f"Skill is larger than {_MAX_SKILL_BYTES} bytes: {slug}")
            skills.append(_parse_skill(slug, path.read_text(encoding="utf-8")))
        if allow is not None:
            missing = sorted(allow - {skill.slug for skill in skills})
            if missing:
                raise ValueError(f"Enabled skills not found: {', '.join(missing)}")
        return cls(tuple(skills))

    def list(self) -> tuple[Skill, ...]:
        return tuple(self._skills.values())

    def get(self, slug: str) -> Skill | None:
        return self._skills.get(slug)

    def prompt_index(self) -> str:
        if not self._skills:
            return "## Available skills\n\nNo skills are currently enabled."
        rows = [
            "## Available skills",
            "",
            "Load a relevant skill with `read_skill` before performing the task. "
            "Use `list_skills` when the matching skill is unclear. Do not load every "
            "skill speculatively.",
            "",
        ]
        rows.extend(
            f"- `{skill.slug}` — {skill.description}" for skill in self.list()
        )
        return "\n".join(rows)


@dataclass(frozen=True, slots=True)
class SkillManifest:
    schema_version: int
    revision: int
    enabled: tuple[str, ...]

    def value(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "enabled": list(self.enabled),
        }


class SkillCatalogManager:
    """Atomically reloadable catalog backed by a persistent JSON manifest."""

    def __init__(self, directory: str, manifest_path: str | None = None) -> None:
        self.directory = _resolve_directory(directory)
        if not self.directory.is_dir():
            raise FileNotFoundError(
                f"Skills directory not found: {self.directory}"
            )
        requested_manifest = Path(manifest_path).expanduser() if manifest_path else None
        self.manifest_path = (
            requested_manifest.resolve()
            if requested_manifest is not None and requested_manifest.is_absolute()
            else (self.directory / (manifest_path or _MANIFEST_NAME)).resolve()
        )
        if self.manifest_path.parent != self.directory:
            raise ValueError("Skills manifest must live directly in the skills directory")
        self._lock = RLock()
        manifest, catalog = self._load_candidate()
        self._manifest = manifest
        self._catalog = catalog

    @property
    def revision(self) -> int:
        with self._lock:
            return self._manifest.revision

    def snapshot(self) -> SkillCatalog:
        with self._lock:
            return self._catalog

    def manifest(self) -> SkillManifest:
        with self._lock:
            return self._manifest

    def pending_manifest(self) -> SkillManifest:
        with self._lock:
            return _read_manifest(self.manifest_path)

    def reload(self) -> SkillCatalog:
        with self._lock:
            # Any exception occurs before the swap and therefore leaves the
            # last known-good in-memory catalog untouched.
            manifest, catalog = self._load_candidate()
            self._manifest = manifest
            self._catalog = catalog
            return catalog

    def validate(self, slug: str, raw: str) -> Skill:
        _validate_slug(slug)
        encoded = raw.encode("utf-8")
        if len(encoded) > _MAX_SKILL_BYTES:
            raise ValueError(f"Skill is larger than {_MAX_SKILL_BYTES} bytes: {slug}")
        if _PRIVATE_KEY.search(raw) or _SECRET_ASSIGNMENT.search(raw):
            raise ValueError("Skill appears to contain a credential or private key")
        return _parse_skill(slug, raw)

    def install(
        self, slug: str, raw: str, *, overwrite: bool, enable: bool = True
    ) -> Skill:
        skill = self.validate(slug, raw)
        with self._lock:
            manifest = _read_manifest(self.manifest_path)
            target_directory = self.directory / slug
            target = target_directory / "SKILL.md"
            if target_directory.is_symlink() or target.is_symlink():
                raise ValueError("Skill destination cannot be a symlink")
            existed = target.is_file()
            if existed and not overwrite:
                raise FileExistsError(f"Skill already exists: {slug}")
            if overwrite and not existed:
                raise FileNotFoundError(f"Skill does not exist: {slug}")
            if target_directory.exists() and not target_directory.is_dir():
                raise ValueError("Skill destination is not a directory")
            previous = target.read_bytes() if existed else None
            if not target_directory.exists():
                target_directory.mkdir(mode=0o750)
            try:
                _atomic_write(target, raw.encode("utf-8"), mode=0o640)
                enabled = list(manifest.enabled)
                if enable and slug not in enabled:
                    enabled.append(slug)
                updated = SkillManifest(
                    schema_version=manifest.schema_version,
                    revision=manifest.revision + 1,
                    enabled=tuple(enabled),
                )
                # Validate the complete prospective catalog before publishing
                # its manifest. The active in-memory catalog changes only on
                # the explicit reload_skills operation.
                self._catalog_for_manifest(updated)
                _atomic_write(
                    self.manifest_path,
                    (json.dumps(updated.value(), ensure_ascii=False, indent=2) + "\n").encode(),
                    mode=0o640,
                )
            except Exception:
                if previous is not None:
                    _atomic_write(target, previous, mode=0o640)
                elif target.exists():
                    target.unlink()
                    try:
                        target_directory.rmdir()
                    except OSError:
                        pass
                raise
        return skill

    def _load_candidate(self) -> tuple[SkillManifest, SkillCatalog]:
        manifest = _read_manifest(self.manifest_path)
        catalog = self._catalog_for_manifest(manifest)
        return manifest, catalog

    def _catalog_for_manifest(self, manifest: SkillManifest) -> SkillCatalog:
        skills: list[Skill] = []
        for slug in manifest.enabled:
            candidate = self.directory / slug
            path = candidate / "SKILL.md"
            if (
                not candidate.is_dir()
                or candidate.is_symlink()
                or not path.is_file()
                or path.is_symlink()
                or self.directory not in path.resolve().parents
            ):
                raise ValueError(f"Enabled skill not found or unsafe: {slug}")
            skills.append(self.validate(slug, path.read_text(encoding="utf-8")))
        return SkillCatalog(tuple(skills))


class SkillTools:
    def __init__(self, catalog: SkillCatalog | SkillCatalogManager) -> None:
        self.catalog = catalog

    def _snapshot(self) -> SkillCatalog:
        if isinstance(self.catalog, SkillCatalogManager):
            return self.catalog.snapshot()
        return self.catalog

    def definitions(self) -> tuple[ToolDefinition, ...]:
        slugs = [skill.slug for skill in self._snapshot().list()]
        name_schema: dict[str, Any] = {
            "type": "string",
            "description": "Skill slug from list_skills or the system prompt index.",
        }
        if slugs:
            name_schema["enum"] = slugs
        return (
            ToolDefinition(
                "list_skills",
                "List available instruction skills with descriptions. This does not execute code.",
                _object_schema({}),
            ),
            ToolDefinition(
                "read_skill",
                "Load one relevant SKILL.md instruction by its exact slug before applying it.",
                _object_schema({"name": name_schema}, ("name",)),
            ),
        )

    async def execute(
        self, name: str, arguments: dict[str, Any]
    ) -> ToolExecutionResult:
        catalog = self._snapshot()
        if name == "list_skills":
            payload: dict[str, object] = {
                "skills": [skill.summary() for skill in catalog.list()]
            }
            if isinstance(self.catalog, SkillCatalogManager):
                payload["revision"] = self.catalog.revision
            return ToolExecutionResult(json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ))
        if name == "read_skill":
            slug = arguments.get("name")
            if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
                return ToolExecutionResult("Invalid skill name", is_error=True)
            skill = catalog.get(slug)
            if skill is None:
                return ToolExecutionResult(f"Unknown or disabled skill: {slug}", is_error=True)
            return ToolExecutionResult(skill.content)
        return ToolExecutionResult(f"Unknown skill tool: {name}", is_error=True)


def compose_system_prompt(base_prompt: str, catalog: SkillCatalog) -> str:
    return f"{base_prompt.rstrip()}\n\n{catalog.prompt_index()}"


def _resolve_directory(directory: str) -> Path:
    requested = Path(directory).expanduser()
    if requested.is_absolute():
        return requested.resolve()
    package_root = Path(__file__).resolve().parent.parent
    source_candidate = (package_root / requested).resolve()
    if source_candidate.exists():
        return source_candidate
    # setuptools data-files land below the active environment prefix. Source
    # releases use the first path; an installed wheel can use this fallback.
    return (Path(sys.prefix) / requested).resolve()


def _validate_slug(slug: str) -> None:
    if not _SLUG.fullmatch(slug):
        raise ValueError(f"Invalid skill slug: {slug}")


def _read_manifest(path: Path) -> SkillManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Skills manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Skills manifest is invalid JSON: {exc.msg}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("Skills manifest requires schema_version 1")
    revision = raw.get("revision")
    enabled = raw.get("enabled")
    if not isinstance(revision, int) or revision < 1:
        raise ValueError("Skills manifest revision must be a positive integer")
    if not isinstance(enabled, list) or not all(isinstance(item, str) for item in enabled):
        raise ValueError("Skills manifest enabled must be a string array")
    if len(enabled) != len(set(enabled)):
        raise ValueError("Skills manifest contains duplicate enabled slugs")
    for slug in enabled:
        _validate_slug(slug)
    return SkillManifest(1, revision, tuple(enabled))


def _atomic_write(path: Path, content: bytes, *, mode: int) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _parse_enabled(value: str) -> set[str] | None:
    normalized = value.strip()
    if normalized in {"", "*"}:
        return None
    result = {item.strip() for item in normalized.split(",") if item.strip()}
    invalid = sorted(item for item in result if not _SLUG.fullmatch(item))
    if invalid:
        raise ValueError(f"Invalid skill slugs: {', '.join(invalid)}")
    return result


def _parse_skill(slug: str, raw: str) -> Skill:
    text = raw.lstrip("\ufeff").strip()
    if not text.startswith("---\n"):
        raise ValueError(f"Skill {slug} must start with YAML-style front matter")
    marker = text.find("\n---\n", 4)
    if marker < 0:
        raise ValueError(f"Skill {slug} has unterminated front matter")
    header_text = text[4:marker]
    content = text[marker + 5 :].strip()
    metadata: dict[str, str] = {}
    for line in header_text.splitlines():
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or not value.strip():
            raise ValueError(f"Skill {slug} has invalid front matter line: {line}")
        metadata[key.strip()] = value.strip().strip("\"'")
    name = metadata.get("name", "")
    description = metadata.get("description", "")
    version = metadata.get("version", "1")
    if not name or not description or not content:
        raise ValueError(f"Skill {slug} requires name, description, and Markdown body")
    return Skill(slug, name, description, version, content)
