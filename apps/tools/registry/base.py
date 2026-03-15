"""Base tool class and tool registry for agent tool-use.

Every concrete tool subclasses ``BaseTool`` and registers itself via the
``@register_tool`` decorator or by calling ``ToolRegistry.register()``.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type

from apps.agents.services.llm_client import ToolSpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base tool
# ---------------------------------------------------------------------------
@dataclass
class ToolResult:
    """Structured output from a tool execution."""
    success: bool = True
    data: Any = None
    error: str = ""
    duration_ms: int = 0


class BaseTool(ABC):
    """Abstract base class for all agent-callable tools."""

    name: str = ""
    description: str = ""
    parameters_schema: Dict[str, Any] = {}
    required_permission: str = ""  # RBAC permission code for governance metadata

    def get_spec(self) -> ToolSpec:
        """Return the JSON-Schema tool spec for the LLM."""
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=self.parameters_schema,
        )

    def execute(self, **kwargs) -> ToolResult:
        """Execute with timing and error handling."""
        start = time.monotonic()
        try:
            result = self.run(**kwargs)
            result.duration_ms = int((time.monotonic() - start) * 1000)
            return result
        except Exception as exc:
            duration = int((time.monotonic() - start) * 1000)
            logger.exception("Tool %s failed", self.name)
            return ToolResult(success=False, error=str(exc), duration_ms=duration)

    @abstractmethod
    def run(self, **kwargs) -> ToolResult:
        """Implement the actual tool logic. Must return a ToolResult."""
        ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
class ToolRegistry:
    """Singleton registry mapping tool names → tool instances."""

    _tools: Dict[str, BaseTool] = {}

    @classmethod
    def register(cls, tool: BaseTool) -> None:
        cls._tools[tool.name] = tool
        logger.debug("Registered tool: %s", tool.name)

    @classmethod
    def get(cls, name: str) -> Optional[BaseTool]:
        return cls._tools.get(name)

    @classmethod
    def get_all(cls) -> Dict[str, BaseTool]:
        return dict(cls._tools)

    @classmethod
    def get_specs(cls, names: Optional[List[str]] = None) -> List[ToolSpec]:
        """Return ToolSpec list for the LLM.  Filter by *names* if provided."""
        tools = cls._tools
        if names:
            tools = {k: v for k, v in tools.items() if k in names}
        return [t.get_spec() for t in tools.values()]

    @classmethod
    def clear(cls) -> None:
        cls._tools.clear()


def register_tool(cls: Type[BaseTool]) -> Type[BaseTool]:
    """Class decorator — instantiate and register a tool class."""
    instance = cls()
    ToolRegistry.register(instance)
    return cls
