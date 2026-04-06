"""Management command: seed_procurement_requests

Creates a varied set of sample ProcurementRequest records with attributes,
supplier quotations, line items, and completed analysis runs so the
Procurement workspace UI has real data to browse immediately.

Usage:
    python manage.py seed_procurement_requests
    python manage.py seed_procurement_requests --flush   # wipe and re-seed
    python manage.py seed_procurement_requests --count 10  # create N requests
"""
from __future__ import annotations

import decimal
import logging
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.core.enums import (
    AnalysisRunStatus,
    AnalysisRunType,
    AttributeDataType,
    BenchmarkRiskLevel,
    ComplianceStatus,
    ExtractionSourceType,
    ProcurementRequestStatus,
    ProcurementRequestType,
    VarianceStatus,
)
from apps.procurement.models import (
    AnalysisRun,
    BenchmarkResult,
    BenchmarkResultLine,
    ComplianceResult,
    ProcurementRequest,
    ProcurementRequestAttribute,
    QuotationLineItem,
    RecommendationResult,
    SupplierQuotation,
)

logger = logging.getLogger(__name__)
User = get_user_model()

# ---------------------------------------------------------------------------
# Request templates — each describes one full procurement request
# ---------------------------------------------------------------------------

REQUESTS = [
    # -----------------------------------------------------------------------
    # 1. HVAC — Large Superstore (COMPLETED, full recommendation)
    # -----------------------------------------------------------------------
    {
        "title": "HVAC System Upgrade – Central Superstore, Dubai Mall",
        "description": (
            "Full replacement of the central-plant HVAC system for a 14,500 sqm "
            "retail superstore. The existing Carrier centrifugal chillers are 18 years "
            "old. Requirement: chilled-water-based central plant, minimum 5-star DEWA "
            "efficiency rating, BMS integration."
        ),
        "domain_code": "HVAC",
        "schema_code": "HVAC_CENTRAL_PLANT",
        "request_type": ProcurementRequestType.BOTH,
        "status": ProcurementRequestStatus.COMPLETED,
        "priority": "HIGH",
        "geography_country": "UAE",
        "geography_city": "Dubai",
        "currency": "AED",
        "attributes": [
            ("store_type", "Store Type", AttributeDataType.TEXT, "Superstore", None, None),
            ("area_sqm", "Floor Area (sqm)", AttributeDataType.NUMBER, "", decimal.Decimal("14500"), None),
            ("zone_count", "Cooling Zones", AttributeDataType.NUMBER, "", decimal.Decimal("8"), None),
            ("cooling_load_tr", "Cooling Load (TR)", AttributeDataType.NUMBER, "", decimal.Decimal("420"), None),
            ("ambient_temp_max", "Max Ambient Temp (C)", AttributeDataType.NUMBER, "", decimal.Decimal("47"), None),
            ("chilled_water_available", "Chilled Water Available", AttributeDataType.TEXT, "Yes", None, None),
            ("outdoor_unit_restriction", "Outdoor Unit Restriction", AttributeDataType.TEXT, "None", None, None),
            ("budget_category", "Budget Category", AttributeDataType.TEXT, "Capex", None, None),
            ("efficiency_priority", "Efficiency Priority", AttributeDataType.TEXT, "High", None, None),
            ("dust_level", "Dust Level", AttributeDataType.TEXT, "Moderate", None, None),
            ("humidity_level", "Humidity Level", AttributeDataType.TEXT, "Low", None, None),
        ],
        "quotations": [
            {
                "vendor_name": "Carrier Middle East LLC",
                "quotation_reference": "CME-2026-0441",
                "total_amount": decimal.Decimal("2850000.00"),
                "currency": "AED",
                "validity_days": 60,
                "lines": [
                    ("Centrifugal Chiller 200TR x2", "Unit", 2, decimal.Decimal("780000"), decimal.Decimal("175000")),
                    ("Cooling Tower Package", "Unit", 1, decimal.Decimal("320000"), decimal.Decimal("290000")),
                    ("Chilled Water Pump Set", "Set", 1, decimal.Decimal("195000"), decimal.Decimal("180000")),
                    ("BMS Integration & Commissioning", "Lump Sum", 1, decimal.Decimal("210000"), decimal.Decimal("200000")),
                    ("Installation & Civil Works", "Lump Sum", 1, decimal.Decimal("580000"), decimal.Decimal("550000")),
                ],
            },
            {
                "vendor_name": "Johnson Controls UAE",
                "quotation_reference": "JCI-UAE-7821",
                "total_amount": decimal.Decimal("3100000.00"),
                "currency": "AED",
                "validity_days": 45,
                "lines": [
                    ("York Centrifugal Chiller 200TR x2", "Unit", 2, decimal.Decimal("870000"), decimal.Decimal("175000")),
                    ("Cooling Tower Package", "Unit", 1, decimal.Decimal("340000"), decimal.Decimal("290000")),
                    ("Chilled Water Pump Set", "Set", 1, decimal.Decimal("210000"), decimal.Decimal("180000")),
                    ("BMS Integration & Commissioning", "Lump Sum", 1, decimal.Decimal("240000"), decimal.Decimal("200000")),
                    ("Installation & Civil Works", "Lump Sum", 1, decimal.Decimal("640000"), decimal.Decimal("550000")),
                ],
            },
        ],
        "runs": [
            {
                "run_type": AnalysisRunType.RECOMMENDATION,
                "status": AnalysisRunStatus.COMPLETED,
                "recommendation": {
                    "recommended_solution": "Carrier 19XR Centrifugal Chiller Package (2 x 200TR)",
                    "recommended_category": "CENTRAL_PLANT_CHILLER",
                    "confidence_score": 0.91,
                    "recommendation_text": (
                        "Based on the 14,500 sqm floor area, cooling load of 420TR, and the "
                        "requirement for chilled-water based central plant with DEWA 5-star "
                        "compliance, the Carrier 19XR two-chiller configuration provides the "
                        "best lifecycle efficiency (COP 6.8) at a competitive capital cost. "
                        "Carrier Middle East's AED 2.85M quotation is 8.4% below benchmark "
                        "and includes BMS integration."
                    ),
                    "alternatives_json": [
                        {"solution": "Trane CenTraVac CDHG", "reason_not_selected": "Lead time 20 weeks vs 14 for Carrier"},
                        {"solution": "McQuay MicroTech III", "reason_not_selected": "No local service centre in Dubai"},
                    ],
                    "compliance_status": ComplianceStatus.PASS,
                },
            },
            {
                "run_type": AnalysisRunType.BENCHMARK,
                "status": AnalysisRunStatus.COMPLETED,
                "benchmark_lines": [
                    ("Centrifugal Chiller 200TR x2", decimal.Decimal("780000"), decimal.Decimal("175000"), "WITHIN_RANGE", BenchmarkRiskLevel.LOW),
                    ("Cooling Tower Package", decimal.Decimal("320000"), decimal.Decimal("290000"), "ABOVE_BENCHMARK", BenchmarkRiskLevel.MEDIUM),
                    ("BMS Integration & Commissioning", decimal.Decimal("210000"), decimal.Decimal("200000"), "WITHIN_RANGE", BenchmarkRiskLevel.LOW),
                ],
            },
        ],
    },

    # -----------------------------------------------------------------------
    # 2. IT Infrastructure — Data Centre UPS (REVIEW_REQUIRED)
    # -----------------------------------------------------------------------
    {
        "title": "Data Centre UPS Replacement — Abu Dhabi HQ",
        "description": (
            "Replace two legacy APC Symmetra 80kVA UPS units in the primary DC. "
            "New requirement: N+1 redundancy, 10-minute runtime at full load, "
            "SNMP/NMC interface, 3-year on-site warranty."
        ),
        "domain_code": "IT",
        "schema_code": "IT_POWER",
        "request_type": ProcurementRequestType.BENCHMARK,
        "status": ProcurementRequestStatus.REVIEW_REQUIRED,
        "priority": "CRITICAL",
        "geography_country": "UAE",
        "geography_city": "Abu Dhabi",
        "currency": "USD",
        "attributes": [
            ("it_category", "IT Category", AttributeDataType.TEXT, "Power Infrastructure", None, None),
            ("ups_capacity_kva", "UPS Capacity (kVA)", AttributeDataType.NUMBER, "", decimal.Decimal("80"), None),
            ("redundancy_level", "Redundancy Level", AttributeDataType.TEXT, "N+1", None, None),
            ("runtime_minutes", "Runtime at Full Load (min)", AttributeDataType.NUMBER, "", decimal.Decimal("10"), None),
            ("battery_technology", "Battery Technology", AttributeDataType.TEXT, "Li-Ion preferred", None, None),
            ("management_interface", "Management Interface", AttributeDataType.TEXT, "SNMP / NMC", None, None),
            ("warranty_years", "Warranty (Years)", AttributeDataType.NUMBER, "", decimal.Decimal("3"), None),
            ("installation_type", "Installation Type", AttributeDataType.TEXT, "Rack-mount", None, None),
        ],
        "quotations": [
            {
                "vendor_name": "APC by Schneider Electric",
                "quotation_reference": "SE-AUH-20260318",
                "total_amount": decimal.Decimal("148000.00"),
                "currency": "USD",
                "validity_days": 30,
                "lines": [
                    ("Galaxy VM 80kVA UPS x2", "Unit", 2, decimal.Decimal("62000"), decimal.Decimal("55000")),
                    ("Li-Ion Battery Module 40kWh x2", "Unit", 2, decimal.Decimal("8500"), decimal.Decimal("7200")),
                    ("Network Management Card x2", "Unit", 2, decimal.Decimal("1100"), decimal.Decimal("950")),
                    ("Installation & Load Testing", "Lump Sum", 1, decimal.Decimal("5800"), decimal.Decimal("4800")),
                ],
            },
            {
                "vendor_name": "Eaton Corporation ME",
                "quotation_reference": "EATON-ME-4421",
                "total_amount": decimal.Decimal("161000.00"),
                "currency": "USD",
                "validity_days": 30,
                "lines": [
                    ("Eaton 9PX 80kVA UPS x2", "Unit", 2, decimal.Decimal("68000"), decimal.Decimal("55000")),
                    ("Li-Ion Battery Cabinet x2", "Unit", 2, decimal.Decimal("9500"), decimal.Decimal("7200")),
                    ("SNMP Management Card x2", "Unit", 2, decimal.Decimal("1200"), decimal.Decimal("950")),
                    ("Installation & Commissioning", "Lump Sum", 1, decimal.Decimal("4900"), decimal.Decimal("4800")),
                ],
            },
        ],
        "runs": [
            {
                "run_type": AnalysisRunType.BENCHMARK,
                "status": AnalysisRunStatus.COMPLETED,
                "benchmark_lines": [
                    ("Galaxy VM 80kVA UPS x2", decimal.Decimal("62000"), decimal.Decimal("55000"), "ABOVE_BENCHMARK", BenchmarkRiskLevel.MEDIUM),
                    ("Li-Ion Battery Module 40kWh x2", decimal.Decimal("8500"), decimal.Decimal("7200"), "SIGNIFICANTLY_ABOVE", BenchmarkRiskLevel.HIGH),
                    ("Installation & Load Testing", decimal.Decimal("5800"), decimal.Decimal("4800"), "ABOVE_BENCHMARK", BenchmarkRiskLevel.MEDIUM),
                ],
            },
        ],
    },

    # -----------------------------------------------------------------------
    # 3. Facilities — Office Fit-Out (READY, no runs yet)
    # -----------------------------------------------------------------------
    {
        "title": "Office Fit-Out — New Regional HQ, Riyadh Tower B",
        "description": (
            "Complete fit-out of 3,200 sqm Grade-A office space across floors 14-16 "
            "of Tower B, Riyadh Business Gate. Includes raised flooring, glass "
            "partitions, workstations, server room, reception, 4 meeting rooms and "
            "executive suite. Hand-over deadline: Q3 2026."
        ),
        "domain_code": "FACILITIES",
        "schema_code": "FITOUT_OFFICE",
        "request_type": ProcurementRequestType.BOTH,
        "status": ProcurementRequestStatus.READY,
        "priority": "HIGH",
        "geography_country": "Saudi Arabia",
        "geography_city": "Riyadh",
        "currency": "SAR",
        "attributes": [
            ("space_type", "Space Type", AttributeDataType.TEXT, "Grade-A Office", None, None),
            ("area_sqm", "Total Area (sqm)", AttributeDataType.NUMBER, "", decimal.Decimal("3200"), None),
            ("floors", "Number of Floors", AttributeDataType.NUMBER, "", decimal.Decimal("3"), None),
            ("workstation_count", "Workstations", AttributeDataType.NUMBER, "", decimal.Decimal("220"), None),
            ("meeting_rooms", "Meeting Rooms", AttributeDataType.NUMBER, "", decimal.Decimal("4"), None),
            ("server_room_required", "Server Room Required", AttributeDataType.TEXT, "Yes", None, None),
            ("flooring_type", "Flooring Type", AttributeDataType.TEXT, "Raised Access Floor + Carpet Tiles", None, None),
            ("handover_deadline", "Handover Deadline", AttributeDataType.TEXT, "Q3 2026", None, None),
            ("vat_applicability", "VAT Applicability", AttributeDataType.TEXT, "15% KSA VAT", None, None),
        ],
        "quotations": [
            {
                "vendor_name": "Areen Design Consultants",
                "quotation_reference": "AREEN-RUH-0189",
                "total_amount": decimal.Decimal("4200000.00"),
                "currency": "SAR",
                "validity_days": 45,
                "lines": [
                    ("Raised Access Flooring 3200sqm", "sqm", 3200, decimal.Decimal("450"), decimal.Decimal("420")),
                    ("Glass Partition System", "sqm", 1800, decimal.Decimal("380"), decimal.Decimal("350")),
                    ("Workstation & Seating Package (220)", "Unit", 220, decimal.Decimal("4200"), decimal.Decimal("3900")),
                    ("Meeting Room AV & Furniture (4 rooms)", "Room", 4, decimal.Decimal("85000"), decimal.Decimal("75000")),
                    ("Server Room (incl. cooling + power)", "Lump Sum", 1, decimal.Decimal("320000"), decimal.Decimal("300000")),
                    ("Lighting Upgrade", "sqm", 3200, decimal.Decimal("95"), decimal.Decimal("88")),
                ],
            },
        ],
        "runs": [],
    },

    # -----------------------------------------------------------------------
    # 4. Food Retail — Cold Chain Refrigeration (PROCESSING)
    # -----------------------------------------------------------------------
    {
        "title": "Cold Chain Refrigeration Upgrade — 12 Hypermarket Branches",
        "description": (
            "Multi-site roll-out of energy-efficient commercial refrigeration across "
            "12 hypermarket branches in the GCC. Each store requires: produce display "
            "cases, frozen food islands, walk-in cold rooms, and centralised "
            "monitoring. CO2 or natural refrigerant preferred (F-Gas Phase-Down)."
        ),
        "domain_code": "FOOD_RETAIL",
        "schema_code": "COLD_CHAIN",
        "request_type": ProcurementRequestType.BOTH,
        "status": ProcurementRequestStatus.PROCESSING,
        "priority": "HIGH",
        "geography_country": "UAE",
        "geography_city": "Multiple",
        "currency": "USD",
        "attributes": [
            ("site_count", "Number of Sites", AttributeDataType.NUMBER, "", decimal.Decimal("12"), None),
            ("store_avg_area_sqm", "Average Store Area (sqm)", AttributeDataType.NUMBER, "", decimal.Decimal("8500"), None),
            ("refrigerant_preference", "Refrigerant Preference", AttributeDataType.TEXT, "CO2 Transcritical / Natural", None, None),
            ("display_case_linear_m", "Display Cases (linear m/store)", AttributeDataType.NUMBER, "", decimal.Decimal("320"), None),
            ("walkin_rooms_per_store", "Walk-in Cold Rooms per Store", AttributeDataType.NUMBER, "", decimal.Decimal("6"), None),
            ("centralised_monitoring", "Centralised Monitoring Required", AttributeDataType.TEXT, "Yes — cloud-based", None, None),
            ("energy_star_required", "Energy Label Requirement", AttributeDataType.TEXT, "EU Energy Label A++", None, None),
            ("installation_window", "Installation Window per Store", AttributeDataType.TEXT, "5 days (overnight + weekend)", None, None),
        ],
        "quotations": [
            {
                "vendor_name": "Danfoss Middle East",
                "quotation_reference": "DANF-GCC-2026-004",
                "total_amount": decimal.Decimal("9600000.00"),
                "currency": "USD",
                "validity_days": 90,
                "lines": [
                    ("CO2 Condensing Unit Pack (per store)", "Unit", 12, decimal.Decimal("185000"), decimal.Decimal("170000")),
                    ("Produce Display Cases 320lm (per store)", "lm", 3840, decimal.Decimal("1100"), decimal.Decimal("980")),
                    ("Frozen Food Island 60lm (per store)", "lm", 720, decimal.Decimal("1450"), decimal.Decimal("1300")),
                    ("Walk-in Cold Room 6 units (per store)", "Unit", 72, decimal.Decimal("28000"), decimal.Decimal("25000")),
                    ("Cloud Monitoring Platform (12 sites)", "Lump Sum", 1, decimal.Decimal("240000"), decimal.Decimal("200000")),
                ],
            },
            {
                "vendor_name": "Emerson Commercial & Residential Solutions",
                "quotation_reference": "EMRSON-ME-RC-019",
                "total_amount": decimal.Decimal("10450000.00"),
                "currency": "USD",
                "validity_days": 60,
                "lines": [
                    ("Copeland CO2 Rack (per store)", "Unit", 12, decimal.Decimal("205000"), decimal.Decimal("170000")),
                    ("Hussmann Display Cases 320lm (per store)", "lm", 3840, decimal.Decimal("1180"), decimal.Decimal("980")),
                    ("Hussmann Frozen Island 60lm (per store)", "lm", 720, decimal.Decimal("1550"), decimal.Decimal("1300")),
                    ("Walk-in Cold Room 6 units (per store)", "Unit", 72, decimal.Decimal("30000"), decimal.Decimal("25000")),
                    ("E2 Supervisory Controller Platform", "Lump Sum", 1, decimal.Decimal("280000"), decimal.Decimal("200000")),
                ],
            },
        ],
        "runs": [
            {
                "run_type": AnalysisRunType.VALIDATION,
                "status": AnalysisRunStatus.RUNNING,
                "benchmark_lines": [],
            }
        ],
    },

    # -----------------------------------------------------------------------
    # 5. IT — Laptop Fleet Refresh (DRAFT)
    # -----------------------------------------------------------------------
    {
        "title": "Laptop Fleet Refresh — 500 Units for Finance & Operations Teams",
        "description": (
            "Replacement of 500 aging laptops (5+ years) across Finance, Operations, "
            "and HR departments. Requirement: 14-inch, Intel Core i7 / AMD Ryzen 7 "
            "class, 16GB RAM, 512GB NVMe SSD, Windows 11 Pro, 3-year on-site warranty "
            "with next-business-day response."
        ),
        "domain_code": "IT",
        "schema_code": "IT_ENDPOINTS",
        "request_type": ProcurementRequestType.BENCHMARK,
        "status": ProcurementRequestStatus.DRAFT,
        "priority": "MEDIUM",
        "geography_country": "UAE",
        "geography_city": "Dubai",
        "currency": "USD",
        "attributes": [
            ("device_type", "Device Type", AttributeDataType.TEXT, "Business Laptop", None, None),
            ("quantity", "Quantity", AttributeDataType.NUMBER, "", decimal.Decimal("500"), None),
            ("screen_size_inch", "Screen Size (inch)", AttributeDataType.NUMBER, "", decimal.Decimal("14"), None),
            ("processor_class", "Processor Class", AttributeDataType.TEXT, "Intel Core i7 13th Gen / AMD Ryzen 7 7000", None, None),
            ("ram_gb", "RAM (GB)", AttributeDataType.NUMBER, "", decimal.Decimal("16"), None),
            ("storage_nvme_gb", "NVMe SSD (GB)", AttributeDataType.NUMBER, "", decimal.Decimal("512"), None),
            ("os_required", "Operating System", AttributeDataType.TEXT, "Windows 11 Pro", None, None),
            ("warranty_type", "Warranty Type", AttributeDataType.TEXT, "3-Year On-Site NBD", None, None),
            ("deployment_timeline", "Deployment Timeline", AttributeDataType.TEXT, "8 weeks from PO", None, None),
        ],
        "quotations": [],
        "runs": [],
    },
]


class Command(BaseCommand):
    help = "Seed sample procurement requests with attributes, quotations, and analysis runs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete all existing procurement requests before seeding.",
        )
        parser.add_argument(
            "--count",
            type=int,
            default=None,
            help="Limit the number of requests to seed (default: all).",
        )

    def handle(self, *args, **options):
        if options["flush"]:
            deleted, _ = ProcurementRequest.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Flushed {deleted} existing procurement records."))

        # Resolve a user to assign requests to
        user = (
            User.objects.filter(email__icontains="admin").first()
            or User.objects.filter(is_superuser=True).first()
            or User.objects.first()
        )
        if not user:
            self.stderr.write(self.style.ERROR("No users found. Run seed_rbac first."))
            return

        templates = REQUESTS[: options["count"]] if options["count"] else REQUESTS
        created_count = 0

        for tmpl in templates:
            try:
                req = self._create_request(tmpl, user)
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  [{tmpl['status']:18s}] {req.title[:70]}"
                    )
                )
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"  FAILED: {tmpl['title'][:60]} — {exc}"))
                logger.exception("seed_procurement_requests: failed for %s", tmpl["title"])

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(f"Done. Created {created_count}/{len(templates)} procurement requests.")
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _create_request(self, tmpl: dict, user) -> ProcurementRequest:
        """Create one full ProcurementRequest with all related objects.

        The template format is intentionally tolerant:
        - Line tuples may have 4 OR 5 elements (5th legacy benchmark price is ignored).
        - Recommendation dicts accept both old keys (recommended_solution,
          recommendation_text) and new keys (recommended_option, reasoning_summary).
        - Benchmark lines may use a string description OR an int index to resolve
          the corresponding QuotationLineItem FK.
        - Quotation dicts accept both "quotation_number" and legacy "quotation_reference".
        """
        base_at = timezone.now() - timedelta(days=14)

        # ------------------------------------------------------------------
        # ProcurementRequest
        # ------------------------------------------------------------------
        req = ProcurementRequest.objects.create(
            title=tmpl["title"],
            description=tmpl["description"],
            domain_code=tmpl["domain_code"],
            schema_code=tmpl["schema_code"],
            request_type=tmpl["request_type"],
            status=tmpl["status"],
            priority=tmpl["priority"],
            geography_country=tmpl["geography_country"],
            geography_city=tmpl["geography_city"],
            currency=tmpl["currency"],
            assigned_to=user,
            trace_id=uuid.uuid4().hex,
            created_by=user,
        )

        # ------------------------------------------------------------------
        # Attributes
        # ------------------------------------------------------------------
        for code, label, dtype, val_text, val_num, val_json in tmpl.get("attributes", []):
            ProcurementRequestAttribute.objects.create(
                request=req,
                attribute_code=code,
                attribute_label=label,
                data_type=dtype,
                value_text=val_text or "",
                value_number=val_num,
                value_json=val_json,
                is_required=True,
                extraction_source=ExtractionSourceType.MANUAL,
            )

        # ------------------------------------------------------------------
        # Quotations + Line Items
        # ------------------------------------------------------------------
        # vendor_name -> SupplierQuotation for run lookup below
        quotation_map: dict = {}

        for q_data in tmpl.get("quotations", []):
            # Accept both "quotation_number" (new) and "quotation_reference" (legacy)
            q_number = q_data.get("quotation_number", q_data.get("quotation_reference", ""))
            quote = SupplierQuotation.objects.create(
                request=req,
                vendor_name=q_data["vendor_name"],
                quotation_number=q_number,
                quotation_date=base_at.date(),
                total_amount=q_data["total_amount"],
                currency=q_data["currency"],
                created_by=user,
            )
            quotation_map[q_data["vendor_name"]] = quote

            # Tuple may be 4 or 5 elements — slice to 4 to drop legacy benchmark col
            for i, line_tuple in enumerate(q_data.get("lines", []), 1):
                desc, unit, qty, unit_rate = line_tuple[:4]
                QuotationLineItem.objects.create(
                    quotation=quote,
                    line_number=i,
                    description=desc,
                    unit=unit,
                    quantity=decimal.Decimal(str(qty)),
                    unit_rate=unit_rate,
                    total_amount=unit_rate * qty,
                    extraction_source=ExtractionSourceType.MANUAL,
                )

        # ------------------------------------------------------------------
        # Analysis Runs
        # ------------------------------------------------------------------
        first_vendor = tmpl["quotations"][0]["vendor_name"] if tmpl.get("quotations") else None

        for run_tmpl in tmpl.get("runs", []):
            is_completed = run_tmpl["status"] == AnalysisRunStatus.COMPLETED
            run_at = base_at + timedelta(days=3)
            run = AnalysisRun.objects.create(
                request=req,
                run_id=uuid.uuid4(),
                run_type=run_tmpl["run_type"],
                status=run_tmpl["status"],
                started_at=run_at if run_tmpl["status"] != AnalysisRunStatus.QUEUED else None,
                completed_at=(run_at + timedelta(minutes=5)) if is_completed else None,
                triggered_by=user,
                trace_id=uuid.uuid4().hex,
                created_by=user,
            )

            # RecommendationResult + ComplianceResult
            if run_tmpl["run_type"] == AnalysisRunType.RECOMMENDATION and is_completed:
                rec = run_tmpl.get("recommendation", {})
                # Accept both old key names and new key names
                recommended_option = rec.get(
                    "recommended_option", rec.get("recommended_solution", "N/A")
                )
                reasoning_summary = rec.get(
                    "reasoning_summary", rec.get("recommendation_text", "")
                )
                details = rec.get("reasoning_details_json")
                if details is None:
                    details = {
                        "alternatives": rec.get("alternatives_json", []),
                        "category": rec.get("recommended_category", ""),
                    }
                RecommendationResult.objects.create(
                    run=run,
                    recommended_option=recommended_option,
                    reasoning_summary=reasoning_summary,
                    reasoning_details_json=details,
                    confidence_score=rec.get("confidence_score", 0.8),
                    compliance_status=rec.get("compliance_status", ComplianceStatus.NOT_CHECKED),
                )
                ComplianceResult.objects.create(
                    run=run,
                    compliance_status=rec.get("compliance_status", ComplianceStatus.PASS),
                    rules_checked_json=[],
                    violations_json=[],
                    recommendations_json=[],
                )

            # BenchmarkResult + BenchmarkResultLines
            if (
                run_tmpl["run_type"] == AnalysisRunType.BENCHMARK
                and is_completed
                and first_vendor
                and run_tmpl.get("benchmark_lines")
            ):
                first_quote = quotation_map.get(first_vendor)
                if not first_quote:
                    continue

                bench_lines = run_tmpl["benchmark_lines"]

                # Build lookup structures for the line items
                line_by_desc = {
                    li.description: li
                    for li in first_quote.line_items.all()
                }
                line_by_idx = list(first_quote.line_items.order_by("line_number"))

                total_quoted = sum(row[1] for row in bench_lines)
                total_bench = sum(row[2] for row in bench_lines)
                overall_var = (
                    ((total_quoted - total_bench) / total_bench * 100)
                    if total_bench
                    else decimal.Decimal("0")
                )
                worst_risk = (
                    BenchmarkRiskLevel.HIGH
                    if any(row[4] == BenchmarkRiskLevel.HIGH for row in bench_lines)
                    else BenchmarkRiskLevel.MEDIUM
                )
                above_count = sum(
                    1 for row in bench_lines if "ABOVE" in str(row[3])
                )

                b_result = BenchmarkResult.objects.create(
                    run=run,
                    quotation=first_quote,
                    total_quoted_amount=first_quote.total_amount,
                    total_benchmark_amount=total_bench,
                    variance_pct=overall_var,
                    risk_level=worst_risk,
                    summary_json={
                        "above_benchmark_lines": above_count,
                        "source": "Market survey Q1 2026",
                    },
                )

                for desc_or_idx, quoted_val, bench_avg, variance_status, risk in bench_lines:
                    # Support both string-description (old) and int-index (new) format
                    if isinstance(desc_or_idx, int):
                        q_line = line_by_idx[desc_or_idx] if desc_or_idx < len(line_by_idx) else None
                    else:
                        q_line = line_by_desc.get(desc_or_idx)
                    if not q_line:
                        continue

                    line_var_pct = (
                        ((quoted_val - bench_avg) / bench_avg * 100)
                        if bench_avg
                        else decimal.Decimal("0")
                    )
                    # Accept both enum instances and raw string values
                    vs_value = (
                        variance_status.value
                        if hasattr(variance_status, "value")
                        else str(variance_status)
                    )
                    BenchmarkResultLine.objects.create(
                        benchmark_result=b_result,
                        quotation_line=q_line,
                        quoted_value=quoted_val,
                        benchmark_avg=bench_avg,
                        benchmark_min=bench_avg * decimal.Decimal("0.95"),
                        benchmark_max=bench_avg * decimal.Decimal("1.10"),
                        variance_pct=line_var_pct,
                        variance_status=vs_value,
                        remarks=f"Market Q1-2026 | risk: {risk}",
                    )

        return req
