"""Azure OpenAI LLM client — single entry-point for all agent LLM calls.

Supports both plain OpenAI and Azure OpenAI via Django settings.
Uses the openai SDK's ChatCompletion interface.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from django.conf import settings
from openai import AzureOpenAI, OpenAI

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class LLMMessage:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


@dataclass
class ToolSpec:
    """JSON-Schema tool definition passed to the model."""
    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMToolCall:
    """Parsed tool-call request from the model."""
    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    content: Optional[str] = None
    tool_calls: List[LLMToolCall] = field(default_factory=list)
    finish_reason: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    raw: Optional[Any] = None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class LLMClient:
    """Unified wrapper around Azure OpenAI / OpenAI chat completions."""

    def __init__(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        provider = getattr(settings, "LLM_PROVIDER", "azure_openai")
        self.model = model or getattr(settings, "LLM_MODEL_NAME", "gpt-4o")
        self.temperature = temperature if temperature is not None else getattr(settings, "LLM_TEMPERATURE", 0.1)
        self.max_tokens = max_tokens or getattr(settings, "LLM_MAX_TOKENS", 4096)

        if provider == "azure_openai":
            self._client = AzureOpenAI(
                api_key=settings.AZURE_OPENAI_API_KEY,
                api_version=getattr(settings, "AZURE_OPENAI_API_VERSION", "2024-02-01"),
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            )
            # Azure uses deployment name as "model"
            self.model = getattr(settings, "AZURE_OPENAI_DEPLOYMENT", "") or self.model
        else:
            self._client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self._langfuse_span: Any = None

    # ------------------------------------------------------------------
    # Chat completions
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: List[LLMMessage],
        tools: Optional[List[ToolSpec]] = None,
        tool_choice: str = "auto",
        response_format: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        """Send a chat-completion request and return a structured response."""
        api_messages = self._build_messages(messages)
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": api_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        if response_format:
            kwargs["response_format"] = response_format

        if tools:
            kwargs["tools"] = [self._tool_to_dict(t) for t in tools]
            kwargs["tool_choice"] = tool_choice

        logger.debug("LLM request: model=%s messages=%d tools=%d", self.model, len(messages), len(tools or []))

        raw = self._client.chat.completions.create(**kwargs)
        parsed = self._parse_response(raw)
        if self._langfuse_span is not None:
            try:
                from apps.core.langfuse_client import log_generation
                log_generation(
                    span=self._langfuse_span,
                    name="llm_chat",
                    model=self.model,
                    prompt_messages=[
                        {"role": m.role, "content": (m.content or "").replace("{{", "{").replace("}}", "}")}
                        for m in messages
                    ],
                    completion=parsed.content or "",
                    prompt_tokens=parsed.prompt_tokens,
                    completion_tokens=parsed.completion_tokens,
                    total_tokens=parsed.total_tokens,
                    metadata={
                        "tools_count": len(tools or []),
                        "finish_reason": parsed.finish_reason,
                    },
                )
            except Exception:
                pass
        return parsed

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _build_messages(messages: List[LLMMessage]) -> List[Dict[str, Any]]:
        out = []
        for m in messages:
            d: Dict[str, Any] = {"role": m.role, "content": m.content or ""}
            if m.tool_call_id:
                d["tool_call_id"] = m.tool_call_id
            if m.name:
                d["name"] = m.name
            if m.tool_calls:
                d["tool_calls"] = m.tool_calls
            out.append(d)
        return out

    @staticmethod
    def _tool_to_dict(spec: ToolSpec) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters or {"type": "object", "properties": {}},
            },
        }

    @staticmethod
    def _parse_response(raw) -> LLMResponse:
        choice = raw.choices[0]
        msg = choice.message

        tool_calls: List[LLMToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {"_raw": tc.function.arguments}
                tool_calls.append(LLMToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        usage = raw.usage
        return LLMResponse(
            content=msg.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "",
            model=raw.model or "",
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
            raw=raw,
        )
