from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
import json
from typing import Any, Protocol

import httpx


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple["ToolCall", ...] = ()

    def api_value(self) -> dict[str, Any]:
        value: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name is not None:
            value["name"] = self.name
        if self.tool_call_id is not None:
            value["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            value["tool_calls"] = [item.api_value() for item in self.tool_calls]
        return value


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: str

    def api_value(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    index: int
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass(frozen=True, slots=True)
class StreamDelta:
    reasoning: str = ""
    content: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    tool_calls: tuple[ToolCallDelta, ...] = ()


def parse_sse_data(line: str) -> StreamDelta | None:
    """Parse one OpenAI-compatible SSE line, retaining reasoning separately."""
    if not line.startswith("data:"):
        return None
    raw = line[5:].strip()
    if not raw or raw == "[DONE]":
        return None
    data = json.loads(raw)
    usage = data.get("usage") or {}
    prompt_tokens = usage.get("prompt_tokens")
    if not isinstance(prompt_tokens, int):
        prompt_tokens = None
    completion_tokens = usage.get("completion_tokens")
    if not isinstance(completion_tokens, int):
        completion_tokens = None
    choices = data.get("choices") or []
    if not choices:
        return StreamDelta(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    delta = choices[0].get("delta") or {}
    tool_call_deltas: list[ToolCallDelta] = []
    for raw_call in delta.get("tool_calls") or []:
        function = raw_call.get("function") or {}
        raw_index = raw_call.get("index", 0)
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            index = 0
        raw_arguments = function.get("arguments") or ""
        if not isinstance(raw_arguments, str):
            raw_arguments = json.dumps(raw_arguments, ensure_ascii=False)
        tool_call_deltas.append(ToolCallDelta(
            index=index,
            id=str(raw_call.get("id") or ""),
            name=str(function.get("name") or ""),
            arguments=raw_arguments,
        ))
    return StreamDelta(
        reasoning=delta.get("reasoning") or delta.get("reasoning_content") or "",
        content=delta.get("content") or "",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        tool_calls=tuple(tool_call_deltas),
    )


class LLM(Protocol):
    async def stream(
        self,
        messages: Sequence[ChatMessage],
        reasoning_effort: str,
        tools: Sequence[dict[str, Any]] = (),
    ) -> AsyncIterator[StreamDelta]: ...


class OpenAICompatibleLLM:
    def __init__(self, base_url: str, model: str, api_key: str | None = None, timeout: float = 120.0):
        self.url = f"{base_url.rstrip('/')}/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        reasoning_effort: str,
        tools: Sequence[dict[str, Any]] = (),
    ) -> AsyncIterator[StreamDelta]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [item.api_value() for item in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            "reasoning_effort": reasoning_effort,  # Always explicit by contract.
        }
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", self.url, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    parsed = parse_sse_data(line)
                    if parsed is not None:
                        yield parsed


class MockLLM:
    def __init__(self, response: str = "To jest testowa odpowiedź lokalnego asystenta."):
        self.response = response
        self.last_reasoning_effort: str | None = None

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        reasoning_effort: str,
        tools: Sequence[dict[str, Any]] = (),
    ) -> AsyncIterator[StreamDelta]:
        self.last_reasoning_effort = reasoning_effort
        yield StreamDelta(reasoning="mock reasoning; never spoken")
        for word in self.response.split(" "):
            yield StreamDelta(content=word + " ")
