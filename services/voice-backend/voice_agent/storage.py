from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4

from .llm import ChatMessage, ToolCall
from .languages import DEFAULT_CONVERSATION_LANGUAGE, conversation_language
from .protocol import ReasoningEffort


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True, slots=True)
class StoredSession:
    id: str
    created_at: str
    updated_at: str
    reasoning_effort: ReasoningEffort
    conversation_language: str
    title: str | None
    message_count: int
    last_message: str | None = None


@dataclass(frozen=True, slots=True)
class StoredToolActivity:
    call_id: str
    response_id: str
    name: str
    arguments: str
    step: int
    status: str
    started_at: str
    finished_at: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class OpenedSession:
    session: StoredSession
    messages: tuple[ChatMessage, ...]
    resumed: bool


class SQLiteSessionStore:
    """Small durable conversation store using only the Python standard library."""

    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()
        self._migrate()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _migrate(self) -> None:
        with self._lock, self._connection:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    reasoning_effort TEXT NOT NULL,
                    conversation_language TEXT NOT NULL DEFAULT 'pl',
                    title TEXT
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    name TEXT,
                    tool_call_id TEXT,
                    tool_calls_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    UNIQUE(session_id, ordinal)
                );
                CREATE INDEX IF NOT EXISTS messages_session_ordinal
                    ON messages(session_id, ordinal);
                CREATE TABLE IF NOT EXISTS tool_activity (
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    call_id TEXT NOT NULL,
                    response_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    arguments TEXT NOT NULL,
                    step INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    error TEXT,
                    PRIMARY KEY(session_id, call_id)
                );
                CREATE INDEX IF NOT EXISTS tool_activity_session_started
                    ON tool_activity(session_id, started_at);
                """
            )
            columns = {
                row["name"]
                for row in self._connection.execute("PRAGMA table_info(sessions)").fetchall()
            }
            if "conversation_language" not in columns:
                self._connection.execute(
                    "ALTER TABLE sessions ADD COLUMN conversation_language TEXT NOT NULL DEFAULT 'pl'"
                )
            self._backfill_tool_activity_unlocked()

    def _backfill_tool_activity_unlocked(self) -> None:
        """Make tool calls from pre-0.4.1 databases visible without rewriting messages."""
        assistant_rows = self._connection.execute(
            """
            SELECT session_id, ordinal, tool_calls_json, created_at
            FROM messages
            WHERE role = 'assistant' AND tool_calls_json <> '[]'
            ORDER BY session_id, ordinal
            """
        ).fetchall()
        for assistant in assistant_rows:
            try:
                calls = json.loads(assistant["tool_calls_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            for step, item in enumerate(calls, start=1):
                function = item.get("function") or {}
                call_id = item.get("id") or ""
                if not call_id:
                    continue
                result = self._connection.execute(
                    """
                    SELECT created_at FROM messages
                    WHERE session_id = ? AND role = 'tool' AND tool_call_id = ?
                    ORDER BY ordinal LIMIT 1
                    """,
                    (assistant["session_id"], call_id),
                ).fetchone()
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO tool_activity(
                        session_id, call_id, response_id, name, arguments, step,
                        status, started_at, finished_at, error
                    ) VALUES (?, ?, 'historic', ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        assistant["session_id"],
                        call_id,
                        function.get("name", "tool"),
                        function.get("arguments", "{}"),
                        step,
                        "completed" if result is not None else "interrupted",
                        assistant["created_at"],
                        result["created_at"] if result is not None else None,
                    ),
                )

    def open(self, session_id: str | None, system_prompt: str) -> OpenedSession:
        requested = session_id or str(uuid4())
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (requested,)
            ).fetchone()
            resumed = row is not None
            if row is None:
                now = _now()
                with self._connection:
                    self._connection.execute(
                        "INSERT INTO sessions(id, created_at, updated_at, reasoning_effort, conversation_language) VALUES (?, ?, ?, ?, ?)",
                        (
                            requested,
                            now,
                            now,
                            ReasoningEffort.NONE.value,
                            DEFAULT_CONVERSATION_LANGUAGE,
                        ),
                    )
                    self._insert_message(requested, ChatMessage("system", system_prompt), now)
                row = self._connection.execute(
                    "SELECT * FROM sessions WHERE id = ?", (requested,)
                ).fetchone()
            assert row is not None
            messages = self._load_messages_unlocked(requested)
            return OpenedSession(
                session=self._session_from_row(row, len(messages)),
                messages=messages,
                resumed=resumed,
            )

    def append_message(self, session_id: str, message: ChatMessage) -> None:
        with self._lock, self._connection:
            now = _now()
            self._insert_message(session_id, message, now)
            title = None
            if message.role == "user":
                title = " ".join(message.content.split())[:80] or None
            if title:
                self._connection.execute(
                    "UPDATE sessions SET updated_at = ?, title = COALESCE(title, ?) WHERE id = ?",
                    (now, title, session_id),
                )
            else:
                self._connection.execute(
                    "UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id)
                )

    def update_reasoning_effort(
        self, session_id: str, effort: ReasoningEffort
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE sessions SET reasoning_effort = ?, updated_at = ? WHERE id = ?",
                (effort.value, _now(), session_id),
            )

    def update_conversation_language(self, session_id: str, language: str) -> None:
        normalized = conversation_language(language).code
        with self._lock, self._connection:
            self._connection.execute(
                "UPDATE sessions SET conversation_language = ?, updated_at = ? WHERE id = ?",
                (normalized, _now(), session_id),
            )

    def get(self, session_id: str) -> OpenedSession | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            messages = self._load_messages_unlocked(session_id)
            return OpenedSession(
                session=self._session_from_row(row, len(messages)),
                messages=messages,
                resumed=True,
            )

    def list_sessions(self, limit: int = 100) -> tuple[StoredSession, ...]:
        safe_limit = min(max(limit, 1), 500)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT s.*, COUNT(m.id) AS message_count,
                    (
                        SELECT content FROM messages latest
                        WHERE latest.session_id = s.id
                          AND latest.role IN ('user', 'assistant')
                          AND TRIM(latest.content) <> ''
                        ORDER BY latest.ordinal DESC LIMIT 1
                    ) AS last_message
                FROM sessions s LEFT JOIN messages m ON m.session_id = s.id
                GROUP BY s.id ORDER BY s.updated_at DESC LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
            return tuple(
                self._session_from_row(row, int(row["message_count"])) for row in rows
            )

    def rename(self, session_id: str, title: str) -> StoredSession | None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title, _now(), session_id),
            )
            if cursor.rowcount == 0:
                return None
        opened = self.get(session_id)
        return opened.session if opened is not None else None

    def start_tool_call(
        self,
        session_id: str,
        *,
        call_id: str,
        response_id: str,
        name: str,
        arguments: str,
        step: int,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO tool_activity(
                    session_id, call_id, response_id, name, arguments, step,
                    status, started_at, finished_at, error
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, NULL, NULL)
                ON CONFLICT(session_id, call_id) DO UPDATE SET
                    response_id=excluded.response_id,
                    name=excluded.name,
                    arguments=excluded.arguments,
                    step=excluded.step,
                    status='running',
                    started_at=excluded.started_at,
                    finished_at=NULL,
                    error=NULL
                """,
                (session_id, call_id, response_id, name, arguments, step, _now()),
            )

    def finish_tool_call(
        self, session_id: str, call_id: str, *, is_error: bool, error: str | None = None
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE tool_activity
                SET status = ?, finished_at = ?, error = ?
                WHERE session_id = ? AND call_id = ?
                """,
                ("failed" if is_error else "completed", _now(), error, session_id, call_id),
            )

    def list_tool_activity(
        self, session_id: str, limit: int = 100
    ) -> tuple[StoredToolActivity, ...]:
        safe_limit = min(max(limit, 1), 500)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM tool_activity WHERE session_id = ?
                ORDER BY started_at DESC LIMIT ?
                """,
                (session_id, safe_limit),
            ).fetchall()
        return tuple(
            StoredToolActivity(
                call_id=row["call_id"],
                response_id=row["response_id"],
                name=row["name"],
                arguments=row["arguments"],
                step=int(row["step"]),
                status=row["status"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                error=row["error"],
            )
            for row in rows
        )

    def delete(self, session_id: str) -> bool:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                "DELETE FROM sessions WHERE id = ?", (session_id,)
            )
            return cursor.rowcount > 0

    def _insert_message(self, session_id: str, message: ChatMessage, now: str) -> None:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        ordinal = int(row[0])
        calls = json.dumps(
            [call.api_value() for call in message.tool_calls],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self._connection.execute(
            """
            INSERT INTO messages(
                session_id, ordinal, role, content, name, tool_call_id,
                tool_calls_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                ordinal,
                message.role,
                message.content,
                message.name,
                message.tool_call_id,
                calls,
                now,
            ),
        )

    def _load_messages_unlocked(self, session_id: str) -> tuple[ChatMessage, ...]:
        rows = self._connection.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY ordinal", (session_id,)
        ).fetchall()
        messages: list[ChatMessage] = []
        for row in rows:
            raw_calls = json.loads(row["tool_calls_json"])
            calls = tuple(
                ToolCall(
                    id=item.get("id", ""),
                    name=(item.get("function") or {}).get("name", ""),
                    arguments=(item.get("function") or {}).get("arguments", ""),
                )
                for item in raw_calls
            )
            messages.append(ChatMessage(
                role=row["role"],
                content=row["content"],
                name=row["name"],
                tool_call_id=row["tool_call_id"],
                tool_calls=calls,
            ))
        return tuple(messages)

    @staticmethod
    def _session_from_row(row: sqlite3.Row, message_count: int) -> StoredSession:
        keys = row.keys()
        return StoredSession(
            id=row["id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            reasoning_effort=ReasoningEffort(row["reasoning_effort"]),
            conversation_language=(
                row["conversation_language"]
                if "conversation_language" in keys
                else DEFAULT_CONVERSATION_LANGUAGE
            ),
            title=row["title"],
            message_count=message_count,
            last_message=row["last_message"] if "last_message" in keys else None,
        )


def public_messages(messages: tuple[ChatMessage, ...] | list[ChatMessage]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    pending_assistant = ""
    for message in messages:
        if message.role == "assistant" and message.tool_calls:
            pending_assistant += message.content
            continue
        if message.role == "tool" or message.role == "system":
            continue
        content = message.content
        if message.role == "assistant":
            content = pending_assistant + content
            pending_assistant = ""
        elif pending_assistant.strip():
            result.append({"role": "assistant", "text": pending_assistant.strip()})
            pending_assistant = ""
        if message.role in {"user", "assistant"} and content.strip():
            result.append({"role": message.role, "text": content.strip()})
    if pending_assistant.strip():
        result.append({"role": "assistant", "text": pending_assistant.strip()})
    return result
