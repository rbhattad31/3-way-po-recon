"""Export field mapping agent for purchase-invoice workbook generation.

This agent applies deterministic ERP snapshot mapping first, then optionally
uses an AI fallback for unresolved fields only.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from django.conf import settings

logger = logging.getLogger(__name__)


class ExportFieldMappingAgent:
    """Resolve export fields with deterministic-first and optional AI fallback."""

    _LINE_FIELDS = (
        "item_code",
        "uom",
        "cost_center",
        "department",
        "purchase_account",
    )

    def __init__(self, reference_resolver):
        self._resolver = reference_resolver
        self._enable_ai_fallback = bool(
            getattr(settings, "EXPORT_MAPPING_AI_FALLBACK_ENABLED", False)
        )
        self._ai_min_confidence = float(
            getattr(settings, "EXPORT_MAPPING_AI_MIN_CONFIDENCE", 0.8)
        )
        self._llm = None
        self._header_unresolved_count = 0
        self._line_unresolved_count = 0
        self._ai_fallback_used = False
        self._ai_fields_applied = 0

        if self._enable_ai_fallback:
            try:
                from apps.agents.services.llm_client import LLMClient

                self._llm = LLMClient()
            except Exception as exc:
                logger.warning(
                    "Export AI mapping fallback disabled: LLM client unavailable: %s",
                    exc,
                )
                self._enable_ai_fallback = False

    def resolve_header_fields(self, *, invoice, vendor_name: str, po_number: str) -> Dict[str, Any]:
        """Resolve header-level export fields."""
        resolved = self._resolver.resolve_header_fields(
            invoice=invoice,
            vendor_name=vendor_name,
            po_number=po_number,
        )

        unresolved = [
            field
            for field in ("party_account", "purchase_account", "currency")
            if not self._has_value(resolved.get(field))
        ]
        if not unresolved:
            return resolved

        self._header_unresolved_count += len(unresolved)

        ai_resolved = self._resolve_with_ai(
            scope="header",
            unresolved_fields=unresolved,
            context={
                "vendor_name": vendor_name,
                "po_number": po_number,
                "invoice_currency": getattr(invoice, "currency", "") or "",
                "invoice_number": getattr(invoice, "invoice_number", "") or "",
            },
        )
        for field in unresolved:
            candidate = ai_resolved.get(field)
            if self._has_value(candidate):
                resolved[field] = candidate
                self._ai_fields_applied += 1
        return resolved

    def resolve_line_fields(
        self,
        *,
        po_number: str,
        line_number: int,
        description: str,
        party_account: str = "",
    ) -> Dict[str, Any]:
        """Resolve line-level export fields."""
        resolved = self._resolver.resolve_line_fields(
            po_number=po_number,
            line_number=line_number,
            description=description,
            party_account=party_account,
        )

        unresolved = [
            field for field in self._LINE_FIELDS if not self._has_value(resolved.get(field))
        ]
        if not unresolved:
            return resolved

        self._line_unresolved_count += len(unresolved)

        ai_resolved = self._resolve_with_ai(
            scope="line",
            unresolved_fields=unresolved,
            context={
                "po_number": po_number,
                "line_number": line_number,
                "description": description,
                "party_account": party_account,
            },
        )
        for field in unresolved:
            candidate = ai_resolved.get(field)
            if self._has_value(candidate):
                resolved[field] = candidate
                self._ai_fields_applied += 1
        return resolved

    def emit_governance_run(
        self,
        *,
        request_user=None,
        tenant=None,
        scope: str = "single",
        invoices_count: int = 1,
        invoice_id: int = 0,
    ) -> None:
        """Best-effort emission of system-agent run for export mapping governance."""
        try:
            from apps.agents.services.base_agent import AgentContext
            from apps.agents.services.guardrails_service import AgentGuardrailsService
            from apps.agents.services.system_agent_classes import SystemExportFieldMappingAgent
            from apps.core.enums import AgentType

            actor = AgentGuardrailsService.resolve_actor(request_user)
            if not AgentGuardrailsService.authorize_agent(actor, AgentType.SYSTEM_EXPORT_FIELD_MAPPING):
                return

            snapshot = AgentGuardrailsService.build_rbac_snapshot(actor)
            ctx = AgentContext(
                reconciliation_result=None,
                invoice_id=invoice_id or 0,
                extra={
                    "scope": scope,
                    "invoices_count": int(invoices_count or 1),
                    "header_unresolved_count": int(self._header_unresolved_count),
                    "line_unresolved_count": int(self._line_unresolved_count),
                    "ai_fallback_enabled": bool(self._enable_ai_fallback),
                    "ai_fallback_used": bool(self._ai_fallback_used),
                    "ai_fields_applied": int(self._ai_fields_applied),
                },
                actor_user_id=snapshot.get("actor_user_id"),
                actor_primary_role=snapshot.get("actor_primary_role", ""),
                actor_roles_snapshot=snapshot.get("actor_roles_snapshot") or [],
                permission_checked="agents.run_system_export_field_mapping",
                permission_source=snapshot.get("permission_source", "USER"),
                access_granted=True,
                tenant=tenant,
                invocation_reason=f"export_mapping:{scope}",
            )

            SystemExportFieldMappingAgent().run(ctx)
        except Exception as exc:
            logger.warning("Failed to emit export mapping governance AgentRun: %s", exc)

    @staticmethod
    def _has_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str) and not value.strip():
            return False
        return True

    def _resolve_with_ai(
        self,
        *,
        scope: str,
        unresolved_fields: List[str],
        context: Dict[str, Any],
    ) -> Dict[str, str]:
        """Resolve only unresolved fields via AI and strict JSON contract."""
        if not self._enable_ai_fallback or self._llm is None:
            return {}

        try:
            from apps.agents.services.llm_client import LLMMessage

            system_msg = (
                "You are a deterministic data mapping assistant for AP exports. "
                "Return JSON only with keys: resolved (object) and confidence (number). "
                "Only map fields explicitly requested in unresolved_fields. "
                "Do not invent financial values. If unknown, leave the field absent."
            )
            user_msg = json.dumps(
                {
                    "scope": scope,
                    "unresolved_fields": unresolved_fields,
                    "context": context,
                }
            )

            resp = self._llm.chat(
                [
                    LLMMessage(role="system", content=system_msg),
                    LLMMessage(role="user", content=user_msg),
                ],
                response_format={"type": "json_object"},
            )
            payload = json.loads(resp.content or "{}")
            confidence = float(payload.get("confidence", 0.0) or 0.0)
            if confidence < self._ai_min_confidence:
                return {}

            resolved = payload.get("resolved") or {}
            if not isinstance(resolved, dict):
                return {}

            clean: Dict[str, str] = {}
            for field in unresolved_fields:
                value = resolved.get(field)
                if value in (None, ""):
                    continue
                clean[field] = str(value).strip()
            if clean:
                self._ai_fallback_used = True
            return clean
        except Exception as exc:
            logger.warning("Export AI mapping fallback failed: %s", exc)
            return {}
