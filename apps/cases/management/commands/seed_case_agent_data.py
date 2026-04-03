"""
Management command: seed_case_agent_data

Seeds agent runs, steps, tool calls, decisions, and recommendations
for case 202 (non-PO invoice D96 from DEETYA GEMS).  Does NOT create
or link any master data (vendors, POs, GRNs) to the invoice.

Usage:
    python manage.py seed_case_agent_data
    python manage.py seed_case_agent_data --flush   # delete existing agent data for case 202 and re-create
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

CASE_PK = 202
INVOICE_PK = 248


class Command(BaseCommand):
    help = "Seed agent pipeline data for case 202 (non-PO)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete existing agent data for this case before seeding.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        from apps.agents.models import (
            AgentDefinition,
            AgentRecommendation,
            AgentRun,
            AgentStep,
            DecisionLog,
        )
        from apps.cases.models import APCase, APCaseDecision
        from apps.documents.models import Invoice
        from apps.reconciliation.models import ReconciliationResult, ReconciliationRun
        from apps.tools.models import ToolCall

        # ── Validate prerequisites ──
        try:
            case = APCase.objects.get(pk=CASE_PK)
        except APCase.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Case {CASE_PK} does not exist."))
            return

        try:
            invoice = Invoice.objects.get(pk=INVOICE_PK)
        except Invoice.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Invoice {INVOICE_PK} does not exist."))
            return

        # ── Flush if requested ──
        if options["flush"]:
            self.stdout.write("Flushing existing agent data for case 202...")
            # Delete agent runs linked to this case's recon result or invoice
            old_runs = AgentRun.objects.filter(
                input_payload__invoice_id=INVOICE_PK,
            )
            count = old_runs.count()
            old_runs.delete()
            if case.reconciliation_result:
                rr = case.reconciliation_result
                AgentRun.objects.filter(reconciliation_result=rr).delete()
                rr.delete()
                case.reconciliation_result = None
                case.save(update_fields=["reconciliation_result", "updated_at"])
            self.stdout.write(f"  Deleted {count} agent run(s) and related objects.")

        # ── Ensure a ReconciliationResult exists for the case ──
        recon_result = case.reconciliation_result
        if not recon_result:
            # Create a minimal recon run + result for non-PO
            run = ReconciliationRun.objects.create(
                status="COMPLETED",
                total_invoices=1,
                matched_count=0,
                partial_count=0,
                unmatched_count=0,
                review_count=1,
                error_count=0,
            )
            recon_result = ReconciliationResult.objects.create(
                run=run,
                invoice=invoice,
                purchase_order=None,
                match_status="REQUIRES_REVIEW",
                requires_review=True,
                vendor_match=False,
                currency_match=True,
                po_total_match=False,
                extraction_confidence=float(invoice.extraction_confidence or 0),
                deterministic_confidence=0.0,
                summary="Non-PO invoice requires manual review. No PO reference found.",
                reconciliation_mode="NON_PO",
                is_two_way_result=False,
                is_three_way_result=False,
            )
            case.reconciliation_result = recon_result
            case.save(update_fields=["reconciliation_result", "updated_at"])
            self.stdout.write(f"  Created ReconciliationResult #{recon_result.pk}")

        # ── Timestamps ──
        now = timezone.now()
        t0 = now - timedelta(minutes=12)
        trace_id = uuid.uuid4().hex[:16]

        # ── Agent definitions lookup ──
        agent_defs = {
            ad.agent_type: ad for ad in AgentDefinition.objects.all()
        }

        # ==============================================================
        # Agent Run 1: Exception Analysis Agent
        # ==============================================================
        t1_start = t0
        t1_end = t1_start + timedelta(seconds=8)
        run1 = AgentRun.objects.create(
            agent_definition=agent_defs.get("EXCEPTION_ANALYSIS"),
            agent_type="EXCEPTION_ANALYSIS",
            reconciliation_result=recon_result,
            status="COMPLETED",
            input_payload={
                "invoice_id": INVOICE_PK,
                "case_id": CASE_PK,
                "match_status": "REQUIRES_REVIEW",
                "reconciliation_mode": "NON_PO",
            },
            output_payload={
                "status": "COMPLETED",
                "confidence": 0.72,
                "recommendation_type": "SEND_TO_AP_REVIEW",
                "reasoning": (
                    "Invoice D96 from DEETYA GEMS is a non-PO invoice for INR 17,170. "
                    "No purchase order reference found. Vendor is not registered in the "
                    "master data system. The invoice contains a single line item for "
                    "semi-precious stone beads. Policy check flagged the invoice as "
                    "requiring AP review due to unregistered vendor and missing PO."
                ),
                "findings": [
                    "No PO reference on invoice",
                    "Vendor DEETYA GEMS not found in master data",
                    "Amount INR 17,170 is within standard approval threshold",
                    "HSN code 7013 identified on line item",
                ],
            },
            summarized_reasoning=(
                "Analyzed non-PO invoice D96 from DEETYA GEMS (INR 17,170). "
                "No PO linked, vendor not in master data. Single line item for "
                "semi-precious stone beads (HSN 7013). Amount is within standard "
                "limits but requires AP review due to missing PO and unregistered vendor."
            ),
            confidence=0.72,
            started_at=t1_start,
            completed_at=t1_end,
            duration_ms=8000,
            trace_id=trace_id,
            llm_model_used="gpt-4o",
            prompt_tokens=2100,
            completion_tokens=420,
            total_tokens=2520,
            actor_primary_role="SYSTEM_AGENT",
            permission_source="SYSTEM",
            access_granted=True,
        )

        # Steps for run 1
        AgentStep.objects.create(
            agent_run=run1,
            step_number=1,
            action="analyze_invoice_context",
            input_data={"invoice_id": INVOICE_PK, "mode": "NON_PO"},
            output_data={
                "invoice_number": "D96",
                "vendor_name": "DEETYA GEMS",
                "total_amount": 17170.0,
                "currency": "INR",
                "po_number": None,
                "line_count": 1,
            },
            success=True,
            duration_ms=500,
        )
        AgentStep.objects.create(
            agent_run=run1,
            step_number=2,
            action="check_vendor_registration",
            input_data={"vendor_name": "DEETYA GEMS"},
            output_data={
                "vendor_found": False,
                "search_methods": ["exact_match", "alias_lookup", "fuzzy_match"],
                "best_fuzzy_score": 0.0,
            },
            success=True,
            duration_ms=1200,
        )
        AgentStep.objects.create(
            agent_run=run1,
            step_number=3,
            action="evaluate_exceptions",
            input_data={"findings_count": 4},
            output_data={
                "exception_count": 2,
                "severity_breakdown": {"HIGH": 1, "MEDIUM": 1},
                "exceptions": [
                    {"type": "MISSING_PO", "severity": "HIGH", "message": "No purchase order reference found on invoice"},
                    {"type": "UNREGISTERED_VENDOR", "severity": "MEDIUM", "message": "Vendor DEETYA GEMS is not registered in master data"},
                ],
            },
            success=True,
            duration_ms=800,
        )

        # Tool calls for run 1
        ToolCall.objects.create(
            agent_run=run1,
            tool_name="vendor_search",
            status="SUCCESS",
            input_payload={"query": "DEETYA GEMS", "search_type": "fuzzy"},
            output_payload={
                "found": False,
                "candidates": [],
                "message": "No matching vendor found in master data",
            },
            duration_ms=650,
        )
        ToolCall.objects.create(
            agent_run=run1,
            tool_name="invoice_details",
            status="SUCCESS",
            input_payload={"invoice_id": INVOICE_PK},
            output_payload={
                "invoice_number": "D96",
                "vendor_name": "DEETYA GEMS",
                "total_amount": "17170.00",
                "currency": "INR",
                "line_items": [
                    {
                        "description": "Mix semi precious stone beads (HSN: 7013)",
                        "quantity": 7785,
                        "unit_price": 2.20,
                        "amount": 17127.18,
                    }
                ],
            },
            duration_ms=120,
        )

        # Decision log for run 1
        DecisionLog.objects.create(
            agent_run=run1,
            decision_type="RECOMMENDATION",
            decision="SEND_TO_AP_REVIEW",
            rationale=(
                "Non-PO invoice from unregistered vendor requires AP review. "
                "Amount is within standard threshold but vendor and PO validation failed."
            ),
            confidence=0.72,
            deterministic_flag=False,
            evidence_refs=[
                {"type": "tool_call", "tool": "vendor_search", "result": "not_found"},
                {"type": "tool_call", "tool": "invoice_details", "result": "no_po"},
            ],
            recommendation_type="SEND_TO_AP_REVIEW",
            invoice_id=INVOICE_PK,
            case_id=CASE_PK,
            reconciliation_result_id=recon_result.pk,
            trace_id=trace_id,
            actor_primary_role="SYSTEM_AGENT",
        )

        # Recommendation for run 1
        AgentRecommendation.objects.create(
            agent_run=run1,
            reconciliation_result=recon_result,
            invoice=invoice,
            recommendation_type="SEND_TO_AP_REVIEW",
            confidence=0.72,
            reasoning=(
                "Invoice D96 from DEETYA GEMS requires AP review. The vendor is not "
                "registered in master data and no PO reference was provided. A reviewer "
                "should verify the vendor details and approve or reject the invoice."
            ),
            evidence=[
                {"finding": "No PO reference on invoice", "severity": "HIGH"},
                {"finding": "Vendor not in master data", "severity": "MEDIUM"},
                {"finding": "Amount INR 17,170 within threshold", "severity": "LOW"},
            ],
            recommended_action="Route to AP team for manual vendor verification and approval.",
        )

        self.stdout.write(f"  Created AgentRun #{run1.pk} (Exception Analysis)")

        # ==============================================================
        # Agent Run 2: Review Routing Agent
        # ==============================================================
        t2_start = t1_end + timedelta(seconds=1)
        t2_end = t2_start + timedelta(seconds=4)
        run2 = AgentRun.objects.create(
            agent_definition=agent_defs.get("REVIEW_ROUTING"),
            agent_type="REVIEW_ROUTING",
            reconciliation_result=recon_result,
            status="COMPLETED",
            input_payload={
                "invoice_id": INVOICE_PK,
                "case_id": CASE_PK,
                "prior_recommendation": "SEND_TO_AP_REVIEW",
                "exception_count": 2,
            },
            output_payload={
                "status": "COMPLETED",
                "confidence": 0.85,
                "routing_decision": "AP_REVIEW",
                "priority": 5,
                "reasoning": (
                    "Routing to AP review queue based on exception analysis. "
                    "Invoice has unregistered vendor and no PO -- standard "
                    "non-PO review workflow applies."
                ),
            },
            summarized_reasoning=(
                "Routed invoice D96 to AP review queue with priority 5. "
                "Standard non-PO workflow: unregistered vendor, no PO reference. "
                "No escalation needed as amount is within normal limits."
            ),
            confidence=0.85,
            started_at=t2_start,
            completed_at=t2_end,
            duration_ms=4000,
            trace_id=trace_id,
            llm_model_used="gpt-4o",
            prompt_tokens=1800,
            completion_tokens=280,
            total_tokens=2080,
            actor_primary_role="SYSTEM_AGENT",
            permission_source="SYSTEM",
            access_granted=True,
        )

        # Steps for run 2
        AgentStep.objects.create(
            agent_run=run2,
            step_number=1,
            action="evaluate_routing_criteria",
            input_data={
                "exception_count": 2,
                "prior_recommendation": "SEND_TO_AP_REVIEW",
                "amount": 17170.0,
                "currency": "INR",
            },
            output_data={
                "queue": "AP_REVIEW",
                "priority": 5,
                "escalation_needed": False,
                "reason": "Standard non-PO review -- amount within limits",
            },
            success=True,
            duration_ms=600,
        )
        AgentStep.objects.create(
            agent_run=run2,
            step_number=2,
            action="assign_review_queue",
            input_data={"queue": "AP_REVIEW", "priority": 5},
            output_data={
                "assigned": True,
                "queue": "AP_REVIEW",
                "estimated_sla_hours": 24,
            },
            success=True,
            duration_ms=300,
        )

        # Decision log for run 2
        DecisionLog.objects.create(
            agent_run=run2,
            decision_type="RECOMMENDATION",
            decision="ROUTE_TO_AP_REVIEW",
            rationale=(
                "Standard routing for non-PO invoice with unregistered vendor. "
                "Priority 5 (normal). No escalation triggers met."
            ),
            confidence=0.85,
            deterministic_flag=False,
            evidence_refs=[
                {"type": "exception_analysis", "recommendation": "SEND_TO_AP_REVIEW"},
                {"type": "policy", "rule": "non_po_standard_review"},
            ],
            recommendation_type="SEND_TO_AP_REVIEW",
            invoice_id=INVOICE_PK,
            case_id=CASE_PK,
            reconciliation_result_id=recon_result.pk,
            trace_id=trace_id,
            actor_primary_role="SYSTEM_AGENT",
        )

        self.stdout.write(f"  Created AgentRun #{run2.pk} (Review Routing)")

        # ==============================================================
        # Agent Run 3: Case Summary Agent
        # ==============================================================
        t3_start = t2_end + timedelta(seconds=1)
        t3_end = t3_start + timedelta(seconds=5)
        run3 = AgentRun.objects.create(
            agent_definition=agent_defs.get("CASE_SUMMARY"),
            agent_type="CASE_SUMMARY",
            reconciliation_result=recon_result,
            status="COMPLETED",
            input_payload={
                "invoice_id": INVOICE_PK,
                "case_id": CASE_PK,
                "agent_runs_count": 2,
                "exception_count": 2,
            },
            output_payload={
                "status": "COMPLETED",
                "confidence": 0.90,
                "summary": (
                    "Non-PO invoice D96 from DEETYA GEMS for INR 17,170.00 "
                    "(semi-precious stone beads, HSN 7013). Vendor is not registered "
                    "in master data. No purchase order linked. Two exceptions identified: "
                    "missing PO (HIGH) and unregistered vendor (MEDIUM). Routed to AP "
                    "review queue for manual verification. Amount is within standard "
                    "approval threshold for INR transactions."
                ),
            },
            summarized_reasoning=(
                "Generated case summary consolidating findings from exception analysis "
                "and review routing. Key points: non-PO, unregistered vendor, INR 17,170 "
                "within limits, 2 exceptions, routed to AP review."
            ),
            confidence=0.90,
            started_at=t3_start,
            completed_at=t3_end,
            duration_ms=5000,
            trace_id=trace_id,
            llm_model_used="gpt-4o",
            prompt_tokens=2400,
            completion_tokens=350,
            total_tokens=2750,
            actor_primary_role="SYSTEM_AGENT",
            permission_source="SYSTEM",
            access_granted=True,
        )

        # Steps for run 3
        AgentStep.objects.create(
            agent_run=run3,
            step_number=1,
            action="aggregate_findings",
            input_data={"agent_runs": 2, "exceptions": 2, "decisions": 2},
            output_data={
                "total_findings": 4,
                "risk_level": "MEDIUM",
                "key_issues": ["missing_po", "unregistered_vendor"],
            },
            success=True,
            duration_ms=400,
        )
        AgentStep.objects.create(
            agent_run=run3,
            step_number=2,
            action="generate_narrative",
            input_data={"risk_level": "MEDIUM", "findings_count": 4},
            output_data={
                "summary_length": 280,
                "sections": ["overview", "exceptions", "routing", "recommendation"],
            },
            success=True,
            duration_ms=3500,
        )

        # Tool call for run 3
        ToolCall.objects.create(
            agent_run=run3,
            tool_name="exception_list",
            status="SUCCESS",
            input_payload={"reconciliation_result_id": recon_result.pk},
            output_payload={
                "exceptions": [
                    {"type": "MISSING_PO", "severity": "HIGH"},
                    {"type": "UNREGISTERED_VENDOR", "severity": "MEDIUM"},
                ],
                "count": 2,
            },
            duration_ms=90,
        )

        self.stdout.write(f"  Created AgentRun #{run3.pk} (Case Summary)")

        # ==============================================================
        # Case decision: SENT_TO_REVIEW (if not already present)
        # ==============================================================
        if not case.decisions.filter(decision_type="SENT_TO_REVIEW").exists():
            APCaseDecision.objects.create(
                case=case,
                decision_type="SENT_TO_REVIEW",
                decision_source="AGENT",
                decision_value="SEND_TO_AP_REVIEW",
                confidence=0.72,
                rationale=(
                    "Agent pipeline completed analysis of non-PO invoice D96. "
                    "Two exceptions identified (missing PO, unregistered vendor). "
                    "Routed to AP review queue for manual verification."
                ),
                evidence={
                    "agent_runs": 3,
                    "exceptions": [
                        {"type": "MISSING_PO", "severity": "HIGH"},
                        {"type": "UNREGISTERED_VENDOR", "severity": "MEDIUM"},
                    ],
                    "recommendation": "SEND_TO_AP_REVIEW",
                    "routing_priority": 5,
                },
            )
            self.stdout.write("  Created case decision: SENT_TO_REVIEW")

        self.stdout.write(
            self.style.SUCCESS(
                f"\nSeeded agent pipeline data for case {case.case_number} "
                f"(3 agent runs, steps, tool calls, decisions, recommendation)."
            )
        )
