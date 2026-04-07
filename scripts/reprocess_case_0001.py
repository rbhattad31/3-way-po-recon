"""Reprocess case AP-260406-0001 from NON_PO_VALIDATION stage."""
from apps.cases.models import APCase
from apps.cases.orchestrators.case_orchestrator import CaseOrchestrator

case = APCase.objects.select_related("invoice", "invoice__vendor").get(pk=1)
print(f"Case: {case.case_number}, status={case.status}")
print(f"  vendor on case: {case.vendor_id}")
print(f"  vendor on invoice: {case.invoice.vendor_id} ({case.invoice.vendor})")

orchestrator = CaseOrchestrator(case)
orchestrator.run_from("NON_PO_VALIDATION")

case.refresh_from_db()
print(f"After reprocess: status={case.status}, stage={case.current_stage}")

# Check validation result
artifact = case.artifacts.filter(artifact_type="VALIDATION_RESULT").order_by("-version").first()
if artifact and artifact.payload:
    checks = artifact.payload.get("checks", {})
    for name, check in checks.items():
        status = check.get("status", "?")
        msg = check.get("message", "")
        print(f"  {name:25s} {status:6s} {msg}")
