"""
PromptBuilderService — Dynamically builds LLM prompts for document extraction.

Takes an ExtractionTemplate (schema-driven field specs) and an optional
TaxJurisdictionProfile to produce system + user messages that instruct
the LLM to extract fields as strict JSON.

Design:
    - Zero hardcoded country-specific prompts — everything is derived
      from schema field definitions and jurisdiction profile config_json
    - Schema field specs drive the expected output JSON structure
    - Jurisdiction profile drives tax-specific extraction guidance
    - Returns a list of LLMMessage objects ready for LLMClient.chat()
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from apps.agents.services.llm_client import LLMMessage
from apps.extraction_core.models import TaxJurisdictionProfile
from apps.extraction_core.services.extraction_service import (
    ExtractionTemplate,
    FieldSpec,
)

logger = logging.getLogger(__name__)


class PromptBuilderService:
    """Builds structured LLM prompts for schema-driven field extraction."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def build_messages(
        cls,
        template: ExtractionTemplate,
        ocr_text: str,
        jurisdiction_profile: Optional[TaxJurisdictionProfile] = None,
        unresolved_field_keys: Optional[set[str]] = None,
    ) -> list[LLMMessage]:
        """
        Build the full message list for an extraction LLM call.

        Parameters
        ----------
        template : ExtractionTemplate
            Schema-driven template defining all fields to extract.
        ocr_text : str
            Raw OCR text of the document.
        jurisdiction_profile : TaxJurisdictionProfile, optional
            Jurisdiction metadata for tax-specific guidance.
        unresolved_field_keys : set[str], optional
            If provided, only these field keys are requested from the LLM
            (used in hybrid mode where deterministic already resolved some).

        Returns
        -------
        list[LLMMessage]
            System message + user message ready for LLMClient.chat().
        """
        system_content = cls._build_system_prompt(
            template, jurisdiction_profile, unresolved_field_keys,
        )
        user_content = cls._build_user_prompt(ocr_text)

        return [
            LLMMessage(role="system", content=system_content),
            LLMMessage(role="user", content=user_content),
        ]

    @classmethod
    def build_expected_schema(
        cls,
        template: ExtractionTemplate,
        unresolved_field_keys: Optional[set[str]] = None,
    ) -> dict:
        """
        Build the JSON schema describing the expected LLM output.

        Used for response_format (structured output) and for validation.
        """
        header_specs = cls._filter_specs(
            template.header_fields, unresolved_field_keys,
        )
        tax_specs = cls._filter_specs(
            template.tax_fields, unresolved_field_keys,
        )
        line_item_specs = cls._filter_specs(
            template.line_item_fields, unresolved_field_keys,
        )

        schema: dict = {"type": "object", "properties": {}}

        if header_specs:
            schema["properties"]["header_fields"] = {
                "type": "object",
                "properties": {
                    s.field_key: cls._field_json_type(s) for s in header_specs
                },
            }
        if tax_specs:
            schema["properties"]["tax_fields"] = {
                "type": "object",
                "properties": {
                    s.field_key: cls._field_json_type(s) for s in tax_specs
                },
            }
        if line_item_specs:
            line_props = {
                s.field_key: cls._field_json_type(s) for s in line_item_specs
            }
            schema["properties"]["line_items"] = {
                "type": "array",
                "items": {"type": "object", "properties": line_props},
            }

        return schema

    # ------------------------------------------------------------------
    # System prompt building
    # ------------------------------------------------------------------

    @classmethod
    def _build_system_prompt(
        cls,
        template: ExtractionTemplate,
        jurisdiction_profile: Optional[TaxJurisdictionProfile],
        unresolved_field_keys: Optional[set[str]],
    ) -> str:
        sections: list[str] = [
            cls._global_instructions(),
            cls._field_definitions_section(template, unresolved_field_keys),
        ]

        if jurisdiction_profile:
            tax_section = cls._jurisdiction_section(jurisdiction_profile)
            if tax_section:
                sections.append(tax_section)

        sections.append(cls._output_format_section(template, unresolved_field_keys))

        return "\n\n".join(sections)

    @classmethod
    def _global_instructions(cls) -> str:
        return (
            "You are an expert document data extractor. Your task is to extract "
            "structured field values from OCR text of a financial document.\n\n"
            "RULES — follow these strictly:\n"
            "1. Return ONLY valid JSON matching the schema described below.\n"
            "2. Do NOT invent or hallucinate values. If a field is not present "
            "in the document text, set its value to null.\n"
            "3. For each extracted field, also provide:\n"
            '   - "confidence": a float 0.0–1.0 indicating extraction certainty\n'
            '   - "evidence": the exact text snippet from the document that '
            "supports the extracted value (max 120 characters)\n"
            "4. Preserve original formatting for IDs, dates, and tax numbers — "
            "do not reformat them.\n"
            "5. For monetary amounts, return the numeric value as a string "
            "without currency symbols or thousand separators.\n"
            "6. Do NOT include any explanation, commentary, or markdown — "
            "only the JSON object."
        )

    @classmethod
    def _field_definitions_section(
        cls,
        template: ExtractionTemplate,
        unresolved_field_keys: Optional[set[str]],
    ) -> str:
        lines = ["FIELDS TO EXTRACT:"]

        header_specs = cls._filter_specs(
            template.header_fields, unresolved_field_keys,
        )
        tax_specs = cls._filter_specs(
            template.tax_fields, unresolved_field_keys,
        )
        line_item_specs = cls._filter_specs(
            template.line_item_fields, unresolved_field_keys,
        )

        if header_specs:
            lines.append("\n[Header Fields]")
            for s in header_specs:
                lines.append(cls._describe_field(s))

        if tax_specs:
            lines.append("\n[Tax Fields]")
            for s in tax_specs:
                lines.append(cls._describe_field(s))

        if line_item_specs:
            lines.append("\n[Line Item Fields] — extract for EACH line item")
            for s in line_item_specs:
                lines.append(cls._describe_field(s))

        return "\n".join(lines)

    @classmethod
    def _describe_field(cls, spec: FieldSpec) -> str:
        parts = [f"- {spec.field_key} ({spec.display_name})"]
        parts.append(f"  type={spec.data_type}")
        if spec.is_mandatory:
            parts.append("  MANDATORY")
        if spec.aliases:
            parts.append(f"  aliases: {', '.join(spec.aliases)}")
        return " | ".join(parts)

    @classmethod
    def _jurisdiction_section(
        cls, profile: TaxJurisdictionProfile,
    ) -> str:
        """
        Build tax-specific extraction guidance from the jurisdiction profile.

        Everything is derived from the profile fields and config_json —
        no hardcoded country logic.
        """
        lines = [
            f"JURISDICTION CONTEXT: {profile.country_name} — {profile.tax_regime}"
        ]

        if profile.regime_full_name:
            lines.append(f"Tax regime: {profile.regime_full_name}")

        lines.append(f"Currency: {profile.default_currency}")

        if profile.tax_id_label:
            lines.append(
                f"Tax ID: Look for '{profile.tax_id_label}' — this is the "
                f"primary tax registration identifier in {profile.country_name}."
            )

        if profile.date_formats:
            lines.append(
                f"Expected date formats: {', '.join(profile.date_formats)}"
            )

        # Derive guidance from config_json (tax_components, rates, flags, etc.)
        config = profile.config_json or {}

        tax_components = config.get("tax_components", [])
        if tax_components:
            lines.append(
                "Tax components to look for: "
                + ", ".join(str(c) for c in tax_components)
            )

        standard_rate = config.get("standard_vat_rate") or config.get(
            "standard_tax_rate",
        )
        if standard_rate is not None:
            lines.append(f"Standard tax rate: {standard_rate}%")

        if config.get("has_state_level_tax"):
            lines.append(
                "This jurisdiction uses state-level taxes — look for "
                "separate state and central tax components."
            )

        if config.get("reverse_charge_supported"):
            lines.append(
                "Reverse charge mechanism is applicable — check for "
                "reverse charge indicators or flags."
            )

        if config.get("e_invoicing_mandatory"):
            lines.append(
                "E-invoicing is mandatory — look for IRN (Invoice Reference "
                "Number), QR codes, or e-invoice portal references."
            )

        if config.get("zatca_compliant"):
            lines.append(
                "ZATCA e-invoicing compliance — look for UUID, QR code, "
                "and cryptographic stamp fields."
            )

        # Pass through any additional hints stored in config
        extra_hints = config.get("extraction_hints", [])
        for hint in extra_hints:
            lines.append(f"Note: {hint}")

        return "\n".join(lines)

    @classmethod
    def _output_format_section(
        cls,
        template: ExtractionTemplate,
        unresolved_field_keys: Optional[set[str]],
    ) -> str:
        """Describe the expected JSON output structure with an example."""
        header_specs = cls._filter_specs(
            template.header_fields, unresolved_field_keys,
        )
        tax_specs = cls._filter_specs(
            template.tax_fields, unresolved_field_keys,
        )
        line_item_specs = cls._filter_specs(
            template.line_item_fields, unresolved_field_keys,
        )

        example: dict = {}

        if header_specs:
            example["header_fields"] = {}
            sample = header_specs[0]
            example["header_fields"][sample.field_key] = {
                "value": "<extracted value or null>",
                "confidence": 0.95,
                "evidence": "<source text snippet>",
            }
            if len(header_specs) > 1:
                example["header_fields"]["..."] = "..."

        if tax_specs:
            example["tax_fields"] = {}
            sample = tax_specs[0]
            example["tax_fields"][sample.field_key] = {
                "value": "<extracted value or null>",
                "confidence": 0.90,
                "evidence": "<source text snippet>",
            }
            if len(tax_specs) > 1:
                example["tax_fields"]["..."] = "..."

        if line_item_specs:
            item_example: dict = {}
            for s in line_item_specs[:2]:
                item_example[s.field_key] = {
                    "value": "<extracted value or null>",
                    "confidence": 0.85,
                    "evidence": "<source text snippet>",
                }
            example["line_items"] = [item_example]

        lines = [
            "OUTPUT FORMAT — return exactly this JSON structure:",
            "```json",
            json.dumps(example, indent=2),
            "```",
            "Each field value MUST be an object with 'value', 'confidence', "
            "and 'evidence' keys.",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # User prompt
    # ------------------------------------------------------------------

    @classmethod
    def _build_user_prompt(cls, ocr_text: str) -> str:
        # Truncate to avoid exceeding context limits
        max_chars = 60000
        text = ocr_text[:max_chars]
        if len(ocr_text) > max_chars:
            text += "\n\n[... text truncated ...]"

        return (
            "Extract the requested fields from the following document text.\n\n"
            "--- DOCUMENT TEXT START ---\n"
            f"{text}\n"
            "--- DOCUMENT TEXT END ---"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @classmethod
    def _filter_specs(
        cls,
        specs: list[FieldSpec],
        unresolved_keys: Optional[set[str]],
    ) -> list[FieldSpec]:
        """Filter specs to only unresolved keys if provided."""
        if unresolved_keys is None:
            return specs
        return [s for s in specs if s.field_key in unresolved_keys]

    @classmethod
    def _field_json_type(cls, spec: FieldSpec) -> dict:
        """Map FieldSpec data_type to JSON schema type hint."""
        type_map = {
            "STRING": "string",
            "NUMBER": "number",
            "DECIMAL": "number",
            "INTEGER": "integer",
            "DATE": "string",
            "BOOLEAN": "boolean",
            "CURRENCY": "string",
        }
        return {"type": type_map.get(spec.data_type, "string")}
