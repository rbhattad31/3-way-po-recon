"""Check validation results after reprocess."""
from apps.cases.models import APCase
c = APCase.objects.get(pk=1)
art = c.artifacts.filter(artifact_type="VALIDATION_RESULT").order_by("-version").first()
if art:
    checks = art.payload.get("checks", {})
    for name, check in checks.items():
        status = check.get("status", "?")
        msg = check.get("message", "")
        print(f"  {name:25s} {status:6s} {msg}")
    print(f"Overall: {art.payload.get('overall_status')}")
    print(f"Version: {art.version}")
else:
    print("No validation artifact found")
print(f"Case vendor_id: {c.vendor_id}")
print(f"Artifacts count: {c.artifacts.filter(artifact_type='VALIDATION_RESULT').count()}")
