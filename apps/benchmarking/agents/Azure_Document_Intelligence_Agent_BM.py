"""Azure Document Intelligence extraction agent for benchmarking quotations.

Runs OCR + table parsing and persists normalized BenchmarkLineItem rows.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from django.conf import settings

from apps.benchmarking.models import BenchmarkLineItem, BenchmarkQuotation
from apps.benchmarking.services.blob_storage_service import BlobStorageService


class AzureDocumentIntelligenceAgentBM:
	"""Extract line items from a benchmark quotation using Azure DI."""

	_NUMBER_RE = re.compile(r"-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?")

	@classmethod
	def extract_quotation(cls, *, quotation: BenchmarkQuotation) -> Dict[str, Any]:
		"""Extract OCR + line items for a single quotation and persist them."""
		if not quotation.blob_name:
			quotation.extraction_status = "FAILED"
			quotation.extraction_error = "Missing blob_name for quotation"
			quotation.save(update_fields=["extraction_status", "extraction_error", "updated_at"])
			return {"success": False, "error": "missing_blob_name", "line_count": 0}

		endpoint = str(getattr(settings, "AZURE_DI_ENDPOINT", "") or "").strip()
		key = str(getattr(settings, "AZURE_DI_KEY", "") or "").strip()
		if not endpoint or not key:
			quotation.extraction_status = "FAILED"
			quotation.extraction_error = "Azure DI credentials not configured (AZURE_DI_ENDPOINT / AZURE_DI_KEY)."
			quotation.save(update_fields=["extraction_status", "extraction_error", "updated_at"])
			return {"success": False, "error": "azure_di_not_configured", "line_count": 0}

		try:
			from azure.ai.formrecognizer import DocumentAnalysisClient
			from azure.core.credentials import AzureKeyCredential
		except Exception:
			quotation.extraction_status = "FAILED"
			quotation.extraction_error = "azure-ai-formrecognizer SDK not installed."
			quotation.save(update_fields=["extraction_status", "extraction_error", "updated_at"])
			return {"success": False, "error": "azure_di_sdk_missing", "line_count": 0}

		try:
			pdf_bytes = BlobStorageService.download_blob_bytes(quotation.blob_name)

			client = DocumentAnalysisClient(
				endpoint=endpoint,
				credential=AzureKeyCredential(key),
			)
			poller = client.begin_analyze_document("prebuilt-layout", pdf_bytes)
			result = poller.result()

			extracted_text = cls._collect_text(result)
			line_payloads = cls._extract_line_payloads(result)

			# Replace previously active line items on re-extraction.
			BenchmarkLineItem.objects.filter(quotation=quotation, is_active=True).update(
				is_active=False,
			)

			created_count = 0
			for index, payload in enumerate(line_payloads, start=1):
				BenchmarkLineItem.objects.create(
					quotation=quotation,
					tenant=quotation.tenant,
					description=payload.get("description", "")[:2000] or f"Line {index}",
					uom=(payload.get("uom") or "")[:50],
					quantity=payload.get("quantity"),
					quoted_unit_rate=payload.get("quoted_unit_rate"),
					line_amount=payload.get("line_amount"),
					line_number=index,
					extraction_confidence=float(payload.get("extraction_confidence", 0.7) or 0.7),
					classification_source="KEYWORD",
					category="UNCATEGORIZED",
					classification_confidence=0.0,
					variance_status="NEEDS_REVIEW",
					benchmark_source="NONE",
					created_by=quotation.created_by,
					updated_by=quotation.updated_by,
				)
				created_count += 1

			quotation.extracted_text = extracted_text[:100000]
			quotation.di_extraction_json = {
				"engine": "azure_di_prebuilt_layout",
				"line_count": created_count,
				"page_count": len(getattr(result, "pages", []) or []),
				"table_count": len(getattr(result, "tables", []) or []),
			}
			quotation.extraction_status = "DONE" if created_count > 0 else "FAILED"
			quotation.extraction_error = "" if created_count > 0 else "No line items detected in document."
			quotation.save(
				update_fields=[
					"extracted_text",
					"di_extraction_json",
					"extraction_status",
					"extraction_error",
					"updated_at",
				]
			)

			return {
				"success": created_count > 0,
				"error": "" if created_count > 0 else "no_line_items_detected",
				"line_count": created_count,
			}
		except Exception as exc:
			quotation.extraction_status = "FAILED"
			quotation.extraction_error = str(exc)[:2000]
			quotation.save(update_fields=["extraction_status", "extraction_error", "updated_at"])
			return {"success": False, "error": str(exc), "line_count": 0}

	@classmethod
	def _collect_text(cls, result: Any) -> str:
		lines: List[str] = []
		for page in getattr(result, "pages", []) or []:
			for line in getattr(page, "lines", []) or []:
				content = (getattr(line, "content", "") or "").strip()
				if content:
					lines.append(content)
		return "\n".join(lines)

	@classmethod
	def _extract_line_payloads(cls, result: Any) -> List[Dict[str, Any]]:
		payloads: List[Dict[str, Any]] = []

		for table in getattr(result, "tables", []) or []:
			row_map: Dict[int, Dict[int, str]] = {}
			for cell in getattr(table, "cells", []) or []:
				row_index = int(getattr(cell, "row_index", 0) or 0)
				col_index = int(getattr(cell, "column_index", 0) or 0)
				text = (getattr(cell, "content", "") or "").strip()
				if not text:
					continue
				row_map.setdefault(row_index, {})[col_index] = text

			for row_index in sorted(row_map.keys()):
				row_cells = [row_map[row_index][col] for col in sorted(row_map[row_index].keys())]
				parsed = cls._parse_row(row_cells)
				if parsed:
					payloads.append(parsed)

		if payloads:
			return payloads

		# Fallback: parse text lines when table extraction is weak.
		for page in getattr(result, "pages", []) or []:
			for line in getattr(page, "lines", []) or []:
				parsed = cls._parse_row([(getattr(line, "content", "") or "").strip()])
				if parsed:
					payloads.append(parsed)

		return payloads

	@classmethod
	def _parse_row(cls, row_cells: List[str]) -> Optional[Dict[str, Any]]:
		normalized = [c.strip() for c in row_cells if (c or "").strip()]
		if not normalized:
			return None

		row_text = " | ".join(normalized)
		lower = row_text.lower()
		if any(h in lower for h in ["description", "qty", "quantity", "unit rate", "amount", "total"]):
			return None

		numeric_candidates: List[Decimal] = []
		for cell in normalized:
			numeric_candidates.extend(cls._extract_numbers(cell))

		description = cls._pick_description(normalized)
		if not description:
			return None

		if not numeric_candidates and len(description) < 8:
			return None

		quantity = numeric_candidates[-3] if len(numeric_candidates) >= 3 else (numeric_candidates[0] if numeric_candidates else None)
		unit_rate = numeric_candidates[-2] if len(numeric_candidates) >= 2 else None
		amount = numeric_candidates[-1] if len(numeric_candidates) >= 1 else None

		# Guard against OCR noise rows.
		if quantity is None and unit_rate is None and amount is None:
			return None

		uom = cls._pick_uom(normalized)
		return {
			"description": description,
			"uom": uom,
			"quantity": quantity,
			"quoted_unit_rate": unit_rate,
			"line_amount": amount,
			"extraction_confidence": 0.7,
		}

	@classmethod
	def _extract_numbers(cls, text: str) -> List[Decimal]:
		values: List[Decimal] = []
		for token in cls._NUMBER_RE.findall(text or ""):
			cleaned = token.replace(",", "")
			try:
				values.append(Decimal(cleaned))
			except (InvalidOperation, ValueError):
				continue
		return values

	@staticmethod
	def _pick_description(cells: List[str]) -> str:
		candidates = []
		for cell in cells:
			plain = re.sub(r"\s+", " ", cell).strip()
			if not plain:
				continue
			has_alpha = any(ch.isalpha() for ch in plain)
			has_digit = any(ch.isdigit() for ch in plain)
			if has_alpha and (len(plain) >= 4):
				score = len(plain) + (20 if not has_digit else 0)
				candidates.append((score, plain))
		if not candidates:
			return ""
		candidates.sort(key=lambda pair: pair[0], reverse=True)
		return candidates[0][1]

	@staticmethod
	def _pick_uom(cells: List[str]) -> str:
		known = {
			"nos", "no", "ea", "each", "unit", "units", "set", "sets",
			"m", "m2", "m3", "rm", "kg", "ton", "tr", "lot", "job", "ls",
		}
		for cell in cells:
			token = (cell or "").strip().lower()
			compact = re.sub(r"[^a-z0-9]", "", token)
			if compact in known:
				return compact.upper()
			# Prefer very short alphabetic cells as potential UOM.
			if token.isalpha() and 1 <= len(token) <= 4:
				return token.upper()
		return ""
