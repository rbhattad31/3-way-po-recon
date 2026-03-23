"""
PromptBuilderService (Enhanced) — Schema-driven dynamic prompt builder.

Builds LLM prompts with sections:
1. Global instructions
2. Country/regime instructions
3. Document-type instructions
4. Schema fields
5. Tax instructions
6. Evidence + confidence rules

INPUT:
- country_code, regime_code, document_type
- schema_definition, tax_field_definitions

OUTPUT:
- Dynamic prompt (no hardcoded invoice schema)

All prompt generation is data-driven via jurisdiction profile + schema
+ field definitions — no hardcoded country-specific text.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from apps.core.enums import AuditEventType
from apps.extraction_core.models import (
    ExtractionSchemaDefinition,
    TaxJurisdictionProfile,
)

logger = logging.getLogger(__name__)


class PromptBuilderService:
    """
    Schema-driven dynamic prompt builder for extraction LLM calls.

    Produces a structured prompt from schema field definitions and
    jurisdiction metadata — zero hardcoded country specifics.
    """

    PROMPT_VERSION = "2.0"
    PROMPT_CODE = "extraction_core_v2"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def build(
        cls,
        *,
        country_code: str,
        regime_code: str,
        document_type: str,
        schema: ExtractionSchemaDefinition,
        jurisdiction_profile: Optional[TaxJurisdictionProfile] = None,
        field_definitions: Optional[list] = None,
        unresolved_field_keys: Optional[set[str]] = None,
    ) -> dict:
        """
        Build a complete prompt payload.

        Returns
        -------
        dict
            {
                "prompt_code": str,
                "prompt_version": str,
                "system_message": str,
                "user_message_template": str,
                "expected_schema": dict,
                "field_count": int,
            }
        """
        sections = [
            cls._global_instructions(),
            cls._country_regime_instructions(
                country_code, regime_code, jurisdiction_profile,
            ),
            cls._document_type_instructions(document_type),
            cls._schema_fields_section(
                schema, field_definitions, unresolved_field_keys,
            ),
            cls._tax_instructions(
                regime_code, jurisdiction_profile, field_definitions,
            ),
            cls._evidence_confidence_rules(),
            cls._output_format_section(
                schema, field_definitions, unresolved_field_keys,
            ),
        ]

        system_message = "\n\n".join(s for s in sections if s)
        expected_schema = cls._build_expected_schema(
            schema, field_definitions, unresolved_field_keys,
        )

        field_count = len(schema.get_all_field_keys())
        if unresolved_field_keys:
            field_count = len(unresolved_field_keys)

        return {
            "prompt_code": cls.PROMPT_CODE,
            "prompt_version": cls.PROMPT_VERSION,
            "system_message": system_message,
            "user_message_template": cls._user_message_template(),
            "expected_schema": expected_schema,
            "field_count": field_count,
        }

    @classmethod
    def build_user_message(cls, ocr_text: str) -> str:
        """Build the user message with OCR text."""
        return (
            "Extract all requested fields from the following document text. "
            "Return ONLY valid JSON.\n\n"
            "--- DOCUMENT TEXT ---\n"
            f"{ocr_text[:60000]}\n"
            "--- END DOCUMENT TEXT ---"
        )

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    @classmethod
    def _global_instructions(cls) -> str:
        return (
            "You are an expert document data extractor. Your task is to extract "
            "structured field values from OCR text of a financial document.\n\n"
            "RULES — follow these strictly:\n"
            "1. Return ONLY valid JSON matching the schema described below.\n"
            "2. Do NOT invent or hallucinate values. If a field is not present "
            "in the document text, set its value to null.\n"
            "3. For each extracted field, provide:\n"
            '   - "value": the extracted value\n'
            '   - "confidence": a float 0.0–1.0 indicating extraction certainty\n'
            '   - "evidence": the exact text snippet from the document that '
            "supports the extracted value (max 120 characters)\n"
            "4. Preserve original formatting for IDs, dates, and tax numbers.\n"
            "5. For monetary amounts, return the numeric value as a string "
            "without currency symbols or thousand separators.\n"
            "6. Do NOT include any explanation, commentary, or markdown."
        )

    @classmethod
    def _country_regime_instructions(
        cls,
        country_code: str,
        regime_code: str,
        jurisdiction_profile: Optional[TaxJurisdictionProfile],
    ) -> str:
        lines = [f"JURISDICTION CONTEXT: Country={country_code}, Tax Regime={regime_code}"]

        if jurisdiction_profile:
            lines.append(
                f"Tax ID Label: {jurisdiction_profile.tax_id_label}"
            )
            if jurisdiction_profile.default_currency:
                lines.append(
                    f"Expected Currency: {jurisdiction_profile.default_currency}"
                )
            if jurisdiction_profile.date_formats:
                lines.append(
                    f"Date Formats: {', '.join(jurisdiction_profile.date_formats)}"
                )
            config = jurisdiction_profile.config_json or {}
            if config.get("extraction_notes"):
                lines.append(f"Notes: {config['extraction_notes']}")

        return "\n".join(lines)

    @classmethod
    def _document_type_instructions(cls, document_type: str) -> str:
        return (
            f"DOCUMENT TYPE: {document_type}\n"
            f"Extract fields according to the schema below for a {document_type} document."
        )

    @classmethod
    def _schema_fields_section(
        cls,
        schema: ExtractionSchemaDefinition,
        field_definitions: Optional[list],
        unresolved_field_keys: Optional[set[str]],
    ) -> str:
        lines = ["FIELDS TO EXTRACT:"]

        header_keys = set(schema.header_fields_json or [])
        tax_keys = set(schema.tax_fields_json or [])
        line_item_keys = set(schema.line_item_fields_json or [])

        fd_map = {}
        if field_definitions:
            fd_map = {fd.field_key: fd for fd in field_definitions}

        def _describe_field(key: str) -> str:
            fd = fd_map.get(key)
            if fd:
                mandatory = " [REQUIRED]" if fd.is_mandatory else ""
                return f"  - {fd.field_key}: {fd.display_name} ({fd.data_type}){mandatory}"
            return f"  - {key}"

        def _should_include(key: str) -> bool:
            return unresolved_field_keys is None or key in unresolved_field_keys

        header_fields = [k for k in (schema.header_fields_json or []) if _should_include(k)]
        if header_fields:
            lines.append("\n[Header Fields]")
            for key in header_fields:
                lines.append(_describe_field(key))

        tax_fields = [k for k in (schema.tax_fields_json or []) if _should_include(k)]
        if tax_fields:
            lines.append("\n[Tax Fields]")
            for key in tax_fields:
                lines.append(_describe_field(key))

        li_fields = [k for k in (schema.line_item_fields_json or []) if _should_include(k)]
        if li_fields:
            lines.append("\n[Line Item Fields] — extract for EACH line item")
            for key in li_fields:
                lines.append(_describe_field(key))

        return "\n".join(lines)

    @classmethod
    def _tax_instructions(
        cls,
        regime_code: str,
        jurisdiction_profile: Optional[TaxJurisdictionProfile],
        field_definitions: Optional[list],
    ) -> str:
        lines = ["TAX EXTRACTION GUIDANCE:"]

        if regime_code:
            lines.append(f"Tax regime: {regime_code}")

        if jurisdiction_profile:
            config = jurisdiction_profile.config_json or {}
            if config.get("tax_extraction_notes"):
                lines.append(config["tax_extraction_notes"])
            if jurisdiction_profile.tax_id_regex:
                lines.append(
                    f"Tax ID format regex: {jurisdiction_profile.tax_id_regex}"
                )

        tax_fields = [
            fd for fd in (field_definitions or []) if fd.is_tax_field
        ]
        if tax_fields:
            lines.append(f"Tax-specific fields ({len(tax_fields)}):")
            for fd in tax_fields:
                lines.append(f"  - {fd.field_key}: {fd.display_name}")

        return "\n".join(lines) if len(lines) > 1 else ""

    @classmethod
    def _evidence_confidence_rules(cls) -> str:
        return (
            "EVIDENCE AND CONFIDENCE RULES:\n"
            "- For each field, provide the exact text snippet from the document "
            "that supports your extraction (max 120 chars).\n"
            "- Set confidence to 1.0 only when the value is unambiguously "
            "present in the document text.\n"
            "- Set confidence to 0.7–0.9 when the value requires inference "
            "or is partially visible.\n"
            "- Set confidence to 0.3–0.6 when the value is ambiguous or "
            "derived from context.\n"
            "- Set confidence to 0.0 and value to null when the field is "
            "not found.\n"
            "- Evidence snippets must be verbatim from the document — do "
            "not paraphrase."
        )

    @classmethod
    def _output_format_section(
        cls,
        schema: ExtractionSchemaDefinition,
        field_definitions: Optional[list],
        unresolved_field_keys: Optional[set[str]],
    ) -> str:
        expected = cls._build_expected_schema(
            schema, field_definitions, unresolved_field_keys,
        )
        return (
            "OUTPUT FORMAT:\n"
            "Return a JSON object with this structure:\n"
            f"```json\n{json.dumps(expected, indent=2)}\n```\n"
            "Each field value should be an object with: "
            '{"value": <extracted_value>, "confidence": <float>, "evidence": "<snippet>"}'
        )

    @classmethod
    def _user_message_template(cls) -> str:
        return (
            "Extract all requested fields from the following document text. "
            "Return ONLY valid JSON.\n\n"
            "--- DOCUMENT TEXT ---\n{ocr_text}\n--- END DOCUMENT TEXT ---"
        )

    # ------------------------------------------------------------------
    # Schema building
    # ------------------------------------------------------------------

    @classmethod
    def _build_expected_schema(
        cls,
        schema: ExtractionSchemaDefinition,
        field_definitions: Optional[list],
        unresolved_field_keys: Optional[set[str]],
    ) -> dict:
        """Build the expected JSON schema for the LLM response."""

        def _should_include(key: str) -> bool:
            return unresolved_field_keys is None or key in unresolved_field_keys

        field_example = {"value": None, "confidence": 0.0, "evidence": ""}

        result: dict = {}

        header_keys = [
            k for k in (schema.header_fields_json or []) if _should_include(k)
        ]
        if header_keys:
            result["header_fields"] = {k: field_example for k in header_keys}

        tax_keys = [
            k for k in (schema.tax_fields_json or []) if _should_include(k)
        ]
        if tax_keys:
            result["tax_fields"] = {k: field_example for k in tax_keys}

        li_keys = [
            k for k in (schema.line_item_fields_json or []) if _should_include(k)
        ]
        if li_keys:
            result["line_items"] = [
                {k: field_example for k in li_keys},
            ]

        return result
