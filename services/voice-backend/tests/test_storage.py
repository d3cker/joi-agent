from voice_agent.llm import ChatMessage, ToolCall
from voice_agent.protocol import ReasoningEffort
from voice_agent.storage import SQLiteSessionStore, public_messages


def test_sqlite_store_creates_resumes_and_lists_a_session(tmp_path):
    database = tmp_path / "sessions.sqlite3"
    store = SQLiteSessionStore(str(database))
    opened = store.open("session-1", "system prompt")
    assert opened.resumed is False
    assert opened.messages == (ChatMessage("system", "system prompt"),)

    store.append_message("session-1", ChatMessage("user", "Pierwsza wiadomość"))
    store.append_message("session-1", ChatMessage("assistant", "Odpowiedź"))
    store.update_reasoning_effort("session-1", ReasoningEffort.HIGH)
    store.update_conversation_language("session-1", "en-US")

    resumed = store.open("session-1", "a changed prompt")
    assert resumed.resumed is True
    assert resumed.session.reasoning_effort is ReasoningEffort.HIGH
    assert resumed.session.conversation_language == "en"
    assert resumed.session.title == "Pierwsza wiadomość"
    assert [item["text"] for item in public_messages(resumed.messages)] == [
        "Pierwsza wiadomość", "Odpowiedź"
    ]
    assert store.list_sessions()[0].id == "session-1"

    store.close()
    reopened = SQLiteSessionStore(str(database)).get("session-1")
    assert reopened is not None
    assert reopened.messages[0].content == "system prompt"


def test_sqlite_store_round_trips_tool_messages_and_cascades_delete():
    store = SQLiteSessionStore(":memory:")
    store.open("tools", "system")
    call = ToolCall("call-1", "echo", '{"text":"hello"}')
    store.append_message("tools", ChatMessage("assistant", tool_calls=(call,)))
    store.append_message(
        "tools", ChatMessage("tool", "hello", name="echo", tool_call_id="call-1")
    )

    loaded = store.get("tools")
    assert loaded is not None
    assert loaded.messages[1].tool_calls == (call,)
    assert loaded.messages[2].tool_call_id == "call-1"
    assert store.delete("tools") is True
    assert store.get("tools") is None
    assert store.delete("tools") is False


def test_session_rename_preview_and_persistent_tool_activity():
    store = SQLiteSessionStore(":memory:")
    store.open("managed", "system")
    store.append_message("managed", ChatMessage("user", "Please inspect the project"))
    renamed = store.rename("managed", "Project inspection")
    assert renamed is not None
    assert renamed.title == "Project inspection"
    assert store.list_sessions()[0].last_message == "Please inspect the project"

    store.start_tool_call(
        "managed",
        call_id="call-7",
        response_id="response-2",
        name="grep",
        arguments='{"query":"TODO"}',
        step=1,
    )
    running = store.list_tool_activity("managed")[0]
    assert running.status == "running"
    assert running.name == "grep"
    store.finish_tool_call("managed", "call-7", is_error=False)
    completed = store.list_tool_activity("managed")[0]
    assert completed.status == "completed"
    assert completed.finished_at is not None


def test_existing_tool_messages_are_backfilled(tmp_path):
    database = tmp_path / "legacy.sqlite3"
    store = SQLiteSessionStore(str(database))
    store.open("legacy", "system")
    call = ToolCall("old-call", "read", '{"path":"README.md"}')
    store.append_message("legacy", ChatMessage("assistant", tool_calls=(call,)))
    store.append_message(
        "legacy", ChatMessage("tool", "contents", name="read", tool_call_id="old-call")
    )
    store.close()

    migrated = SQLiteSessionStore(str(database))
    activity = migrated.list_tool_activity("legacy")
    assert len(activity) == 1
    assert activity[0].call_id == "old-call"
    assert activity[0].status == "completed"
