"""
Bulk scenario generator for QA and large seed modes.

Generates additional randomized (but realistic) AP case scenarios beyond the
30 deterministic ones, using the same vendor/branch/line-item catalog.
"""
from __future__ import annotations

import logging
import random
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

from apps.accounts.models import User
from apps.agents.models import AgentDefinition, AgentRun
from apps.auditlog.models import AuditEvent
from apps.cases.models import (
    APCase,
    APCaseActivity,
    APCaseAssignment,
    APCaseComment,
    APCaseDecision,
    APCaseStage,
    APCaseSummary,
)
from apps.core.enums import (
    AgentRunStatus,
    AgentType,
    ArtifactType,
    AssignmentStatus,
    AssignmentType,
    AuditEventType,
    BudgetCheckStatus,
    CasePriority,
    CaseStageType,
    CaseStatus,
    DecisionSource,
    DecisionType,
    ExceptionSeverity,
    ExceptionType,
    InvoiceStatus,
    InvoiceType,
    MatchStatus,
    PerformedByType,
    ProcessingPath,
    ReconciliationMode,
    ReconciliationModeApplicability,
    ReconciliationRunStatus,
    ReviewStatus,
    SourceChannel,
    StageStatus,
    UserRole,
)
from apps.documents.models import (
    GRNLineItem,
    GoodsReceiptNote,
    Invoice,
    InvoiceLineItem,
    PurchaseOrder,
    PurchaseOrderLineItem,
)
from apps.reconciliation.models import (
    ReconciliationException,
    ReconciliationResult,
    ReconciliationRun,
)
from apps.reviews.models import ReviewAssignment
from apps.vendors.models import Vendor

from .constants import (
    BRANCHES,
    LINE_ITEMS_CATALOG,
    NON_PO_LINE_ITEMS,
    SERVICE_LINE_ITEMS,
    WAREHOUSES,
)

logger = logging.getLogger(__name__)

VAT_RATE = Decimal("0.15")


def _d(val) -> Decimal:
    return Decimal(str(val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# Path distribution weights
_PATH_WEIGHTS = [
    ("TWO_WAY", 0.30),
    ("THREE_WAY", 0.45),
    ("NON_PO", 0.25),
]

# Status distribution for bulk cases
_STATUS_POOL = [
    CaseStatus.NEW,
    CaseStatus.EXTRACTION_COMPLETED,
    CaseStatus.TWO_WAY_IN_PROGRESS,
    CaseStatus.THREE_WAY_IN_PROGRESS,
    CaseStatus.NON_PO_VALIDATION_IN_PROGRESS,
    CaseStatus.READY_FOR_REVIEW,
    CaseStatus.IN_REVIEW,
    CaseStatus.REVIEW_COMPLETED,
    CaseStatus.CLOSED,
    CaseStatus.CLOSED,
    CaseStatus.CLOSED,
    CaseStatus.READY_FOR_APPROVAL,
    CaseStatus.ESCALATED,
    CaseStatus.REJECTED,
    CaseStatus.FAILED,
]

# Category pools per path
_TWO_WAY_CATEGORIES = [
    "HVAC Maintenance", "Kitchen Equipment Service", "Pest Control",
    "Signage & Branding", "Telecom & Internet", "Cold Chain Logistics",
    "Facility Maintenance", "Security Services", "Waste Management",
]
_THREE_WAY_CATEGORIES = [
    "Frozen Proteins", "Bakery & Buns", "Dairy & Sauces",
    "Condiments & Sauces", "Beverages & Syrups", "Fries & Potato Products",
    "Packaging Materials", "Cleaning & Hygiene",
]
_NON_PO_CATEGORIES = [
    "Utilities", "Government & Compliance", "Consulting & Audit",
    "Marketing & Agency", "Training & Development", "Recruitment & Staffing",
]


def _pick_vendor_for_category(category: str, vendors: dict[str, Vendor], rng: random.Random) -> Vendor | None:
    """Find a vendor matching the category, or pick randomly."""
    from .constants import VENDORS_DATA
    matching = [v for v in VENDORS_DATA if v["category"] == category]
    if matching:
        code = rng.choice(matching)["code"]
        return vendors.get(code)
    return rng.choice(list(vendors.values()))


def _pick_location(path: str, rng: random.Random) -> str:
    if path == "THREE_WAY":
        return rng.choice(WAREHOUSES)["code"]
    elif path == "TWO_WAY":
        return rng.choice(BRANCHES + WAREHOUSES)["code"]
    else:
        locs = BRANCHES + [None]
        loc = rng.choice(locs)
        return loc["code"] if loc else ""


def generate_bulk_scenarios(
    start_num: int,
    count: int,
    vendors: dict[str, Vendor],
    users: dict[str, User],
    admin: User,
    rand_seed: int = 42,
) -> dict:
    """Generate `count` random AP case scenarios starting at `start_num`."""
    rng = random.Random(rand_seed)
    stats = {"cases": 0, "invoices": 0, "pos": 0, "grns": 0}

    # Get or create a recon run
    run, _ = ReconciliationRun.objects.get_or_create(
        celery_task_id="seed-run-mcdksa-bulk",
        defaults={
            "status": ReconciliationRunStatus.COMPLETED,
            "started_at": timezone.now() - timedelta(hours=4),
            "completed_at": timezone.now() - timedelta(hours=3),
            "total_invoices": count,
            "triggered_by": admin,
            "created_by": admin,
        },
    )

    reviewer_pool = [
        users.get("reviewer"), users.get("reviewer_sc"),
        users.get("reviewer_fac"), users.get("reviewer_senior"),
    ]
    reviewer_pool = [r for r in reviewer_pool if r is not None]

    for i in range(count):
        sc_num = start_num + i

        # Pick path
        path = rng.choices(
            [p[0] for p in _PATH_WEIGHTS],
            weights=[p[1] for p in _PATH_WEIGHTS],
        )[0]

        # Pick category
        if path == "TWO_WAY":
            category = rng.choice(_TWO_WAY_CATEGORIES)
        elif path == "THREE_WAY":
            category = rng.choice(_THREE_WAY_CATEGORIES)
        else:
            category = rng.choice(_NON_PO_CATEGORIES)

        vendor = _pick_vendor_for_category(category, vendors, rng)
        location = _pick_location(path, rng)

        # Pick status appropriate to path
        status = rng.choice(_STATUS_POOL)
        # Fix incompatible status/path combos
        if path == "TWO_WAY" and status == CaseStatus.THREE_WAY_IN_PROGRESS:
            status = CaseStatus.TWO_WAY_IN_PROGRESS
        if path == "THREE_WAY" and status == CaseStatus.TWO_WAY_IN_PROGRESS:
            status = CaseStatus.THREE_WAY_IN_PROGRESS
        if path == "NON_PO" and status in (CaseStatus.TWO_WAY_IN_PROGRESS, CaseStatus.THREE_WAY_IN_PROGRESS):
            status = CaseStatus.NON_PO_VALIDATION_IN_PROGRESS

        priority = rng.choice([CasePriority.LOW, CasePriority.LOW, CasePriority.MEDIUM, CasePriority.MEDIUM, CasePriority.HIGH, CasePriority.CRITICAL])

        # Pick items
        pool = (
            LINE_ITEMS_CATALOG.get(category)
            or SERVICE_LINE_ITEMS.get(category)
            or NON_PO_LINE_ITEMS.get(category)
            or [{"desc": f"{category} item", "uom": "EA", "price": 1000.00}]
        )
        n_lines = rng.randint(1, min(4, len(pool)))
        items = rng.sample(pool, n_lines)
        quantities = [rng.randint(3, 40) for _ in items]

        inv_date = date(2026, 1, 15) - timedelta(days=rng.randint(1, 120))

        # --- Create PO ---
        po = None
        po_lines = []
        if path in ("TWO_WAY", "THREE_WAY"):
            po_num = f"PO-MCD-{sc_num:04d}"
            po_date = inv_date - timedelta(days=rng.randint(5, 30))
            subtotal = sum(_d(item["price"]) * qty for item, qty in zip(items, quantities))
            tax = (subtotal * VAT_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            po, _ = PurchaseOrder.objects.get_or_create(
                po_number=po_num,
                defaults={
                    "normalized_po_number": po_num.upper(),
                    "po_date": po_date,
                    "vendor": vendor,
                    "currency": "SAR",
                    "total_amount": subtotal + tax,
                    "tax_amount": tax,
                    "status": "OPEN",
                    "department": category,
                    "created_by": admin,
                },
            )
            stats["pos"] += 1

            for idx, (item, qty) in enumerate(zip(items, quantities), 1):
                up = _d(item["price"])
                la = up * qty
                lt = (la * VAT_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                pl, _ = PurchaseOrderLineItem.objects.get_or_create(
                    purchase_order=po,
                    line_number=idx,
                    defaults={
                        "item_code": f"ITM-{sc_num:04d}-{idx:02d}",
                        "description": item["desc"],
                        "quantity": Decimal(str(qty)),
                        "unit_price": up,
                        "tax_amount": lt,
                        "line_amount": la,
                        "unit_of_measure": item["uom"],
                        "is_service_item": path == "TWO_WAY",
                        "is_stock_item": path == "THREE_WAY",
                    },
                )
                po_lines.append(pl)

        # --- Create GRN (THREE_WAY only, 80% chance) ---
        grns = []
        has_grn = path == "THREE_WAY" and rng.random() > 0.2
        if has_grn and po:
            grn_num = f"GRN-MCD-{sc_num:04d}"
            grn, _ = GoodsReceiptNote.objects.get_or_create(
                grn_number=grn_num,
                defaults={
                    "purchase_order": po,
                    "vendor": vendor,
                    "receipt_date": inv_date - timedelta(days=rng.randint(0, 5)),
                    "status": "RECEIVED",
                    "warehouse": location,
                    "created_by": admin,
                },
            )
            for pl in po_lines:
                variance = rng.choice([0, 0, 0, 0, -1, -2, 1, 2])
                qty_r = max(int(pl.quantity) + variance, 1)
                qty_rej = max(0, rng.choice([0, 0, 0, 0, 0, 1]))
                GRNLineItem.objects.get_or_create(
                    grn=grn,
                    line_number=pl.line_number,
                    defaults={
                        "po_line": pl,
                        "item_code": pl.item_code,
                        "description": pl.description,
                        "quantity_received": Decimal(str(qty_r)),
                        "quantity_accepted": Decimal(str(qty_r - qty_rej)),
                        "quantity_rejected": Decimal(str(qty_rej)),
                        "unit_of_measure": pl.unit_of_measure,
                    },
                )
            grns.append(grn)
            stats["grns"] += 1

        # --- Create Invoice ---
        inv_num = f"INV-MCD-{sc_num:04d}"
        inv_subtotal = sum(_d(item["price"]) * qty for item, qty in zip(items, quantities))
        # Random small variance
        if rng.random() > 0.7:
            inv_subtotal += _d(rng.uniform(-100, 100))
        inv_tax = (inv_subtotal * VAT_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        inv_total = inv_subtotal + inv_tax

        extraction_conf = round(rng.uniform(0.55, 0.99), 2)

        inv_status = InvoiceStatus.READY_FOR_RECON
        if status in (CaseStatus.CLOSED, CaseStatus.REVIEW_COMPLETED, CaseStatus.REJECTED):
            inv_status = InvoiceStatus.RECONCILED
        elif status in (CaseStatus.NEW, CaseStatus.EXTRACTION_COMPLETED):
            inv_status = InvoiceStatus.EXTRACTED

        invoice, _ = Invoice.objects.get_or_create(
            invoice_number=inv_num,
            defaults={
                "normalized_invoice_number": inv_num.upper(),
                "raw_invoice_number": inv_num,
                "raw_vendor_name": vendor.name if vendor else "",
                "raw_invoice_date": str(inv_date),
                "raw_po_number": po.po_number if po else "",
                "raw_currency": "SAR",
                "raw_subtotal": str(inv_subtotal),
                "raw_tax_amount": str(inv_tax),
                "raw_total_amount": str(inv_total),
                "invoice_date": inv_date,
                "po_number": po.po_number if po else "",
                "normalized_po_number": po.po_number.upper() if po else "",
                "currency": "SAR",
                "subtotal": inv_subtotal,
                "tax_amount": inv_tax,
                "total_amount": inv_total,
                "status": inv_status,
                "vendor": vendor,
                "extraction_confidence": extraction_conf,
                "created_by": admin,
            },
        )
        stats["invoices"] += 1

        for idx, (item, qty) in enumerate(zip(items, quantities), 1):
            InvoiceLineItem.objects.get_or_create(
                invoice=invoice,
                line_number=idx,
                defaults={
                    "raw_description": item["desc"],
                    "description": item["desc"],
                    "normalized_description": item["desc"].upper(),
                    "quantity": Decimal(str(qty)),
                    "unit_price": _d(item["price"]),
                    "line_amount": _d(item["price"]) * qty,
                    "unit_of_measure": item["uom"],
                    "extraction_confidence": extraction_conf,
                },
            )

        # --- Recon result ---
        recon_result = None
        match_status_val = MatchStatus.MATCHED
        has_exceptions = rng.random() > 0.5
        if has_exceptions:
            match_status_val = rng.choice([MatchStatus.PARTIAL_MATCH, MatchStatus.REQUIRES_REVIEW])

        if path in ("TWO_WAY", "THREE_WAY") and po:
            diff = invoice.total_amount - po.total_amount
            recon_result, _ = ReconciliationResult.objects.get_or_create(
                run=run,
                invoice=invoice,
                defaults={
                    "purchase_order": po,
                    "match_status": match_status_val,
                    "requires_review": has_exceptions,
                    "vendor_match": True,
                    "currency_match": True,
                    "po_total_match": abs(diff) < Decimal("1"),
                    "invoice_total_vs_po": diff,
                    "total_amount_difference": diff,
                    "extraction_confidence": extraction_conf,
                    "deterministic_confidence": 0.90 if not has_exceptions else 0.65,
                    "reconciliation_mode": ReconciliationMode.TWO_WAY if path == "TWO_WAY" else ReconciliationMode.THREE_WAY,
                    "is_two_way_result": path == "TWO_WAY",
                    "is_three_way_result": path == "THREE_WAY",
                    "grn_available": has_grn,
                    "summary": f"Bulk scenario {sc_num}: {category} invoice for {vendor.name if vendor else 'unknown'}.",
                    "created_by": admin,
                },
            )

            # Random exceptions
            if has_exceptions:
                exc_pool = ["QTY_MISMATCH", "PRICE_MISMATCH", "AMOUNT_MISMATCH", "TAX_MISMATCH"]
                if path == "THREE_WAY" and not has_grn:
                    exc_pool.append("GRN_NOT_FOUND")
                for exc_key in rng.sample(exc_pool, min(rng.randint(1, 2), len(exc_pool))):
                    exc_type = getattr(ExceptionType, exc_key, None)
                    if exc_type:
                        ReconciliationException.objects.get_or_create(
                            result=recon_result,
                            exception_type=exc_type,
                            defaults={
                                "severity": rng.choice([ExceptionSeverity.LOW, ExceptionSeverity.MEDIUM, ExceptionSeverity.HIGH]),
                                "message": f"{exc_key.replace('_', ' ').title()} detected.",
                                "details": {"bulk": True, "scenario": sc_num},
                                "resolved": status in (CaseStatus.CLOSED,),
                                "applies_to_mode": ReconciliationModeApplicability.BOTH,
                            },
                        )

        # --- AP Case ---
        case_num = f"AP-{sc_num:06d}"
        inv_type = InvoiceType.PO_BACKED if path != "NON_PO" else InvoiceType.NON_PO
        requires_review = has_exceptions or status in (
            CaseStatus.READY_FOR_REVIEW, CaseStatus.IN_REVIEW,
            CaseStatus.ESCALATED,
        )

        case, _ = APCase.objects.get_or_create(
            case_number=case_num,
            defaults={
                "invoice": invoice,
                "vendor": vendor,
                "purchase_order": po,
                "reconciliation_result": recon_result,
                "source_channel": rng.choice([SourceChannel.WEB_UPLOAD, SourceChannel.EMAIL, SourceChannel.API]),
                "invoice_type": inv_type,
                "processing_path": getattr(ProcessingPath, path),
                "status": status,
                "priority": priority,
                "risk_score": round(rng.uniform(0.1, 0.9), 2),
                "extraction_confidence": extraction_conf,
                "requires_human_review": requires_review,
                "requires_approval": status == CaseStatus.READY_FOR_APPROVAL,
                "eligible_for_posting": status in (CaseStatus.CLOSED, CaseStatus.READY_FOR_POSTING),
                "reconciliation_mode": ReconciliationMode.TWO_WAY if path == "TWO_WAY" else (ReconciliationMode.THREE_WAY if path == "THREE_WAY" else ""),
                "created_by": admin,
            },
        )
        stats["cases"] += 1

        # Minimal stages
        base_time = timezone.now() - timedelta(hours=rng.randint(6, 168))
        for stage_name in [CaseStageType.INTAKE, CaseStageType.EXTRACTION]:
            APCaseStage.objects.get_or_create(
                case=case,
                stage_name=stage_name,
                retry_count=0,
                defaults={
                    "stage_status": StageStatus.COMPLETED,
                    "performed_by_type": PerformedByType.SYSTEM,
                    "started_at": base_time,
                    "completed_at": base_time + timedelta(minutes=5),
                },
            )

        # Path decision
        APCaseDecision.objects.get_or_create(
            case=case,
            decision_type=DecisionType.PATH_SELECTED,
            decision_value=path,
            defaults={
                "decision_source": DecisionSource.DETERMINISTIC,
                "confidence": 0.95,
                "rationale": f"Path: {path} for {category}.",
            },
        )

        # Summary
        APCaseSummary.objects.get_or_create(
            case=case,
            defaults={
                "latest_summary": f"Bulk case {case_num}: {category} invoice from {vendor.name if vendor else 'N/A'}. Status: {status}.",
                "recommendation": "Review required" if requires_review else "Eligible for auto-close",
            },
        )

        # Review assignment for cases that need it
        if requires_review and recon_result and reviewer_pool:
            reviewer = rng.choice(reviewer_pool)
            rev_status = ReviewStatus.PENDING
            if status in (CaseStatus.IN_REVIEW,):
                rev_status = ReviewStatus.IN_REVIEW
            elif status in (CaseStatus.CLOSED, CaseStatus.REVIEW_COMPLETED):
                rev_status = ReviewStatus.APPROVED

            ra, _ = ReviewAssignment.objects.get_or_create(
                reconciliation_result=recon_result,
                defaults={
                    "assigned_to": reviewer,
                    "status": rev_status,
                    "priority": rng.randint(1, 9),
                    "notes": f"Bulk scenario {sc_num}",
                    "created_by": admin,
                },
            )
            case.review_assignment = ra
            case.assigned_to = reviewer
            case.save(update_fields=["review_assignment", "assigned_to"])

    logger.info("Bulk generator: %d cases, %d invoices, %d POs, %d GRNs",
                stats["cases"], stats["invoices"], stats["pos"], stats["grns"])
    return stats
