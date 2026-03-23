"""
LLMExtractionAdapter — Wraps LLMClient for schema-driven field extraction.

Responsibilities:
    - Calls PromptBuilderService to build messages
    - Invokes LLMClient with structured JSON output
    - Parses and validates the LLM response against expected field keys
    - Retries on parse failures (configurable)
    - Returns parsed results as FieldResult objects
    - Logs LLM interactions for audit traceability
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from django.conf import settings

from apps.agents.services.llm_client import LLMClient, LLMResponse
from apps.extraction_core.models import TaxJurisdictionProfile
from apps.extraction_core.services.extraction_service import (
    ExtractionTemplate,
    FieldResult,
    FieldSpec,
)
from apps.extraction_core.services.prompt_builder import PromptBuilderService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audit dataclass
# ---------------------------------------------------------------------------

@dataclass
class LLMExtractionAudit:
    """Captures a full record of an LLM extraction call for auditability."""

    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    duration_ms: int = 0
    attempts: int = 0
    success: bool = False
    error_message: str = ""
    fields_extracted: int = 0
    raw_response: str = ""

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "duration_ms": self.duration_ms,
            "attempts": self.attempts,
            "success": self.success,
            "error_message": self.error_message,
            "fields_extracted": self.fields_extracted,
        }


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class LLMExtractionAdapter:
    """
    Adapter between ExtractionService and the LLM layer.

    Usage::

        adapter = LLMExtractionAdapter()
        results, audit = adapter.extract_fields(
            template=template,
            ocr_text=ocr_text,
            jurisdiction_profile=profile,
        )
    """

    def __init__(
        self,
        max_retries: int = 2,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        self._max_retries = max_retries
        self._temperature = temperature
        self._max_tokens = max_tokens or getattr(
            settings, "LLM_MAX_TOKENS", 4096,
        )
        self._llm: LLMClient | None = None

    def _get_client(self) -> LLMClient:
        """Lazy-init LLMClient so it is not created at import time."""
        if self._llm is None:
            self._llm = LLMClient(
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
        return self._llm

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_fields(
        self,
        template: ExtractionTemplate,
        ocr_text: str,
        jurisdiction_profile: Optional[TaxJurisdictionProfile] = None,
        unresolved_field_keys: Optional[set[str]] = None,
    ) -> tuple[dict[str, FieldResult], LLMExtractionAudit]:
        """
        Run LLM extraction and return parsed FieldResults + audit record.

        Parameters
        ----------
        template : ExtractionTemplate
            Schema-driven field specs.
        ocr_text : str
            Raw OCR text.
        jurisdiction_profile : TaxJurisdictionProfile, optional
            For jurisdiction-specific prompt guidance.
        unresolved_field_keys : set[str], optional
            Subset of fields to extract (hybrid mode).

        Returns
        -------
        tuple[dict[str, FieldResult], LLMExtractionAudit]
            Field key → FieldResult mapping and an audit log.
        """
        audit = LLMExtractionAudit()
        start = time.monotonic()

        messages = PromptBuilderService.build_messages(
            template=template,
            ocr_text=ocr_text,
            jurisdiction_profile=jurisdiction_profile,
            unresolved_field_keys=unresolved_field_keys,
        )

        # Determine which specs we expect in the response
        target_specs = self._collect_target_specs(
            template, unresolved_field_keys,
        )

        last_error = ""
        parsed: dict[str, Any] = {}

        for attempt in range(1, self._max_retries + 1):
            audit.attempts = attempt
            try:
                client = self._get_client()
                llm_response: LLMResponse = client.chat(
                    messages=messages,
                    response_format={"type": "json_object"},
                )

                audit.model = llm_response.model
                audit.prompt_tokens = llm_response.prompt_tokens
                audit.completion_tokens = llm_response.completion_tokens
                audit.total_tokens = llm_response.total_tokens

                raw_content = llm_response.content or ""
                audit.raw_response = raw_content[:2000]

                parsed = self._parse_json_response(raw_content)
                self._validate_structure(parsed)
                break  # success

            except (json.JSONDecodeError, ValueError) as exc:
                last_error = f"Attempt {attempt}: {exc}"
                logger.warning(
                    "LLM extraction parse/validation failed (attempt %d/%d): %s",
                    attempt,
                    self._max_retries,
                    exc,
                )
            except Exception as exc:
                last_error = f"Attempt {attempt}: {exc}"
                logger.error(
                    "LLM extraction call failed (attempt %d/%d): %s",
                    attempt,
                    self._max_retries,
                    exc,
                )
                break  # non-retryable

        audit.duration_ms = int((time.monotonic() - start) * 1000)

        if not parsed:
            audit.success = False
            audit.error_message = last_error
            logger.error("LLM extraction failed after %d attempts", audit.attempts)
            return {}, audit

        # Convert parsed JSON into FieldResult objects
        results = self._map_to_field_results(parsed, target_specs)
        audit.success = True
        audit.fields_extracted = sum(1 for r in results.values() if r.extracted)

        logger.info(
            "LLM extraction completed: %d/%d fields extracted in %dms "
            "(tokens: %d)",
            audit.fields_extracted,
            len(target_specs),
            audit.duration_ms,
            audit.total_tokens,
        )

        return results, audit

    # ------------------------------------------------------------------
    # JSON parsing & validation
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json_response(content: str) -> dict:
        """Parse JSON from LLM response, stripping markdown fences if present."""
        text = content.strip()
        if text.startswith("```"):
            # Strip ```json ... ``` wrapping
            lines = text.split("\n")
            lines = [
                l for l in lines
                if not l.strip().startswith("```")
            ]
            text = "\n".join(lines)
        return json.loads(text)

    @staticmethod
    def _validate_structure(data: dict) -> None:
        """
        Validate the top-level structure of the LLM response.

        Raises ValueError if the response is not a dict or has no
        recognised top-level keys.
        """
        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object, got {type(data).__name__}")

        valid_keys = {"header_fields", "tax_fields", "line_items"}
        if not any(k in data for k in valid_keys):
            raise ValueError(
                f"Response missing expected keys. "
                f"Got: {list(data.keys())}; expected at least one of {valid_keys}"
            )

    # ------------------------------------------------------------------
    # FieldResult mapping
    # ------------------------------------------------------------------

    @classmethod
    def _map_to_field_results(
        cls,
        parsed: dict,
        target_specs: dict[str, FieldSpec],
    ) -> dict[str, FieldResult]:
        """
        Convert parsed LLM JSON into FieldResult objects.

        Handles two response formats:
            - Rich: ``{"value": "...", "confidence": 0.9, "evidence": "..."}``
            - Simple: ``"field_key": "value"``
        """
        results: dict[str, FieldResult] = {}

        for section_key in ("header_fields", "tax_fields"):
            section = parsed.get(section_key, {})
            if not isinstance(section, dict):
                continue
            for field_key, entry in section.items():
                spec = target_specs.get(field_key)
                if not spec:
                    continue
                results[field_key] = cls._entry_to_field_result(
                    field_key, entry, spec,
                )

        return results

    @classmethod
    def _entry_to_field_result(
        cls,
        field_key: str,
        entry: Any,
        spec: FieldSpec,
    ) -> FieldResult:
        """Convert a single LLM field entry into a FieldResult."""
        result = FieldResult(
            field_key=field_key,
            display_name=spec.display_name,
            category=spec.category,
            data_type=spec.data_type,
            is_mandatory=spec.is_mandatory,
            method="LLM",
        )

        if isinstance(entry, dict):
            raw = entry.get("value")
            result.confidence = float(entry.get("confidence", 0.0))
            result.source_snippet = str(entry.get("evidence", ""))[:200]
        else:
            # Simple scalar value
            raw = entry
            result.confidence = 0.70  # default for unstructured response

        if raw is not None:
            result.raw_value = str(raw)
            result.extracted = True
        else:
            result.extracted = False
            result.confidence = 0.0

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_target_specs(
        template: ExtractionTemplate,
        unresolved_field_keys: Optional[set[str]],
    ) -> dict[str, FieldSpec]:
        """Build a dict of field_key → FieldSpec for the target fields."""
        all_specs = (
            template.header_fields
            + template.tax_fields
            + template.line_item_fields
        )
        if unresolved_field_keys is not None:
            return {
                s.field_key: s for s in all_specs
                if s.field_key in unresolved_field_keys
            }
        return {s.field_key: s for s in all_specs}
