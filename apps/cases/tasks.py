"""Celery tasks for asynchronous case processing."""

from celery import shared_task
import logging

from apps.core.evaluation_constants import (
    CASE_PROCESSING_SUCCESS,
    CASE_REPROCESSED,
    TRACE_CASE_PIPELINE,
)
from apps.core.observability_helpers import build_observability_context, derive_session_id

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30, acks_late=True)
def process_case_task(self, case_id: int):
    """
    Run the CaseOrchestrator for an APCase asynchronously.

    Called after invoice upload + extraction, or when reprocessing.
    """
    from apps.cases.models import APCase
    from apps.cases.orchestrators.case_orchestrator import CaseOrchestrator

    import hashlib
    _trace_id = hashlib.md5(f"case-{case_id}".encode()).hexdigest()
    _lf_trace = None

    # Pre-load case for metadata (safe -- if not found, the try block below catches it)
    _case_meta = {}
    try:
        _case_pre = APCase.objects.select_related("invoice", "invoice__vendor").get(id=case_id)
        _case_meta = {
            "task_id": self.request.id,
            "case_id": case_id,
            "case_number": _case_pre.case_number,
            "invoice_id": _case_pre.invoice_id,
            "current_stage": _case_pre.current_stage or "",
            "current_status": _case_pre.status or "",
            "processing_path": _case_pre.processing_path or "",
            "vendor_id": getattr(_case_pre.invoice, "vendor_id", None) if _case_pre.invoice else None,
            "vendor_name": (
                _case_pre.invoice.vendor.name[:60]
                if _case_pre.invoice and _case_pre.invoice.vendor
                else ""
            ),
            "po_number": (
                _case_pre.invoice.po_number or ""
            ) if _case_pre.invoice else "",
            "trigger": "full",
            "source": "mixed",
        }
    except Exception:
        _case_meta = {"task_id": self.request.id, "case_id": case_id, "trigger": "full"}

    try:
        from apps.core.langfuse_client import start_trace_safe
        _lf_trace = start_trace_safe(
            _trace_id,
            TRACE_CASE_PIPELINE,
            invoice_id=_case_meta.get("invoice_id"),
            user_id=None,
            session_id=derive_session_id(
                invoice_id=_case_meta.get("invoice_id"),
                case_id=case_id,
            ),
            metadata=_case_meta,
        )
    except Exception:
        pass

    try:
        case = APCase.objects.get(id=case_id)

        # --- System agent: governance-visible case intake record ---
        try:
            from apps.agents.services.system_agent_classes import (
                SystemCaseIntakeAgent,
            )
            from apps.agents.services.base_agent import AgentContext

            _intake_ctx = AgentContext(
                reconciliation_result=None,
                invoice_id=case.invoice_id or 0,
                extra={
                    "case_id": case.pk,
                    "case_number": case.case_number or "",
                    "processing_path": case.processing_path or "",
                    "priority": case.priority or 0,
                    "stage_count": case.stages.count(),
                    "trigger": _case_meta.get("trigger", "full"),
                },
                actor_primary_role="SYSTEM_AGENT",
                actor_roles_snapshot=["SYSTEM_AGENT"],
                permission_source="system",
                access_granted=True,
                trace_id=_trace_id or "",
                _langfuse_trace=_lf_trace,
            )
            SystemCaseIntakeAgent().run(_intake_ctx)
        except Exception:
            logger.debug(
                "SystemCaseIntakeAgent skipped for case %s",
                case_id, exc_info=True,
            )

        orchestrator = CaseOrchestrator(case)
        orchestrator.run(lf_trace=_lf_trace, lf_trace_id=_trace_id)
        logger.info("Case %s processing completed (status=%s)", case.case_number, case.status)
        try:
            from apps.core.langfuse_client import end_span_safe, update_trace_safe, score_trace_safe
            # Update root trace with final metadata
            update_trace_safe(_lf_trace, metadata={
                "final_status": case.status,
                "final_stage": case.current_stage or "",
                "processing_path": case.processing_path or "",
                "review_required": bool(getattr(case, "review_assignment_id", None)),
            }, is_root=True)
            end_span_safe(_lf_trace, output={
                "status": case.status,
                "case_number": case.case_number,
                "final_stage": case.current_stage,
            }, is_root=True)
            score_trace_safe(_trace_id, CASE_PROCESSING_SUCCESS, 1.0, comment=f"case={case.case_number}", span=_lf_trace)
        except Exception:
            pass
    except APCase.DoesNotExist:
        logger.error("Case %d not found", case_id)
        try:
            from apps.core.langfuse_client import end_span_safe, score_trace_safe
            end_span_safe(_lf_trace, output={"error": "not_found"}, level="ERROR")
            score_trace_safe(_trace_id, CASE_PROCESSING_SUCCESS, 0.0, comment="case not found", span=_lf_trace)
        except Exception:
            pass
    except Exception as exc:
        logger.exception("Case %d processing failed", case_id)
        try:
            from apps.core.langfuse_client import end_span_safe, score_trace_safe
            end_span_safe(_lf_trace, output={"error": str(exc)[:200]}, level="ERROR")
            score_trace_safe(_trace_id, CASE_PROCESSING_SUCCESS, 0.0, comment=str(exc)[:100], span=_lf_trace)
        except Exception:
            pass
        from apps.core.utils import safe_retry
        safe_retry(self, exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=10, acks_late=True)
def reprocess_case_from_stage_task(self, case_id: int, stage: str):
    """Reprocess a case from a specific stage."""
    from apps.cases.models import APCase
    from apps.cases.orchestrators.case_orchestrator import CaseOrchestrator

    import hashlib
    _trace_id = hashlib.md5(f"case-{case_id}-reprocess-{stage}".encode()).hexdigest()
    _lf_trace = None

    _case_meta = {}
    try:
        _case_pre = APCase.objects.select_related("invoice").get(id=case_id)
        _case_meta = {
            "task_id": self.request.id,
            "case_id": case_id,
            "case_number": _case_pre.case_number,
            "invoice_id": _case_pre.invoice_id,
            "reprocess_from_stage": stage,
            "current_status": _case_pre.status or "",
            "trigger": "reprocess",
            "source": "mixed",
        }
    except Exception:
        _case_meta = {"task_id": self.request.id, "case_id": case_id, "stage": stage, "trigger": "reprocess"}

    try:
        from apps.core.langfuse_client import start_trace_safe
        _lf_trace = start_trace_safe(
            _trace_id,
            TRACE_CASE_PIPELINE,
            invoice_id=_case_meta.get("invoice_id"),
            session_id=derive_session_id(
                invoice_id=_case_meta.get("invoice_id"),
                case_id=case_id,
            ),
            metadata=_case_meta,
        )
    except Exception:
        pass

    try:
        case = APCase.objects.get(id=case_id)
        orchestrator = CaseOrchestrator(case)
        orchestrator.run_from(stage, lf_trace=_lf_trace, lf_trace_id=_trace_id)
        logger.info("Case %s reprocessed from %s (status=%s)", case.case_number, stage, case.status)
        try:
            from apps.core.langfuse_client import end_span_safe, score_trace_safe
            end_span_safe(_lf_trace, output={"status": case.status, "stage": stage}, is_root=True)
            score_trace_safe(_trace_id, CASE_PROCESSING_SUCCESS, 1.0, span=_lf_trace)
            score_trace_safe(_trace_id, CASE_REPROCESSED, 1.0, span=_lf_trace)
        except Exception:
            pass
    except APCase.DoesNotExist:
        logger.error("Case %d not found", case_id)
        try:
            from apps.core.langfuse_client import end_span_safe, score_trace_safe
            end_span_safe(_lf_trace, output={"error": "not_found"}, level="ERROR")
            score_trace_safe(_trace_id, CASE_PROCESSING_SUCCESS, 0.0, span=_lf_trace)
        except Exception:
            pass
    except Exception as exc:
        logger.exception("Case %d reprocessing failed from %s", case_id, stage)
        try:
            from apps.core.langfuse_client import end_span_safe, score_trace_safe
            end_span_safe(_lf_trace, output={"error": str(exc)[:200]}, level="ERROR")
            score_trace_safe(_trace_id, CASE_PROCESSING_SUCCESS, 0.0, span=_lf_trace)
        except Exception:
            pass
        from apps.core.utils import safe_retry
        safe_retry(self, exc)
