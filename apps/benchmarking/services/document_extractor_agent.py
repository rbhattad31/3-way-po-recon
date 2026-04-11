"""
BenchmarkDocumentExtractorAgent

A strictly-scoped deterministic agent that processes a single BenchmarkQuotation
through the full extraction pipeline:

  Stage 1: Upload PDF to Azure Blob Storage (fail-silent)
  Stage 2: Extract text + tables using Azure Document Intelligence
  Stage 3: Use OpenAI to batch-classify every extracted line item using CategoryMaster
  Stage 4: Apply VarianceThresholdConfig to compute variance bands per line item
  Stage 5: Persist BenchmarkLineItem records with classification_source="AI"

Design principles:
  - No ReAct loop -- this is a deterministic pipeline agent, not a chat agent.
  - Each stage is independently try/except'd so one failure does not abort the rest.
  - Falls back to keyword-based ClassificationService when OpenAI is unavailable.
  - Emits structured logging at INFO level for every stage.
  - Never hard-codes categories -- reads CategoryMaster table at runtime.

Usage (from BenchmarkEngine or a Celery task):
    from apps.benchmarking.services.document_extractor_agent import BenchmarkDocumentExtractorAgent

    agent = BenchmarkDocumentExtractorAgent()
    result = agent.run(quotation_pk=42, bench_request_pk=7)
    # result["success"]       -> bool
    # result["line_items"]    -> list of saved BenchmarkLineItem pks
    # result["engine"]        -> "azure_di" | "pdfplumber_fallback"
    # result["stages"]        -> dict of per-stage outcomes
    # result["error"]         -> None | error message
"""
from __future__ import annotations

import json
import logging
import os
import time
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt templates (ASCII-only per project conventions)
# ---------------------------------------------------------------------------

_CLASSIFICATION_SYSTEM_PROMPT = (
    "You are a senior HVAC procurement analyst. "
    "Your task is to classify each line item description into exactly ONE "
    "category from the provided category list. "
    "Respond ONLY with a valid JSON array -- no prose, no markdown fences. "
    "Each element must have keys: line_number (int), category (str), confidence (float 0-1)."
)

_CLASSIFICATION_USER_PROMPT_TPL = """\
Classify each HVAC quotation line item below into one of these categories:

{category_block}

Line items to classify:
{line_items_block}

Return a JSON array like:
[
  {{"line_number": 1, "category": "EQUIPMENT", "confidence": 0.95}},
  ...
]
Only use category codes from the list above.
"""


class BenchmarkDocumentExtractorAgent:
    """
    Deterministic document extractor agent for benchmarking quotations.

    Orchestrates: Blob Upload -> Azure DI Extraction -> AI Classification
                  -> Variance Threshold Application -> DB Persistence.
    """

    def __init__(self):
        self.stages: dict = {}

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        quotation_pk: int,
        bench_request_pk: Optional[int] = None,
        user=None,
    ) -> dict:
        """
        Execute the full extraction pipeline for one BenchmarkQuotation.

        Args:
            quotation_pk     : PK of a BenchmarkQuotation record.
            bench_request_pk : PK of the parent BenchmarkRequest (optional).
            user             : Django User instance or None.

        Returns:
            {
                "success": bool,
                "line_items": list[int],   # saved BenchmarkLineItem PKs
                "engine": str,
                "stages": dict,            # per-stage outcome dict
                "error": str | None,
            }
        """
        t_start = time.time()
        self.stages = {}

        try:
            from apps.benchmarking.models import BenchmarkQuotation, BenchmarkRequest

            quotation = BenchmarkQuotation.objects.select_related("request").get(pk=quotation_pk)
            bench_request = quotation.request
        except Exception as exc:
            return {
                "success": False,
                "line_items": [],
                "engine": "none",
                "stages": {},
                "error": f"BenchmarkQuotation {quotation_pk} not found: {exc}",
            }

        logger.info(
            "BenchmarkDocumentExtractorAgent.run: start -- quotation_pk=%d request='%s'",
            quotation_pk,
            bench_request.title,
        )

        # ----------------------------------------------------------------
        # Stage 1: Upload to Azure Blob
        # ----------------------------------------------------------------
        blob_name, blob_url = self._stage_blob_upload(quotation)

        # ----------------------------------------------------------------
        # Stage 2: Azure DI extraction
        # ----------------------------------------------------------------
        extraction = self._stage_extract(quotation)
        engine = extraction.get("engine", "none")

        if not extraction.get("text"):
            quotation.extraction_status = "FAILED"
            quotation.extraction_error = extraction.get("error") or "Empty extraction result"
            quotation.save(update_fields=["extraction_status", "extraction_error", "updated_at"])
            return {
                "success": False,
                "line_items": [],
                "engine": engine,
                "stages": self.stages,
                "error": quotation.extraction_error,
            }

        # Store DI metadata on quotation
        quotation.extracted_text = extraction["text"][:50000]
        quotation.di_extraction_json = extraction.get("raw_json") or {}
        if blob_url:
            quotation.blob_url = blob_url
            quotation.blob_name = blob_name
        quotation.extraction_status = "DONE"
        quotation.extraction_error = ""
        quotation.save(
            update_fields=[
                "extracted_text",
                "di_extraction_json",
                "blob_url",
                "blob_name",
                "extraction_status",
                "extraction_error",
                "updated_at",
            ]
        )

        raw_line_items = extraction.get("line_items") or []
        if not raw_line_items:
            logger.warning(
                "BenchmarkDocumentExtractorAgent: No line items from extraction for quotation %d",
                quotation_pk,
            )
            return {
                "success": True,
                "line_items": [],
                "engine": engine,
                "stages": self.stages,
                "error": None,
            }

        # ----------------------------------------------------------------
        # Stage 3: AI classification
        # ----------------------------------------------------------------
        classifications = self._stage_classify(raw_line_items)

        # ----------------------------------------------------------------
        # Stage 4: Load variance thresholds
        # ----------------------------------------------------------------
        threshold_map = self._load_variance_thresholds(bench_request.geography)

        # ----------------------------------------------------------------
        # Stage 5: Persist BenchmarkLineItems
        # ----------------------------------------------------------------
        saved_pks = self._stage_persist(
            quotation,
            bench_request,
            raw_line_items,
            classifications,
            threshold_map,
            user=user,
        )

        total_ms = int((time.time() - t_start) * 1000)
        logger.info(
            "BenchmarkDocumentExtractorAgent.run: complete -- quotation_pk=%d "
            "line_items=%d engine=%s total=%dms",
            quotation_pk,
            len(saved_pks),
            engine,
            total_ms,
        )

        return {
            "success": True,
            "line_items": saved_pks,
            "engine": engine,
            "stages": self.stages,
            "error": None,
        }

    # ------------------------------------------------------------------
    # Stage 1: Blob upload
    # ------------------------------------------------------------------

    def _stage_blob_upload(self, quotation) -> tuple[str, str]:
        """Upload the quotation PDF to Azure Blob. Fail-silent."""
        stage_key = "blob_upload"
        try:
            from apps.benchmarking.services.blob_storage_service import BlobStorageService

            file_path = quotation.document.path
            filename = os.path.basename(file_path)
            request_ref = quotation.request.title[:40].replace(" ", "_")
            blob_name, blob_url = BlobStorageService.upload_quotation(
                file_path,
                filename=filename,
                request_ref=request_ref,
            )
            self.stages[stage_key] = {
                "status": "done" if blob_url else "skipped",
                "blob_name": blob_name,
                "blob_url": blob_url,
            }
            return blob_name, blob_url
        except Exception as exc:
            logger.warning("BenchmarkDocumentExtractorAgent: blob upload failed (non-fatal): %s", exc)
            self.stages[stage_key] = {"status": "failed", "error": str(exc)}
            return "", ""

    # ------------------------------------------------------------------
    # Stage 2: Extraction
    # ------------------------------------------------------------------

    def _stage_extract(self, quotation) -> dict:
        """Extract text and line items using AzureDIExtractionService."""
        stage_key = "extraction"
        try:
            from apps.benchmarking.services.azure_di_extraction_service import AzureDIExtractionService

            file_path = quotation.document.path
            result = AzureDIExtractionService.extract(file_path, source_name=os.path.basename(file_path))
            self.stages[stage_key] = {
                "status": "done",
                "engine": result.get("engine"),
                "page_count": result.get("page_count", 0),
                "line_items_found": len(result.get("line_items") or []),
                "duration_ms": result.get("duration_ms", 0),
            }
            return result
        except Exception as exc:
            logger.exception("BenchmarkDocumentExtractorAgent: extraction failed for quotation %d", quotation.pk)
            self.stages[stage_key] = {"status": "failed", "error": str(exc)}
            return {"text": "", "line_items": [], "raw_json": {}, "error": str(exc), "engine": "none"}

    # ------------------------------------------------------------------
    # Stage 3: AI classification
    # ------------------------------------------------------------------

    def _stage_classify(self, raw_line_items: list) -> dict:
        """
        Use OpenAI to classify each line item into a CategoryMaster category.
        Returns dict keyed by line_number -> {"category": str, "confidence": float, "source": str}
        Falls back to keyword-based classification on error.
        """
        stage_key = "ai_classification"

        # Load active categories from CategoryMaster
        categories = self._load_category_master()
        if not categories:
            # No CategoryMaster records -- use keyword classification only
            classifications = self._keyword_classify(raw_line_items)
            self.stages[stage_key] = {"status": "skipped", "reason": "no_category_master"}
            return classifications

        try:
            return self._openai_classify(raw_line_items, categories, stage_key)
        except Exception as exc:
            logger.warning(
                "BenchmarkDocumentExtractorAgent: OpenAI classification failed (non-fatal), "
                "falling back to keyword classification: %s",
                exc,
            )
            self.stages[stage_key] = {"status": "fallback_keywords", "error": str(exc)}
            return self._keyword_classify(raw_line_items)

    def _load_category_master(self) -> list[dict]:
        """Return active CategoryMaster records as plain dicts."""
        try:
            from apps.benchmarking.models import CategoryMaster

            return list(
                CategoryMaster.objects.filter(is_active=True)
                .order_by("sort_order", "code")
                .values("code", "name", "description", "keywords_csv")
            )
        except Exception as exc:
            logger.warning("BenchmarkDocumentExtractorAgent: could not load CategoryMaster: %s", exc)
            return []

    def _openai_classify(self, raw_line_items: list, categories: list[dict], stage_key: str) -> dict:
        """Call OpenAI to classify line items into CategoryMaster categories."""
        from apps.agents.services.llm_client import LLMClient, LLMMessage

        # Build category block for prompt
        cat_lines = []
        for cat in categories:
            kw_hint = f" (keywords: {cat['keywords_csv']})" if cat.get("keywords_csv") else ""
            cat_lines.append(f"  {cat['code']}: {cat['name']} -- {cat['description']}{kw_hint}")
        category_block = "\n".join(cat_lines)

        # Build line items block (truncate description to 120 chars for token efficiency)
        item_lines = []
        for item in raw_line_items:
            desc = (item.get("description") or "")[:120]
            item_lines.append(f"  {item['line_number']}. {desc}")
        line_items_block = "\n".join(item_lines)

        user_prompt = _CLASSIFICATION_USER_PROMPT_TPL.format(
            category_block=category_block,
            line_items_block=line_items_block,
        )

        llm = LLMClient(temperature=0.0, max_tokens=2048)
        messages = [
            LLMMessage(role="system", content=_CLASSIFICATION_SYSTEM_PROMPT),
            LLMMessage(role="user", content=user_prompt),
        ]
        resp = llm.chat(messages, response_format={"type": "json_object"})
        raw = (resp.content or "").strip()

        # Parse the JSON response
        # The prompt asks for a JSON array directly, but response_format=json_object
        # may wrap it. Handle both.
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                # Might be wrapped: {"items": [...]} or {"results": [...]}
                for key in ("items", "results", "classifications", "line_items"):
                    if isinstance(parsed.get(key), list):
                        parsed = parsed[key]
                        break
            if not isinstance(parsed, list):
                raise ValueError("Expected a JSON array from OpenAI classification")
        except Exception as parse_exc:
            raise RuntimeError(f"JSON parse error in OpenAI classification response: {parse_exc} | raw={raw[:200]}")

        # Build lookup dict
        result = {}
        valid_codes = {c["code"] for c in categories}
        DEFAULT_FALLBACK = categories[0]["code"] if categories else "UNCATEGORIZED"

        for obj in parsed:
            ln = int(obj.get("line_number", 0))
            cat = (obj.get("category") or DEFAULT_FALLBACK).upper()
            if cat not in valid_codes:
                cat = DEFAULT_FALLBACK
            result[ln] = {
                "category": cat,
                "confidence": float(obj.get("confidence", 0.80)),
                "source": "AI",
            }

        self.stages[stage_key] = {
            "status": "done",
            "classified": len(result),
            "model": "gpt-4o",
        }
        return result

    def _keyword_classify(self, raw_line_items: list) -> dict:
        """Fall-back: use ClassificationService keyword rules."""
        from apps.benchmarking.services.classification_service import ClassificationService

        result = {}
        for item in raw_line_items:
            ln = item.get("line_number", 0)
            cls_result = ClassificationService.classify(item.get("description", ""))
            result[ln] = {
                "category": cls_result["category"],
                "confidence": cls_result["confidence"],
                "source": "KEYWORD",
            }
        return result

    # ------------------------------------------------------------------
    # Stage 4: Load variance thresholds
    # ------------------------------------------------------------------

    def _load_variance_thresholds(self, geography: str) -> dict:
        """
        Return a dict mapping category -> (within_range_max, moderate_max).
        Priority: category+geo > category+ALL > ALL+ALL
        """
        try:
            from apps.benchmarking.models import VarianceThresholdConfig

            configs = list(
                VarianceThresholdConfig.objects.filter(is_active=True).values(
                    "category", "geography", "within_range_max_pct", "moderate_max_pct"
                )
            )
        except Exception as exc:
            logger.warning(
                "BenchmarkDocumentExtractorAgent: could not load VarianceThresholdConfig: %s", exc
            )
            return {}

        # Build priority-ordered lookup
        # Lower number = higher priority
        def priority(cfg):
            geo_match = cfg["geography"] == geography
            is_all_geo = cfg["geography"] == "ALL"
            is_all_cat = cfg["category"] == "ALL"
            if not is_all_cat and geo_match:
                return 0
            if not is_all_cat and is_all_geo:
                return 1
            if is_all_cat and geo_match:
                return 2
            if is_all_cat and is_all_geo:
                return 3
            return 99  # irrelevant geo

        configs.sort(key=priority)

        from apps.benchmarking.models import LineCategory

        threshold_map = {}
        all_categories = {code for code, _ in LineCategory.CHOICES + [("UNCATEGORIZED", "Uncategorized")]}
        all_categories.add("ALL")

        for cat_code in all_categories:
            if cat_code == "ALL":
                continue
            # Walk configs in priority order to find best match
            for cfg in configs:
                if cfg["category"] in (cat_code, "ALL"):
                    geo_ok = cfg["geography"] in (geography, "ALL")
                    if geo_ok:
                        threshold_map[cat_code] = (
                            float(cfg["within_range_max_pct"]),
                            float(cfg["moderate_max_pct"]),
                        )
                        break
            if cat_code not in threshold_map:
                # Absolute fallback
                threshold_map[cat_code] = (5.0, 15.0)

        self.stages["variance_thresholds"] = {
            "status": "done",
            "threshold_count": len(threshold_map),
            "geography": geography,
        }
        return threshold_map

    # ------------------------------------------------------------------
    # Stage 5: Persist
    # ------------------------------------------------------------------

    def _stage_persist(
        self,
        quotation,
        bench_request,
        raw_line_items: list,
        classifications: dict,
        threshold_map: dict,
        user=None,
    ) -> list[int]:
        """Delete old line items and save newly classified ones."""
        from apps.benchmarking.models import BenchmarkLineItem, VarianceStatus
        from apps.benchmarking.services.benchmark_service import CorridorLookupService

        # Delete previous line items for this quotation
        quotation.line_items.all().delete()

        saved_pks = []
        for raw in raw_line_items:
            ln = raw.get("line_number", 0)
            cls_info = classifications.get(ln, {"category": "UNCATEGORIZED", "confidence": 0.0, "source": "KEYWORD"})
            category = cls_info["category"]
            cls_confidence = cls_info["confidence"]
            cls_source = cls_info["source"]

            # Corridor lookup
            corridor = CorridorLookupService.find_corridor(
                category=category,
                geography=bench_request.geography,
                scope_type=bench_request.scope_type,
                description=raw.get("description", ""),
            )

            quoted_rate = raw.get("unit_rate")
            quantity = raw.get("quantity")
            amount = raw.get("amount")

            # Compute line_amount from quoted_rate * quantity if not present
            if amount is None and quoted_rate is not None and quantity is not None:
                try:
                    amount = Decimal(str(float(quoted_rate) * float(quantity)))
                except Exception:
                    amount = None

            variance_pct = None
            variance_status = VarianceStatus.NEEDS_REVIEW
            variance_note = ""
            bench_min = bench_mid = bench_max = None
            corridor_code = ""

            if corridor and quoted_rate is not None:
                bench_min = corridor.min_rate
                bench_mid = corridor.mid_rate
                bench_max = corridor.max_rate
                corridor_code = corridor.rule_code
                try:
                    mid = float(corridor.mid_rate)
                    q_rate = float(quoted_rate)
                    if mid > 0:
                        variance_pct = round((q_rate - mid) / mid * 100, 2)
                        within_max, moderate_max = threshold_map.get(category, (5.0, 15.0))
                        abs_v = abs(variance_pct)
                        if abs_v < within_max:
                            variance_status = VarianceStatus.WITHIN_RANGE
                        elif abs_v < moderate_max:
                            variance_status = VarianceStatus.MODERATE
                        else:
                            variance_status = VarianceStatus.HIGH
                        variance_note = (
                            f"Quoted {variance_pct:+.1f}% vs benchmark mid AED {mid:,.0f}; "
                            f"status={variance_status}"
                        )
                except Exception as calc_exc:
                    logger.debug("Variance calc failed for line %d: %s", ln, calc_exc)

            item = BenchmarkLineItem(
                quotation=quotation,
                line_number=ln,
                description=raw.get("description", ""),
                uom=raw.get("uom", ""),
                quantity=quantity,
                quoted_unit_rate=quoted_rate,
                line_amount=amount,
                extraction_confidence=raw.get("extraction_confidence", 0.0),
                classification_source=cls_source,
                category=category,
                classification_confidence=cls_confidence,
                benchmark_min=bench_min,
                benchmark_mid=bench_mid,
                benchmark_max=bench_max,
                corridor_rule_code=corridor_code,
                variance_pct=variance_pct,
                variance_status=variance_status,
                variance_note=variance_note,
                benchmark_source="CORRIDOR_DB" if corridor else "NONE",
                is_active=True,
            )
            if user:
                item.created_by = user
            item.save()
            saved_pks.append(item.pk)

        self.stages["persist"] = {
            "status": "done",
            "saved": len(saved_pks),
        }
        return saved_pks
