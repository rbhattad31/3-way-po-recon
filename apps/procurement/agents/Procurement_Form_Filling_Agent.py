"""Procurement Form Filling Agent.

Transforms Azure Document Intelligence extraction output into a normalized
payload usable by procurement request prefill mapping.
"""
from __future__ import annotations

from typing import Any, Dict, List


class ProcurementFormFillingAgent:
	"""Normalize extracted document content into form-like structured fields."""

	@classmethod
	def fill_form(cls, *, extraction_output: Dict[str, Any], source_doc_type: str = "") -> Dict[str, Any]:
		"""Build canonical payload for request prefill mapping.

		Returns a dict with keys expected by AttributeMappingService.map_request_fields:
		- fields: {field_name: {value, confidence}}
		- attributes: [{key, value, confidence}]
		- requirements: [{key, value, confidence}] or list[str]
		- confidence: overall float
		"""
		extraction_output = extraction_output or {}
		overall_confidence = cls._coerce_confidence(
			extraction_output.get("confidence", 0.5),
		)

		# If DI/LLM already returned canonical fields, keep and sanitize them.
		if isinstance(extraction_output.get("fields"), dict):
			fields = cls._normalize_fields_dict(
				extraction_output.get("fields") or {},
				fallback_confidence=overall_confidence,
			)
			attributes = cls._normalize_key_value_list(
				extraction_output.get("attributes"),
				fallback_confidence=overall_confidence,
			)
			requirements = cls._normalize_key_value_list(
				extraction_output.get("requirements"),
				fallback_confidence=overall_confidence,
			)
			return {
				"fields": fields,
				"attributes": attributes,
				"requirements": requirements,
				"confidence": overall_confidence,
				"source_doc_type": source_doc_type or "",
				"agent": "procurement_form_filling",
			}

		# Common extraction shape from AzureDIExtractorAgent.
		header = extraction_output.get("header") or {}
		key_value_pairs = extraction_output.get("key_value_pairs") or []
		line_items = extraction_output.get("line_items") or []
		commercial_terms = extraction_output.get("commercial_terms") or {}

		fields = cls._normalize_fields_dict(header, fallback_confidence=overall_confidence)

		# Fold DI key-value pairs into canonical fields when possible.
		for pair in key_value_pairs:
			if not isinstance(pair, dict):
				continue
			raw_key = str(pair.get("key", "") or "").strip()
			raw_value = pair.get("value", "")
			if not raw_key or raw_value in (None, ""):
				continue
			key = cls._canonical_key(raw_key)
			if key not in fields:
				fields[key] = {
					"value": raw_value,
					"confidence": cls._coerce_confidence(pair.get("confidence", overall_confidence)),
				}

		attributes: List[Dict[str, Any]] = []
		requirements: List[Dict[str, Any]] = []

		if isinstance(commercial_terms, dict):
			for key, value in commercial_terms.items():
				if value in (None, ""):
					continue
				attributes.append(
					{
						"key": cls._canonical_key(str(key)),
						"value": value,
						"confidence": overall_confidence,
					},
				)

		if isinstance(line_items, list):
			for idx, item in enumerate(line_items, start=1):
				if not isinstance(item, dict):
					continue
				desc = item.get("description") or item.get("scope") or item.get("name")
				if not desc:
					continue
				requirements.append(
					{
						"key": f"requirement_{idx}",
						"value": str(desc).strip(),
						"confidence": cls._coerce_confidence(item.get("confidence", overall_confidence)),
					},
				)

		return {
			"fields": fields,
			"attributes": attributes,
			"requirements": requirements,
			"confidence": overall_confidence,
			"source_doc_type": source_doc_type or "",
			"agent": "procurement_form_filling",
		}

	@staticmethod
	def _normalize_fields_dict(raw_fields: Dict[str, Any], fallback_confidence: float) -> Dict[str, Dict[str, Any]]:
		normalized: Dict[str, Dict[str, Any]] = {}
		for raw_key, raw_value in (raw_fields or {}).items():
			if not str(raw_key).strip():
				continue
			key = ProcurementFormFillingAgent._canonical_key(str(raw_key))

			value = raw_value
			confidence = fallback_confidence
			if isinstance(raw_value, dict):
				value = raw_value.get("value", "")
				confidence = ProcurementFormFillingAgent._coerce_confidence(
					raw_value.get("confidence", fallback_confidence),
				)
			if value in (None, ""):
				continue

			normalized[key] = {
				"value": value,
				"confidence": confidence,
			}
		return normalized

	@staticmethod
	def _normalize_key_value_list(raw: Any, fallback_confidence: float) -> List[Dict[str, Any]]:
		items: List[Dict[str, Any]] = []
		if not isinstance(raw, list):
			return items

		for idx, entry in enumerate(raw, start=1):
			if isinstance(entry, dict):
				key = str(entry.get("key") or entry.get("name") or f"field_{idx}").strip()
				value = entry.get("value", "")
				confidence = ProcurementFormFillingAgent._coerce_confidence(
					entry.get("confidence", fallback_confidence),
				)
				if key and value not in (None, ""):
					items.append({"key": ProcurementFormFillingAgent._canonical_key(key), "value": value, "confidence": confidence})
			elif isinstance(entry, str) and entry.strip():
				items.append({"key": f"field_{idx}", "value": entry.strip(), "confidence": fallback_confidence})
		return items

	@staticmethod
	def _canonical_key(value: str) -> str:
		key = (value or "").strip().lower()
		key = key.replace("-", "_").replace(" ", "_")
		while "__" in key:
			key = key.replace("__", "_")
		return key

	@staticmethod
	def _coerce_confidence(value: Any) -> float:
		try:
			parsed = float(value)
			if parsed < 0.0:
				return 0.0
			if parsed > 1.0:
				return 1.0
			return parsed
		except (TypeError, ValueError):
			return 0.5
