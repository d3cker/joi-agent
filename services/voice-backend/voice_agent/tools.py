from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
import json
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]

    def openai_value(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    content: str
    is_error: bool = False


class ToolExecutor(Protocol):
    def definitions(self) -> Sequence[ToolDefinition]: ...

    async def execute(
        self, name: str, arguments: dict[str, Any]
    ) -> ToolExecutionResult: ...


ToolHandler = Callable[[dict[str, Any]], Awaitable[ToolExecutionResult]]


class ToolRegistry:
    """Composable dispatch registry; registered adapters execute remotely."""

    def __init__(self) -> None:
        self._items: dict[str, tuple[ToolDefinition, ToolHandler]] = {}

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        if definition.name in self._items:
            raise ValueError(f"tool already registered: {definition.name}")
        self._items[definition.name] = (definition, handler)

    def extend(self, executor: ToolExecutor) -> None:
        """Expose another executor through this registry without changing names."""
        for definition in executor.definitions():
            async def handler(
                arguments: dict[str, Any],
                *,
                _name: str = definition.name,
                _executor: ToolExecutor = executor,
            ) -> ToolExecutionResult:
                return await _executor.execute(_name, arguments)

            self.register(definition, handler)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(item[0] for item in self._items.values())

    async def execute(
        self, name: str, arguments: dict[str, Any]
    ) -> ToolExecutionResult:
        item = self._items.get(name)
        if item is None:
            return ToolExecutionResult(f"Unknown tool: {name}", is_error=True)
        try:
            return await item[1](arguments)
        except Exception as exc:
            return ToolExecutionResult(f"Tool {name} failed: {exc}", is_error=True)


def decode_tool_arguments(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        return None, f"Invalid JSON arguments: {exc.msg}"
    if not isinstance(value, dict):
        return None, "Tool arguments must be a JSON object"
    return value, None
