"""Service for prompt testing in sandbox mode."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PromptTestResult:
    """Result of a prompt test execution."""
    rendered_prompt: str = ""
    raw_output: str = ""
    parsed_json: dict = field(default_factory=dict)
    parse_errors: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    latency_ms: int = 0
    token_usage: dict = field(default_factory=dict)
    success: bool = False

    def to_dict(self) -> dict:
        return {
            "rendered_prompt": self.rendered_prompt,
            "raw_output": self.raw_output,
            "parsed_json": self.parsed_json,
            "parse_errors": self.parse_errors,
            "validation_errors": self.validation_errors,
            "latency_ms": self.latency_ms,
            "token_usage": self.token_usage,
            "success": self.success,
        }


class PromptTestService:
    """Sandbox prompt testing — no production data mutation."""

    @classmethod
    def run_test(
        cls,
        *,
        prompt_text: str,
        ocr_text: str,
        country_code: str = "",
        regime_code: str = "",
        document_type: str = "",
        schema_code: str = "",
    ) -> PromptTestResult:
        """Execute a prompt test against the LLM adapter in sandbox mode."""
        result = PromptTestResult()

        # Build rendered prompt
        result.rendered_prompt = prompt_text

        start = time.monotonic()
        try:
            from apps.extraction_core.services.llm_extraction_adapter import LLMExtractionAdapter

            messages = [
                {"role": "system", "content": prompt_text},
                {"role": "user", "content": f"Extract all requested fields from the following document text. Return ONLY valid JSON.\n\n--- DOCUMENT TEXT ---\n{ocr_text[:60000]}"},
            ]

            raw_response = LLMExtractionAdapter.call_llm(messages)
            result.raw_output = raw_response or ""
            result.latency_ms = int((time.monotonic() - start) * 1000)

            # Try to parse JSON
            try:
                result.parsed_json = json.loads(result.raw_output)
                result.success = True
            except (json.JSONDecodeError, TypeError) as e:
                result.parse_errors.append(f"JSON parse error: {e}")
                # Try to extract JSON from response
                text = result.raw_output
                start_idx = text.find("{")
                end_idx = text.rfind("}") + 1
                if start_idx >= 0 and end_idx > start_idx:
                    try:
                        result.parsed_json = json.loads(text[start_idx:end_idx])
                        result.success = True
                        result.parse_errors.append("(recovered JSON from response)")
                    except json.JSONDecodeError:
                        pass

        except Exception as e:
            result.latency_ms = int((time.monotonic() - start) * 1000)
            result.parse_errors.append(f"LLM call failed: {e}")

        return result

    @classmethod
    def render_prompt_preview(cls, prompt_text: str, variables: dict | None = None) -> str:
        """Render prompt with sample variables for preview."""
        if not variables:
            return prompt_text
        try:
            return prompt_text.format_map(variables)
        except (KeyError, ValueError):
            return prompt_text
