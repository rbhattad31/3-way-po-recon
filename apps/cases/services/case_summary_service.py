"""CaseSummaryService — builds and refreshes APCaseSummary."""

import logging

from apps.cases.models import APCase, APCaseSummary

logger = logging.getLogger(__name__)


class CaseSummaryService:

    @staticmethod
    def build_summary(case: APCase) -> APCaseSummary:
        """
        Build or update the case summary.

        Priority:
        1. AI-generated summary from CASE_SUMMARY agent run (deterministic resolver)
        2. Fallback: structured summary built from case data
        """
        ai_summary = CaseSummaryService._get_ai_summary(case)
        recommendation = ""
        agent_run = None

        if ai_summary:
            summary_text = ai_summary["summary"]
            recommendation = ai_summary.get("recommendation", "")
            agent_run = ai_summary.get("agent_run")
        else:
            summary_text = CaseSummaryService._build_deterministic_summary(case)

        summary, _ = APCaseSummary.objects.update_or_create(
            case=case,
            defaults={
                "latest_summary": summary_text,
                "recommendation": recommendation,
                "generated_by_agent_run": agent_run,
            },
        )
        return summary

    @staticmethod
    def _get_ai_summary(case: APCase):
        """Try to get AI-generated summary from CASE_SUMMARY agent run."""
        recon_result = case.reconciliation_result
        if not recon_result:
            return None

        from apps.agents.models import AgentRun
        agent_run = (
            AgentRun.objects
            .filter(
                reconciliation_result=recon_result,
                agent_type="CASE_SUMMARY",
                status="COMPLETED",
            )
            .order_by("-created_at")
            .first()
        )
        if not agent_run or not agent_run.summarized_reasoning:
            return None

        # Extract recommendation from output payload
        recommendation = ""
        if agent_run.output_payload and isinstance(agent_run.output_payload, dict):
            rec_type = agent_run.output_payload.get("recommendation_type", "")
            reasoning = agent_run.output_payload.get("reasoning", "")
            if rec_type:
                recommendation = f"{rec_type.replace('_', ' ').title()}: {reasoning}"

        return {
            "summary": agent_run.summarized_reasoning,
            "recommendation": recommendation,
            "agent_run": agent_run,
        }

    @staticmethod
    def _build_deterministic_summary(case: APCase) -> str:
        """Build a structured summary from case data as fallback."""
        invoice = case.invoice
        parts = []

        parts.append(
            f"Invoice {invoice.invoice_number} from {invoice.vendor.name if invoice.vendor else 'Unknown'} "
            f"for {invoice.currency} {invoice.total_amount or 0:,.2f}"
        )

        parts.append(f"Processing path: {case.get_processing_path_display()}")

        if case.reconciliation_result:
            result = case.reconciliation_result
            parts.append(f"Match status: {result.get_match_status_display()}")
            exc_count = result.exceptions.count()
            if exc_count:
                parts.append(f"Exceptions: {exc_count}")

        validation_artifact = case.artifacts.filter(artifact_type="VALIDATION_RESULT").order_by("-version", "-created_at").first()
        if validation_artifact:
            payload = validation_artifact.payload
            parts.append(f"Non-PO validation: {payload.get('overall_status', 'N/A')}")
            issues = payload.get("issues", [])
            if issues:
                parts.append(f"Issues: {', '.join(issues[:3])}")

        parts.append(f"Status: {case.get_status_display()}")

        return ". ".join(parts)
