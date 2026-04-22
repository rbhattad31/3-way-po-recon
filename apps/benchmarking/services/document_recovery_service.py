"""Utilities for recovering missing benchmarking quotation document links."""

from __future__ import annotations

import os
import re
from typing import List

from django.conf import settings
from django.utils.text import slugify

from apps.benchmarking.services.blob_storage_service import BlobStorageService


class BenchmarkDocumentRecoveryService:
    """Recover missing blob/local document pointers for BenchmarkQuotation rows."""

    @staticmethod
    def quotation_has_document_source(quotation) -> bool:
        return bool(
            (quotation.blob_name or "").strip()
            or (quotation.blob_url or "").strip()
            or getattr(quotation, "document", None)
        )

    @staticmethod
    def _tokenize_for_match(value: str) -> List[str]:
        text = (value or "").strip().lower()
        if not text:
            return []
        parts = re.split(r"[^a-z0-9]+", text)
        return [part for part in parts if len(part) >= 3]

    @classmethod
    def _recover_local_document_name(cls, quotation) -> str:
        media_root = getattr(settings, "MEDIA_ROOT", "")
        if not media_root:
            return ""

        request_slug = slugify(getattr(quotation.request, "title", "") or "")
        if not request_slug:
            return ""

        preferred_dir = os.path.join(media_root, "benchmarking", request_slug, "quotations")
        search_files = []
        if os.path.isdir(preferred_dir):
            for name in os.listdir(preferred_dir):
                if name.lower().endswith(".pdf"):
                    search_files.append(os.path.join(preferred_dir, name))

        if not search_files:
            fallback_root = os.path.join(media_root, "benchmarking")
            if not os.path.isdir(fallback_root):
                return ""
            for root, _directories, files in os.walk(fallback_root):
                for name in files:
                    if name.lower().endswith(".pdf"):
                        search_files.append(os.path.join(root, name))
                if len(search_files) >= 500:
                    break

        ref_tokens = cls._tokenize_for_match(getattr(quotation, "quotation_ref", "") or "")
        supplier_tokens = cls._tokenize_for_match(getattr(quotation, "supplier_name", "") or "")
        strong_tokens = ref_tokens + supplier_tokens
        request_tokens = cls._tokenize_for_match(getattr(quotation.request, "title", "") or "")

        best_path = ""
        best_score = -1
        for file_path in search_files:
            lower_path = file_path.lower().replace("\\", "/")
            request_score = sum(1 for token in request_tokens if token in lower_path)
            strong_score = sum(2 for token in strong_tokens if token in lower_path)
            score = request_score + strong_score
            if score > best_score:
                best_score = score
                best_path = file_path

        if best_score <= 0 or not best_path:
            return ""

        rel_path = os.path.relpath(best_path, media_root)
        return rel_path.replace("\\", "/")

    @classmethod
    def ensure_document_source(cls, quotation) -> bool:
        if cls.quotation_has_document_source(quotation):
            return True

        discovered = cls.discover_document_source(quotation)
        blob_name = discovered.get("blob_name", "")
        blob_url = discovered.get("blob_url", "")
        local_document_name = discovered.get("document_name", "")

        if blob_name:
            quotation.blob_name = blob_name
            if blob_url:
                quotation.blob_url = blob_url
            quotation.save(update_fields=["blob_name", "blob_url", "updated_at"])
            return True

        if local_document_name:
            quotation.document.name = local_document_name
            quotation.save(update_fields=["document", "updated_at"])
            return True

        return False

    @classmethod
    def discover_document_source(cls, quotation) -> dict:
        """Discover a candidate source without persisting any DB changes."""
        if cls.quotation_has_document_source(quotation):
            return {
                "blob_name": (quotation.blob_name or "").strip(),
                "blob_url": (quotation.blob_url or "").strip(),
                "document_name": getattr(getattr(quotation, "document", None), "name", "") or "",
            }

        request_title = getattr(quotation.request, "title", "") or ""
        blob_name, blob_url = BlobStorageService.find_best_blob_for_quotation(
            request_title=request_title,
            quotation_ref=getattr(quotation, "quotation_ref", "") or "",
            supplier_name=getattr(quotation, "supplier_name", "") or "",
        )
        local_document_name = cls._recover_local_document_name(quotation)
        return {
            "blob_name": blob_name,
            "blob_url": blob_url,
            "document_name": local_document_name,
        }
