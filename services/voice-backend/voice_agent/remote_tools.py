from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import Settings
from .skills import SkillCatalog, SkillCatalogManager, SkillTools
from .tools import ToolDefinition, ToolExecutionResult, ToolRegistry


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


def _json_result(value: Any) -> ToolExecutionResult:
    return ToolExecutionResult(json.dumps(value, ensure_ascii=False, indent=2))


class RemoteToolError(RuntimeError):
    pass


class _RemoteJSONClient:
    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self._client = client

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            follow_redirects=True,
            timeout=None,
        )
        try:
            response = await client.request(
                method,
                f"{self.base_url}{path}",
                headers=self.headers,
                params={key: value for key, value in (params or {}).items() if value is not None},
                json=json_body,
            )
            if response.is_error:
                detail = response.text.strip()
                raise RemoteToolError(
                    f"HTTP {response.status_code} from {path}: {detail or response.reason_phrase}"
                )
            if not response.content:
                return {"status": "ok"}
            try:
                return response.json()
            except ValueError as exc:
                raise RemoteToolError(
                    f"Expected JSON from {path}, got {response.headers.get('content-type', 'unknown')}"
                ) from exc
        except httpx.HTTPError as exc:
            raise RemoteToolError(f"Cannot reach {self.base_url}{path}: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()


class OpenTerminalTools:
    """Open Terminal REST adapter. It never executes commands on this host."""

    _DEFINITIONS = (
        ToolDefinition("list_files", "List files in an Open Terminal directory.", _object_schema({
            "directory": {"type": "string", "description": "Absolute or session-relative directory; defaults to ."},
        })),
        ToolDefinition("read", "Read a text file in Open Terminal, optionally by line range.", _object_schema({
            "path": {"type": "string"},
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
        }, ("path",))),
        ToolDefinition("write", "Write complete text content to a file in Open Terminal.", _object_schema({
            "path": {"type": "string"}, "content": {"type": "string"},
        }, ("path", "content"))),
        ToolDefinition("replace", "Apply exact text replacements to a file in Open Terminal.", _object_schema({
            "path": {"type": "string"},
            "replacements": {"type": "array", "items": {"type": "object", "properties": {
                "target": {"type": "string"}, "replacement": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
                "allow_multiple": {"type": "boolean"},
            }, "required": ["target", "replacement"], "additionalProperties": False}},
        }, ("path", "replacements"))),
        ToolDefinition("grep", "Search file contents with Open Terminal grep.", _object_schema({
            "query": {"type": "string"}, "path": {"type": "string"},
            "regex": {"type": "boolean"}, "case_insensitive": {"type": "boolean"},
            "include": {"type": "string"}, "match_per_line": {"type": "boolean"},
            "max_results": {"type": "integer", "minimum": 1},
        }, ("query",))),
        ToolDefinition("execute", "Start a shell command in Open Terminal. The process continues remotely if still running; use process_status to poll it.", _object_schema({
            "command": {"type": "string"}, "cwd": {"type": "string"},
            "env": {"type": "object", "additionalProperties": {"type": "string"}},
            "wait_seconds": {"type": "number", "minimum": 0, "maximum": 300},
            "tail": {"type": "integer", "minimum": 1},
        }, ("command",))),
        ToolDefinition("process_status", "Poll an Open Terminal process and read output after an offset.", _object_schema({
            "process_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
            "wait_seconds": {"type": "number", "minimum": 0, "maximum": 300},
            "tail": {"type": "integer", "minimum": 1},
        }, ("process_id",))),
        ToolDefinition("process_input", "Send text or control characters to a running Open Terminal process.", _object_schema({
            "process_id": {"type": "string"}, "input": {"type": "string"},
        }, ("process_id", "input"))),
        ToolDefinition("process_kill", "Explicitly terminate an Open Terminal process.", _object_schema({
            "process_id": {"type": "string"}, "force": {"type": "boolean"},
        }, ("process_id",))),
    )

    def __init__(
        self,
        base_url: str,
        session_id: str,
        api_key: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        headers = {"X-Session-Id": session_id}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self.remote = _RemoteJSONClient(base_url, headers=headers, client=client)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self._DEFINITIONS

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        routes: dict[str, tuple[str, str]] = {
            "list_files": ("GET", "/files/list"),
            "read": ("GET", "/files/read"),
            "write": ("POST", "/files/write"),
            "replace": ("POST", "/files/replace"),
            "grep": ("GET", "/files/grep"),
            "execute": ("POST", "/execute"),
            "process_status": ("GET", f"/execute/{arguments.get('process_id', '')}/status"),
            "process_input": ("POST", f"/execute/{arguments.get('process_id', '')}/input"),
            "process_kill": ("DELETE", f"/execute/{arguments.get('process_id', '')}"),
        }
        route = routes.get(name)
        if route is None:
            return ToolExecutionResult(f"Unknown Open Terminal tool: {name}", is_error=True)
        method, path = route
        params: dict[str, Any] = {}
        body: dict[str, Any] | None = None
        if name == "list_files":
            params = {"directory": arguments.get("directory", ".")}
        elif name == "read":
            params = {key: arguments.get(key) for key in ("path", "start_line", "end_line")}
        elif name == "grep":
            params = dict(arguments)
            params.setdefault("path", ".")
        elif name == "execute":
            body = {key: arguments[key] for key in ("command", "cwd", "env") if key in arguments}
            params = {"wait": arguments.get("wait_seconds"), "tail": arguments.get("tail")}
        elif name == "process_status":
            params = {"wait": arguments.get("wait_seconds"), "offset": arguments.get("offset", 0), "tail": arguments.get("tail")}
        elif name == "process_input":
            body = {"input": arguments["input"]}
        elif name == "process_kill":
            params = {"force": arguments.get("force", False)}
        else:
            body = dict(arguments)
        try:
            return _json_result(await self.remote.request(method, path, params=params, json_body=body))
        except (RemoteToolError, KeyError) as exc:
            return ToolExecutionResult(str(exc), is_error=True)


class SkillAuthoringTools:
    """Validate and import OpenTerminal drafts into the backend skill store."""

    _DEFINITIONS = (
        ToolDefinition(
            "validate_skill",
            "Validate a SKILL.md draft stored in this session's Open Terminal skill-drafts directory without installing it.",
            _object_schema({
                "slug": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,63}$"},
                "draft_path": {"type": "string", "description": "Exact relative path .voice-agent/skill-drafts/<slug>/SKILL.md"},
            }, ("slug", "draft_path")),
        ),
        ToolDefinition(
            "create_skill",
            "Import a validated new Open Terminal SKILL.md draft into the persistent backend catalog and enable it. Call reload_skills afterwards.",
            _object_schema({
                "slug": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,63}$"},
                "draft_path": {"type": "string", "description": "Exact relative path .voice-agent/skill-drafts/<slug>/SKILL.md"},
            }, ("slug", "draft_path")),
        ),
        ToolDefinition(
            "update_skill",
            "Replace an existing backend skill from its validated Open Terminal draft. Call reload_skills afterwards.",
            _object_schema({
                "slug": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,63}$"},
                "draft_path": {"type": "string", "description": "Exact relative path .voice-agent/skill-drafts/<slug>/SKILL.md"},
            }, ("slug", "draft_path")),
        ),
        ToolDefinition(
            "reload_skills",
            "Atomically validate and activate the persistent skills manifest without restarting the backend.",
            _object_schema({}),
        ),
    )

    def __init__(
        self, manager: SkillCatalogManager, terminal: OpenTerminalTools
    ) -> None:
        self.manager = manager
        self.terminal = terminal

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self._DEFINITIONS

    async def execute(
        self, name: str, arguments: dict[str, Any]
    ) -> ToolExecutionResult:
        if name == "reload_skills":
            try:
                catalog = self.manager.reload()
                return _json_result({
                    "status": "active",
                    "revision": self.manager.revision,
                    "skills": [skill.summary() for skill in catalog.list()],
                })
            except (FileNotFoundError, OSError, ValueError) as exc:
                return ToolExecutionResult(
                    f"Skill reload rejected; previous catalog remains active: {exc}",
                    is_error=True,
                )

        if name not in {"validate_skill", "create_skill", "update_skill"}:
            return ToolExecutionResult(f"Unknown skill authoring tool: {name}", is_error=True)
        slug = arguments.get("slug")
        draft_path = arguments.get("draft_path")
        if not isinstance(slug, str) or not isinstance(draft_path, str):
            return ToolExecutionResult("slug and draft_path must be strings", is_error=True)
        expected_path = f".voice-agent/skill-drafts/{slug}/SKILL.md"
        if draft_path != expected_path:
            return ToolExecutionResult(
                f"draft_path must be exactly {expected_path}", is_error=True
            )
        draft = await self.terminal.execute("read", {"path": draft_path})
        if draft.is_error:
            return ToolExecutionResult(
                f"Cannot read Open Terminal draft: {draft.content}", is_error=True
            )
        try:
            payload = json.loads(draft.content)
            raw = payload.get("content")
            if not isinstance(raw, str):
                raise ValueError("Open Terminal read response has no text content")
            skill = self.manager.validate(slug, raw)
            if name == "validate_skill":
                return _json_result({
                    "status": "valid",
                    "draft_path": draft_path,
                    "skill": skill.summary(),
                    "bytes": len(raw.encode("utf-8")),
                })
            installed = self.manager.install(
                slug,
                raw,
                overwrite=name == "update_skill",
                enable=True,
            )
            return _json_result({
                "status": "installed_pending_reload",
                "operation": "created" if name == "create_skill" else "updated",
                "skill": installed.summary(),
                "manifest_revision": self.manager.pending_manifest().revision,
                "next_tool": "reload_skills",
            })
        except (FileExistsError, FileNotFoundError, json.JSONDecodeError, OSError, ValueError) as exc:
            return ToolExecutionResult(str(exc), is_error=True)


class SearXNGTools:
    _DEFINITION = ToolDefinition(
        "web_search",
        "Search the web only through the configured private SearXNG instance.",
        _object_schema({
            "query": {"type": "string"},
            "categories": {"type": "string", "description": "Comma-separated SearXNG categories"},
            "language": {"type": "string"}, "page": {"type": "integer", "minimum": 1},
            "time_range": {"type": "string", "enum": ["day", "month", "year"]},
            "safesearch": {"type": "integer", "enum": [0, 1, 2]},
        }, ("query",)),
    )

    def __init__(self, base_url: str, language: str, *, client: httpx.AsyncClient | None = None) -> None:
        self.remote = _RemoteJSONClient(base_url, client=client)
        self.language = language

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return (self._DEFINITION,)

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        if name != "web_search":
            return ToolExecutionResult(f"Unknown SearXNG tool: {name}", is_error=True)
        params = {
            "q": arguments.get("query"), "format": "json",
            "categories": arguments.get("categories"),
            "language": arguments.get("language", self.language),
            "pageno": arguments.get("page", 1),
            "time_range": arguments.get("time_range"),
            "safesearch": arguments.get("safesearch"),
        }
        try:
            raw = await self.remote.request("GET", "/search", params=params)
            results = [{key: item.get(key) for key in (
                "title", "url", "content", "engine", "score", "publishedDate"
            ) if item.get(key) is not None} for item in raw.get("results", [])]
            return _json_result({"query": raw.get("query", params["q"]), "results": results})
        except (RemoteToolError, AttributeError) as exc:
            return ToolExecutionResult(str(exc), is_error=True)


class _ReadableHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._ignored = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored += 1
        elif tag == "title":
            self._in_title = True
        elif tag in {"p", "div", "article", "section", "main", "li", "br", "h1", "h2", "h3", "h4"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored:
            self._ignored -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignored:
            return
        if self._in_title:
            self.title += data
        else:
            self._parts.append(data)

    def text(self) -> str:
        lines = [re.sub(r"\s+", " ", line).strip() for line in "".join(self._parts).splitlines()]
        return "\n".join(line for line in lines if line)


class WebFetchTools:
    _DEFINITION = ToolDefinition(
        "web_fetch",
        "Fetch one HTTP(S) page and return readable text. Use web_search first when discovering URLs.",
        _object_schema({"url": {"type": "string"}}, ("url",)),
    )

    def __init__(self, user_agent: str, *, client: httpx.AsyncClient | None = None) -> None:
        self.user_agent = user_agent
        self._client = client

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return (self._DEFINITION,)

    async def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
        if name != "web_fetch":
            return ToolExecutionResult(f"Unknown fetch tool: {name}", is_error=True)
        url = str(arguments.get("url", ""))
        if urlparse(url).scheme not in {"http", "https"}:
            return ToolExecutionResult("web_fetch accepts only http:// or https:// URLs", is_error=True)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(follow_redirects=True, timeout=None)
        try:
            response = await client.get(url, headers={"User-Agent": self.user_agent})
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if "html" in content_type:
                parser = _ReadableHTML()
                parser.feed(response.text)
                value = {"url": str(response.url), "title": parser.title.strip(), "content_type": content_type, "text": parser.text()}
            elif any(kind in content_type for kind in ("text/", "json", "xml", "javascript")) or not content_type:
                value = {"url": str(response.url), "content_type": content_type, "text": response.text}
            else:
                return ToolExecutionResult(f"Unsupported non-text content type: {content_type}", is_error=True)
            return _json_result(value)
        except httpx.HTTPError as exc:
            return ToolExecutionResult(f"Cannot fetch {url}: {exc}", is_error=True)
        finally:
            if owns_client:
                await client.aclose()


def build_tool_registry(
    settings: Settings,
    session_id: str,
    skill_catalog: SkillCatalog | SkillCatalogManager | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    if skill_catalog is not None:
        registry.extend(SkillTools(skill_catalog))
    if settings.tool_mode == "off":
        return registry
    if settings.tool_mode != "yolo":
        raise ValueError("VOICE_AGENT_TOOL_MODE must be 'off' or 'yolo'")
    terminal = OpenTerminalTools(
        settings.open_terminal_base_url,
        session_id,
        settings.open_terminal_api_key,
    )
    registry.extend(terminal)
    if isinstance(skill_catalog, SkillCatalogManager):
        registry.extend(SkillAuthoringTools(skill_catalog, terminal))
    registry.extend(SearXNGTools(settings.searxng_base_url, settings.searxng_language))
    registry.extend(WebFetchTools(settings.web_fetch_user_agent))
    return registry
