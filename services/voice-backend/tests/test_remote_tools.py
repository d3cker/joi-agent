import asyncio
import json

import httpx

from voice_agent.config import Settings
from voice_agent.remote_tools import (
    OpenTerminalTools,
    SearXNGTools,
    SkillAuthoringTools,
    WebFetchTools,
    build_tool_registry,
)
from voice_agent.skills import SkillCatalog, SkillCatalogManager


def test_registry_is_off_by_default_and_yolo_has_only_remote_tools():
    assert build_tool_registry(Settings(), "session-1").definitions() == ()

    skills = SkillCatalog.load("skills", "web-research")
    skill_only = build_tool_registry(Settings(), "session-1", skills)
    assert {item.name for item in skill_only.definitions()} == {
        "list_skills", "read_skill"
    }

    registry = build_tool_registry(Settings(tool_mode="yolo"), "session-1", skills)
    names = {item.name for item in registry.definitions()}
    assert names == {
        "list_files", "read", "write", "replace", "grep", "execute",
        "process_status", "process_input", "process_kill",
        "web_search", "web_fetch",
        "list_skills", "read_skill",
    }
    assert "local_shell" not in names
    assert "python" not in names


def test_yolo_manager_registry_exposes_skill_lifecycle_tools(tmp_path):
    skill_dir = tmp_path / "baseline"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("""---
name: Baseline
description: Baseline workflow.
version: 1
---
# Baseline

Follow it.
""", encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "revision": 1, "enabled": ["baseline"]
    }), encoding="utf-8")
    manager = SkillCatalogManager(str(tmp_path))

    registry = build_tool_registry(
        Settings(tool_mode="yolo"), "session-1", manager
    )
    names = {item.name for item in registry.definitions()}
    assert names >= {
        "validate_skill", "create_skill", "update_skill", "reload_skills"
    }


def test_open_terminal_file_calls_use_bearer_and_session_header():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/files/read":
            return httpx.Response(200, json={"path": "/work/a.py", "total_lines": 3, "content": "print(1)\n"})
        if request.url.path == "/files/grep":
            return httpx.Response(200, json={"matches": [{"path": "/work/a.py", "line": 1}]})
        return httpx.Response(200, json={"status": "ok"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tools = OpenTerminalTools("http://terminal:8000", "voice-session", "secret", client=client)
            read = await tools.execute("read", {"path": "a.py", "start_line": 2, "end_line": 3})
            grep = await tools.execute("grep", {"query": "print", "regex": False})
            write = await tools.execute("write", {"path": "b.py", "content": "x = 1\n"})
            assert not read.is_error and not grep.is_error and not write.is_error

    asyncio.run(scenario())
    assert all(request.headers["x-session-id"] == "voice-session" for request in seen)
    assert all(request.headers["authorization"] == "Bearer secret" for request in seen)
    assert seen[0].url.params["path"] == "a.py"
    assert seen[0].url.params["start_line"] == "2"
    assert seen[1].url.params["path"] == "."
    assert json.loads(seen[2].content) == {"path": "b.py", "content": "x = 1\n"}


def test_skill_draft_is_validated_imported_and_reloaded_from_open_terminal(tmp_path):
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "SKILL.md").write_text("""---
name: Baseline
description: Baseline workflow.
version: 1
---
# Baseline

Follow it.
""", encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({
        "schema_version": 1, "revision": 1, "enabled": ["baseline"]
    }), encoding="utf-8")
    draft = """---
name: Calendar helper
description: Prepare careful calendar plans.
version: 1
---
# Calendar helper

Use the available tools to prepare the requested plan.
"""
    seen_paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.params.get("path"))
        return httpx.Response(200, json={
            "path": request.url.params.get("path"),
            "content": draft,
            "total_lines": len(draft.splitlines()),
        })

    async def scenario():
        manager = SkillCatalogManager(str(tmp_path))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            terminal = OpenTerminalTools(
                "http://terminal:8000", "voice-session", client=client
            )
            tools = SkillAuthoringTools(manager, terminal)
            path = ".voice-agent/skill-drafts/calendar-helper/SKILL.md"
            validated = await tools.execute(
                "validate_skill", {"slug": "calendar-helper", "draft_path": path}
            )
            assert json.loads(validated.content)["status"] == "valid"
            created = await tools.execute(
                "create_skill", {"slug": "calendar-helper", "draft_path": path}
            )
            created_value = json.loads(created.content)
            assert created_value["status"] == "installed_pending_reload"
            assert created_value["manifest_revision"] == 2
            assert manager.snapshot().get("calendar-helper") is None
            reloaded = await tools.execute("reload_skills", {})
            assert json.loads(reloaded.content)["revision"] == 2
            assert manager.snapshot().get("calendar-helper").name == "Calendar helper"

            wrong_path = await tools.execute("validate_skill", {
                "slug": "calendar-helper", "draft_path": "/tmp/SKILL.md"
            })
            assert wrong_path.is_error

    asyncio.run(scenario())
    assert seen_paths == [
        ".voice-agent/skill-drafts/calendar-helper/SKILL.md",
        ".voice-agent/skill-drafts/calendar-helper/SKILL.md",
    ]


def test_open_terminal_process_lifecycle_matches_official_api():
    seen: list[tuple[str, str, dict, dict | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, dict(request.url.params), body))
        if request.method == "POST" and request.url.path == "/execute":
            return httpx.Response(200, json={"id": "job-1", "status": "running", "next_offset": 0, "output": []})
        if request.method == "GET":
            return httpx.Response(200, json={"id": "job-1", "status": "done", "exit_code": 0, "next_offset": 2, "output": ["ok"]})
        return httpx.Response(200, json={"status": "ok"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tools = OpenTerminalTools("http://terminal:8000", "s1", client=client)
            started = await tools.execute("execute", {
                "command": "python task.py", "cwd": "/workspace", "env": {"MODE": "test"}, "wait_seconds": 0,
            })
            assert json.loads(started.content)["status"] == "running"
            polled = await tools.execute("process_status", {"process_id": "job-1", "offset": 0, "wait_seconds": 20})
            assert json.loads(polled.content)["status"] == "done"
            await tools.execute("process_input", {"process_id": "job-1", "input": "yes\n"})
            await tools.execute("process_kill", {"process_id": "job-1", "force": False})

    asyncio.run(scenario())
    assert seen == [
        ("POST", "/execute", {"wait": "0"}, {"command": "python task.py", "cwd": "/workspace", "env": {"MODE": "test"}}),
        ("GET", "/execute/job-1/status", {"wait": "20", "offset": "0"}, None),
        ("POST", "/execute/job-1/input", {}, {"input": "yes\n"}),
        ("DELETE", "/execute/job-1", {"force": "false"}, None),
    ]


def test_cancelling_agent_wait_does_not_kill_open_terminal_process():
    request_started = asyncio.Event()
    release = asyncio.Event()
    methods: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        request_started.set()
        await release.wait()
        return httpx.Response(200, json={"id": "still-running", "status": "running"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tools = OpenTerminalTools("http://terminal:8000", "s1", client=client)
            task = asyncio.create_task(tools.execute("execute", {"command": "long-job"}))
            await request_started.wait()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            release.set()

    asyncio.run(scenario())
    assert methods == ["POST"]


def test_web_search_is_searxng_json_api_only():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={
            "query": "agent głosowy",
            "results": [{
                "title": "Wynik", "url": "https://example.test/a", "content": "Opis",
                "engine": "brave", "score": 1.0, "irrelevant": "drop",
            }],
        })

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tools = SearXNGTools("http://searxng:8080", "pl-PL", client=client)
            result = await tools.execute("web_search", {"query": "agent głosowy", "time_range": "month"})
            value = json.loads(result.content)
            assert value["results"][0]["engine"] == "brave"
            assert "irrelevant" not in value["results"][0]

    asyncio.run(scenario())
    assert seen[0].url.path == "/search"
    assert seen[0].url.params["format"] == "json"
    assert seen[0].url.params["q"] == "agent głosowy"
    assert seen[0].url.params["language"] == "pl-PL"
    assert seen[0].url.params["time_range"] == "month"


def test_web_fetch_extracts_readable_html_without_scripts():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text="""
            <html><head><title>Test page</title><style>.x{}</style></head>
            <body><main><h1>Hello</h1><p>Readable <b>content</b>.</p><script>ignore()</script></main></body></html>
        """, request=request)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tools = WebFetchTools("VoiceAgent-Test", client=client)
            result = await tools.execute("web_fetch", {"url": "https://example.test/page"})
            value = json.loads(result.content)
            assert value["title"] == "Test page"
            assert "Hello" in value["text"]
            assert "Readable content." in value["text"]
            assert "ignore" not in value["text"]

    asyncio.run(scenario())


def test_web_fetch_rejects_non_http_protocols():
    result = asyncio.run(WebFetchTools("test").execute("web_fetch", {"url": "file:///etc/passwd"}))
    assert result.is_error
    assert "http://" in result.content
