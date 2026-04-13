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

    # LLM-guidance metadata -- optional, safe to leave empty on any tool
    when_to_use: str = ""
    when_not_to_use: str = ""
    no_result_meaning: str = ""
    failure_handling_instruction: str = ""
    evidence_keys_produced: list = []
    authoritative_fields: list = []

    def get_spec(self) -> ToolSpec:
        """Return the JSON-Schema tool spec for the LLM.

        Composes a richer description from the base description plus any
        guidance metadata defined on the subclass.
        """
        parts = [self.description]
        if self.when_to_use:
            parts.append("Use when: " + self.when_to_use)
        if self.when_not_to_use:
            parts.append("Do not use when: " + self.when_not_to_use)
        if self.no_result_meaning:
            parts.append("No result means: " + self.no_result_meaning)
        if self.failure_handling_instruction:
            parts.append("On failure: " + self.failure_handling_instruction)
        composed = "\n".join(parts)
        return ToolSpec(
            name=self.name,
            description=composed,
            parameters=self.parameters_schema,
        )

    def execute(self, **kwargs) -> ToolResult:
        """Execute with timing and error handling.

        The ``tenant`` kwarg is extracted and stored on ``self._tenant``
        so subclass ``run()`` methods can apply tenant-scoped queries.
        The ``parent_run_id`` kwarg is extracted and stored on
        ``self._parent_run_id`` so delegation tools can link child runs.
        The ``lf_parent_span`` kwarg is extracted and stored on
        ``self._lf_parent_span`` so delegation tools can propagate tracing.
        All are removed from kwargs before forwarding to ``run()``.
        """
        self._tenant = kwargs.pop("tenant", None)
        self._parent_run_id = kwargs.pop("parent_run_id", None)
        self._lf_parent_span = kwargs.pop("lf_parent_span", None)
        start = time.monotonic()
        try:
            result = self.run(**kwargs)
            result.duration_ms = int((time.monotonic() - start) * 1000)
            return result
        except Exception as exc:
            duration = int((time.monotonic() - start) * 1000)
            logger.exception("Tool %s failed", self.name)
            return ToolResult(success=False, error=str(exc), duration_ms=duration)
        finally:
            self._tenant = None
            self._parent_run_id = None
            self._lf_parent_span = None

    @abstractmethod
    def run(self, **kwargs) -> ToolResult:
        """Implement the actual tool logic. Must return a ToolResult."""
        ...

    # ------------------------------------------------------------------
    # Tenant scoping helper
    # ------------------------------------------------------------------
    def _scoped(self, queryset):
        """Apply tenant filter to a queryset if a tenant was provided."""
        if self._tenant is not None:
            return queryset.filter(tenant=self._tenant)
        return queryset


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
