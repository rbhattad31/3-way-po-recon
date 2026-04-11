"""End-to-end test: create invoice matching PO#1 and run reconciliation.

Verifies:
  1. First-partial classified as PARTIAL_MATCH (not MATCHED)
  2. GRN qty_received populated on result line
  3. PolicyEngine does NOT auto-close first-partial
"""
import os, sys, django
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from decimal import Decimal
from apps.documents.models import (
    DocumentUpload, Invoice, InvoiceLineItem,
    PurchaseOrder, PurchaseOrderLineItem, GoodsReceiptNote,
)
from apps.core.enums import InvoiceStatus, MatchStatus
from apps.reconciliation.models import ReconciliationConfig

# --- Setup: create invoice matching PO #1 ---
po = PurchaseOrder.objects.first()
grn = GoodsReceiptNote.objects.first()
po_line = PurchaseOrderLineItem.objects.filter(purchase_order=po).first()
config = ReconciliationConfig.objects.first()

print(f"PO: {po.po_number} (pk={po.pk})")
print(f"  line pk={po_line.pk}, qty={po_line.quantity}, price={po_line.unit_price}, amount={po_line.line_amount}")
print(f"GRN: pk={grn.pk}, receipt_date={grn.receipt_date}")
print(f"Config: partial_threshold={config.partial_invoice_threshold_pct}%")

# Create upload + invoice (partial: 62.4% of PO total)
upload = DocumentUpload.objects.create(
    original_filename="test_invoice.pdf",
    content_type="application/pdf",
    processing_state="completed",
    tenant=po.tenant,
)

invoice = Invoice.objects.create(
    document_upload=upload,
    invoice_number="TEST-PARTIAL-001",
    vendor=po.vendor,
    raw_vendor_name=po.vendor.name if po.vendor else "Test Vendor",
    invoice_date="2026-04-01",
    total_amount=Decimal("147288.00"),
    subtotal=Decimal("127794.00"),
    tax_amount=Decimal("19494.00"),
    currency="INR",
    po_number=po.po_number,
    status=InvoiceStatus.READY_FOR_RECON,
    extraction_confidence=0.95,
    tenant=po.tenant,
)

inv_line = InvoiceLineItem.objects.create(
    invoice=invoice,
    line_number=1,
    description="RPA Services",
    quantity=Decimal("1.0000"),
    unit_price=Decimal("108300.0000"),
    line_amount=Decimal("127794.00"),
    tax_amount=Decimal("19494.00"),
    tenant=po.tenant,
)

print(f"\nCreated Invoice: {invoice.invoice_number} (pk={invoice.pk})")
print(f"  total={invoice.total_amount}, subtotal={invoice.subtotal}, tax={invoice.tax_amount}")
print(f"  line: qty={inv_line.quantity}, price={inv_line.unit_price}, amount={inv_line.line_amount}")

# --- Run reconciliation ---
from apps.reconciliation.services.runner_service import ReconciliationRunnerService

runner = ReconciliationRunnerService()
run = runner.run(invoices=[invoice])

print(f"\n=== Reconciliation Results ===")
from apps.reconciliation.models import ReconciliationResult, ReconciliationResultLine, ReconciliationException

result = ReconciliationResult.objects.filter(invoice=invoice).order_by("-created_at").first()
print(f"Result pk={result.pk}")
print(f"  match_status={result.match_status}")
print(f"  reconciliation_mode={result.reconciliation_mode}")
print(f"  grn_checked_flag={result.grn_checked_flag}")
print(f"  grn_erp_source_type={result.grn_erp_source_type}")

# Check 1: PARTIAL_MATCH (not MATCHED)
assert result.match_status == MatchStatus.PARTIAL_MATCH, \
    f"FAIL: Expected PARTIAL_MATCH, got {result.match_status}"
print("  CHECK 1 PASSED: match_status is PARTIAL_MATCH")

# Check 2: GRN qty_received populated
lines = ReconciliationResultLine.objects.filter(result=result)
for rl in lines:
    print(f"  Line: po_line_id={rl.po_line_id} qty_received={rl.qty_received} "
          f"qty_invoice={rl.qty_invoice} qty_po={rl.qty_po}")
    assert rl.qty_received is not None, "FAIL: qty_received is None"
    assert rl.qty_received == Decimal("1.0000"), \
        f"FAIL: Expected qty_received=1.0000, got {rl.qty_received}"
print("  CHECK 2 PASSED: qty_received is populated correctly")

# Check exceptions
exceptions = ReconciliationException.objects.filter(result=result)
print(f"\nExceptions ({exceptions.count()}):")
for exc in exceptions:
    print(f"  {exc.exception_type} ({exc.severity}): {exc.message[:100]}")
    if exc.details:
        print(f"    details={exc.details}")

# Check 3: PolicyEngine should NOT auto-close
from apps.agents.services.policy_engine import PolicyEngine
plan = PolicyEngine().plan(result)
print(f"\nPolicyEngine plan:")
print(f"  skip_agents={plan.skip_agents}")
print(f"  auto_close={plan.auto_close}")
print(f"  reason={plan.reason}")
print(f"  agents={plan.agents}")

assert not plan.auto_close, f"FAIL: auto_close should be False, got True. Reason: {plan.reason}"
assert not plan.skip_agents, f"FAIL: skip_agents should be False, got True. Reason: {plan.reason}"
print("  CHECK 3 PASSED: PolicyEngine does NOT auto-close first-partial")

print("\n=== ALL CHECKS PASSED ===")
