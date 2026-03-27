"""Service for managing ExtractionPromptTemplate."""
from __future__ import annotations

import difflib
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone

from apps.extraction_core.models import ExtractionPromptTemplate
from apps.extraction_core.services.extraction_audit import ExtractionAuditService


class PromptRegistryService:
    """Stateless service for extraction prompt template lifecycle."""

    @classmethod
    def list_prompts(cls, filters: dict | None = None) -> QuerySet:
        qs = ExtractionPromptTemplate.objects.all()
        if not filters:
            return qs
        if filters.get("prompt_code"):
            qs = qs.filter(prompt_code__icontains=filters["prompt_code"])
        if filters.get("prompt_category"):
            qs = qs.filter(prompt_category__iexact=filters["prompt_category"])
        if filters.get("country_code"):
            qs = qs.filter(country_code__iexact=filters["country_code"])
        if filters.get("regime_code"):
            qs = qs.filter(regime_code__iexact=filters["regime_code"])
        if filters.get("document_type"):
            qs = qs.filter(document_type__iexact=filters["document_type"])
        if filters.get("status"):
            qs = qs.filter(status__iexact=filters["status"])
        if filters.get("search"):
            qs = qs.filter(prompt_code__icontains=filters["search"])
        return qs

    @classmethod
    def get_prompt_detail(cls, pk: int) -> ExtractionPromptTemplate | None:
        return ExtractionPromptTemplate.objects.filter(pk=pk).first()

    @classmethod
    def create_prompt(cls, data: dict, user) -> ExtractionPromptTemplate:
        prompt = ExtractionPromptTemplate(
            prompt_code=data["prompt_code"],
            prompt_category=data.get("prompt_category", "extraction"),
            country_code=data.get("country_code", ""),
            regime_code=data.get("regime_code", ""),
            document_type=data.get("document_type", ""),
            schema_code=data.get("schema_code", ""),
            version=1,
            status="DRAFT",
            prompt_text=data.get("prompt_text", ""),
            variables_json=data.get("variables_json", []),
            effective_from=data.get("effective_from"),
            effective_to=data.get("effective_to"),
            created_by=user,
            updated_by=user,
        )
        prompt.save()
        return prompt

    @classmethod
    def update_prompt(cls, pk: int, data: dict, user) -> ExtractionPromptTemplate | None:
        prompt = cls.get_prompt_detail(pk)
        if not prompt:
            return None

        before = {"prompt_text": prompt.prompt_text[:200]}
        editable = [
            "prompt_code", "prompt_category", "country_code", "regime_code",
            "document_type", "schema_code", "prompt_text", "variables_json",
            "effective_from", "effective_to",
        ]
        for field in editable:
            if field in data:
                setattr(prompt, field, data[field])
        prompt.updated_by = user
        prompt.save()

        after = {"prompt_text": prompt.prompt_text[:200]}
        ExtractionAuditService.log_settings_updated(
            entity_type="ExtractionPromptTemplate",
            entity_id=prompt.pk,
            before=before,
            after=after,
            user=user,
        )
        return prompt

    @classmethod
    def clone_prompt(cls, pk: int, user) -> ExtractionPromptTemplate | None:
        """Clone a prompt as a new DRAFT version."""
        source = cls.get_prompt_detail(pk)
        if not source:
            return None
        max_version = (
            ExtractionPromptTemplate.objects.filter(prompt_code=source.prompt_code)
            .order_by("-version")
            .values_list("version", flat=True)
            .first()
        ) or 0
        clone = ExtractionPromptTemplate(
            prompt_code=source.prompt_code,
            prompt_category=source.prompt_category,
            country_code=source.country_code,
            regime_code=source.regime_code,
            document_type=source.document_type,
            schema_code=source.schema_code,
            version=max_version + 1,
            status="DRAFT",
            prompt_text=source.prompt_text,
            variables_json=source.variables_json,
            created_by=user,
            updated_by=user,
        )
        clone.save()
        return clone

    @classmethod
    def activate_prompt(cls, pk: int, user) -> ExtractionPromptTemplate | None:
        """Activate a prompt and deactivate other versions of the same scope."""
        prompt = cls.get_prompt_detail(pk)
        if not prompt:
            return None
        # Deactivate other active versions in the same scope
        ExtractionPromptTemplate.objects.filter(
            prompt_code=prompt.prompt_code,
            country_code=prompt.country_code,
            regime_code=prompt.regime_code,
            document_type=prompt.document_type,
            schema_code=prompt.schema_code,
            status="ACTIVE",
        ).exclude(pk=pk).update(status="INACTIVE", updated_by=user)

        prompt.status = "ACTIVE"
        prompt.effective_from = timezone.now()
        prompt.updated_by = user
        prompt.save()
        return prompt

    @classmethod
    def deactivate_prompt(cls, pk: int, user) -> ExtractionPromptTemplate | None:
        prompt = cls.get_prompt_detail(pk)
        if not prompt:
            return None
        prompt.status = "INACTIVE"
        prompt.effective_to = timezone.now()
        prompt.updated_by = user
        prompt.save()
        return prompt

    @classmethod
    def compare_prompts(cls, id1: int, id2: int) -> dict | None:
        p1 = cls.get_prompt_detail(id1)
        p2 = cls.get_prompt_detail(id2)
        if not p1 or not p2:
            return None

        diff = list(difflib.unified_diff(
            p1.prompt_text.splitlines(keepends=True),
            p2.prompt_text.splitlines(keepends=True),
            fromfile=f"v{p1.version}",
            tofile=f"v{p2.version}",
            lineterm="",
        ))
        return {
            "prompt_1": p1,
            "prompt_2": p2,
            "diff_lines": diff,
            "metadata_diff": {
                "prompt_category": (p1.prompt_category, p2.prompt_category),
                "country_code": (p1.country_code, p2.country_code),
                "regime_code": (p1.regime_code, p2.regime_code),
                "document_type": (p1.document_type, p2.document_type),
                "schema_code": (p1.schema_code, p2.schema_code),
                "status": (p1.status, p2.status),
            },
        }

    @classmethod
    def get_active_prompt(cls, prompt_code: str, country_code: str = "", document_type: str = "") -> ExtractionPromptTemplate | None:
        """Get the active prompt for a given scope."""
        qs = ExtractionPromptTemplate.objects.filter(
            prompt_code=prompt_code,
            status="ACTIVE",
        )
        if country_code:
            qs = qs.filter(country_code=country_code)
        if document_type:
            qs = qs.filter(document_type=document_type)
        return qs.first()

    @classmethod
    def get_version_history(cls, prompt_code: str) -> QuerySet:
        return ExtractionPromptTemplate.objects.filter(
            prompt_code=prompt_code
        ).order_by("-version")
