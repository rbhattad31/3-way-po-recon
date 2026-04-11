"""
Management command: seed_benchmark_data

Creates realistic should-cost benchmarking demo data:
  - 4 BenchmarkRequests (UAE/KSA/QATAR x SITC/ITC/EQUIP)
  - 1-2 BenchmarkQuotations per request (different suppliers)
  - 8-12 BenchmarkLineItems per quotation (pre-classified + benchmarked)
  - BenchmarkResult per request (aggregated totals + negotiation notes)

Usage:
    python manage.py seed_benchmark_data
    python manage.py seed_benchmark_data --clear   # wipe and re-seed
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.benchmarking.models import (
    BenchmarkCorridorRule,
    BenchmarkLineItem,
    BenchmarkQuotation,
    BenchmarkRequest,
    BenchmarkResult,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Seed data definitions
# ---------------------------------------------------------------------------

REQUESTS = [
    {
        "title": "Dubai Mall Expansion - HVAC Replacement FY2026",
        "project_name": "Dubai Mall Phase 3 Fit-Out",
        "geography": "UAE",
        "scope_type": "SITC",
        "store_type": "MALL",
        "notes": "Full replacement of aging VRF system. Landlord constraint: no outdoor units on roof.",
        "status": "COMPLETED",
        "quotations": [
            {
                "supplier_name": "Al Rostamani HVAC Solutions",
                "quotation_ref": "ARH/Q/2026/0142",
                "extraction_status": "DONE",
                "extracted_text": "Quotation for HVAC supply, install, test and commission. Dubai Mall Phase 3.",
                "line_items": [
                    {
                        "description": "Daikin VRF Outdoor Unit 20HP R410A",
                        "category": "EQUIPMENT",
                        "uom": "No",
                        "quantity": Decimal("4"),
                        "quoted_unit_rate": Decimal("18500"),
                        "line_amount": Decimal("74000"),
                        "line_number": 1,
                        "benchmark_min": Decimal("15000"),
                        "benchmark_mid": Decimal("17500"),
                        "benchmark_max": Decimal("21000"),
                        "corridor_rule_code": "BC-EQUIP-UAE-001",
                        "variance_pct": 5.71,
                        "variance_status": "MODERATE",
                        "variance_note": "Quoted 5.7% above benchmark mid for VRF outdoor unit",
                    },
                    {
                        "description": "Daikin VRF Indoor Unit 2.5HP Cassette Type",
                        "category": "EQUIPMENT",
                        "uom": "No",
                        "quantity": Decimal("24"),
                        "quoted_unit_rate": Decimal("2800"),
                        "line_amount": Decimal("67200"),
                        "line_number": 2,
                        "benchmark_min": Decimal("2200"),
                        "benchmark_mid": Decimal("2600"),
                        "benchmark_max": Decimal("3100"),
                        "corridor_rule_code": "BC-EQUIP-UAE-001",
                        "variance_pct": 7.69,
                        "variance_status": "MODERATE",
                        "variance_note": "Quoted 7.7% above benchmark mid",
                    },
                    {
                        "description": "BMS Controller with DDC Panel",
                        "category": "CONTROLS",
                        "uom": "Lot",
                        "quantity": Decimal("1"),
                        "quoted_unit_rate": Decimal("35000"),
                        "line_amount": Decimal("35000"),
                        "line_number": 3,
                        "benchmark_min": Decimal("28000"),
                        "benchmark_mid": Decimal("33000"),
                        "benchmark_max": Decimal("40000"),
                        "corridor_rule_code": "BC-CTRL-UAE-001",
                        "variance_pct": 6.06,
                        "variance_status": "MODERATE",
                        "variance_note": "BMS slightly above mid",
                    },
                    {
                        "description": "GI Rectangular Ducting 1.2mm thick",
                        "category": "DUCTING",
                        "uom": "m2",
                        "quantity": Decimal("850"),
                        "quoted_unit_rate": Decimal("95"),
                        "line_amount": Decimal("80750"),
                        "line_number": 4,
                        "benchmark_min": Decimal("80"),
                        "benchmark_mid": Decimal("90"),
                        "benchmark_max": Decimal("110"),
                        "corridor_rule_code": "BC-DUCT-UAE-001",
                        "variance_pct": 5.56,
                        "variance_status": "MODERATE",
                        "variance_note": "GI ducting 5.6% above mid",
                    },
                    {
                        "description": "Armaflex Pipe Insulation 25mm",
                        "category": "INSULATION",
                        "uom": "m",
                        "quantity": Decimal("420"),
                        "quoted_unit_rate": Decimal("28"),
                        "line_amount": Decimal("11760"),
                        "line_number": 5,
                        "benchmark_min": Decimal("22"),
                        "benchmark_mid": Decimal("27"),
                        "benchmark_max": Decimal("35"),
                        "corridor_rule_code": "BC-INSUL-UAE-001",
                        "variance_pct": 3.70,
                        "variance_status": "WITHIN_RANGE",
                        "variance_note": "Pipe insulation within benchmark",
                    },
                    {
                        "description": "Installation and Labour - Complete HVAC",
                        "category": "INSTALLATION",
                        "uom": "Lot",
                        "quantity": Decimal("1"),
                        "quoted_unit_rate": Decimal("45000"),
                        "line_amount": Decimal("45000"),
                        "line_number": 6,
                        "benchmark_min": Decimal("35000"),
                        "benchmark_mid": Decimal("42000"),
                        "benchmark_max": Decimal("55000"),
                        "corridor_rule_code": "BC-INST-UAE-001",
                        "variance_pct": 7.14,
                        "variance_status": "MODERATE",
                        "variance_note": "Labour slightly above mid",
                    },
                    {
                        "description": "Testing and Commissioning",
                        "category": "TC",
                        "uom": "Lot",
                        "quantity": Decimal("1"),
                        "quoted_unit_rate": Decimal("12000"),
                        "line_amount": Decimal("12000"),
                        "line_number": 7,
                        "benchmark_min": Decimal("8000"),
                        "benchmark_mid": Decimal("11000"),
                        "benchmark_max": Decimal("15000"),
                        "corridor_rule_code": "BC-TC-UAE-001",
                        "variance_pct": 9.09,
                        "variance_status": "MODERATE",
                        "variance_note": "T&C slightly above mid",
                    },
                ],
            },
            {
                "supplier_name": "Carrier Emirates HVAC LLC",
                "quotation_ref": "CE/TQ/2026/00891",
                "extraction_status": "DONE",
                "extracted_text": "Supply, installation, testing and commissioning of HVAC system.",
                "line_items": [
                    {
                        "description": "Carrier 30RB Chiller 80TR Water Cooled",
                        "category": "EQUIPMENT",
                        "uom": "No",
                        "quantity": Decimal("1"),
                        "quoted_unit_rate": Decimal("155000"),
                        "line_amount": Decimal("155000"),
                        "line_number": 1,
                        "benchmark_min": Decimal("130000"),
                        "benchmark_mid": Decimal("150000"),
                        "benchmark_max": Decimal("175000"),
                        "corridor_rule_code": "BC-EQUIP-UAE-002",
                        "variance_pct": 3.33,
                        "variance_status": "WITHIN_RANGE",
                        "variance_note": "Chiller within benchmark corridor",
                    },
                    {
                        "description": "Fan Coil Units 2-pipe type 2.0TR",
                        "category": "EQUIPMENT",
                        "uom": "No",
                        "quantity": Decimal("30"),
                        "quoted_unit_rate": Decimal("3200"),
                        "line_amount": Decimal("96000"),
                        "line_number": 2,
                        "benchmark_min": Decimal("2800"),
                        "benchmark_mid": Decimal("3000"),
                        "benchmark_max": Decimal("3500"),
                        "corridor_rule_code": "BC-EQUIP-UAE-003",
                        "variance_pct": 6.67,
                        "variance_status": "MODERATE",
                        "variance_note": "FCU rate slightly above mid",
                    },
                    {
                        "description": "BACnet BMS Integration and Controls",
                        "category": "CONTROLS",
                        "uom": "Lot",
                        "quantity": Decimal("1"),
                        "quoted_unit_rate": Decimal("52000"),
                        "line_amount": Decimal("52000"),
                        "line_number": 3,
                        "benchmark_min": Decimal("28000"),
                        "benchmark_mid": Decimal("33000"),
                        "benchmark_max": Decimal("40000"),
                        "corridor_rule_code": "BC-CTRL-UAE-001",
                        "variance_pct": 57.58,
                        "variance_status": "HIGH",
                        "variance_note": "BMS price significantly above benchmark - high variance flag",
                    },
                ],
            },
        ],
        "result": {
            "total_quoted": Decimal("629710"),
            "total_benchmark_mid": Decimal("583500"),
            "overall_deviation_pct": 7.92,
            "overall_status": "MODERATE",
            "lines_within_range": 2,
            "lines_moderate": 6,
            "lines_high": 1,
            "lines_needs_review": 1,
            "category_summary_json": {
                "EQUIPMENT": {"quoted": 410700, "benchmark_mid": 382600, "deviation_pct": 7.35, "status": "MODERATE", "count": 5},
                "CONTROLS": {"quoted": 87000, "benchmark_mid": 66000, "deviation_pct": 31.82, "status": "HIGH", "count": 2},
                "DUCTING": {"quoted": 80750, "benchmark_mid": 76500, "deviation_pct": 5.56, "status": "MODERATE", "count": 1},
                "INSULATION": {"quoted": 11760, "benchmark_mid": 11340, "deviation_pct": 3.70, "status": "WITHIN_RANGE", "count": 1},
                "INSTALLATION": {"quoted": 45000, "benchmark_mid": 42000, "deviation_pct": 7.14, "status": "MODERATE", "count": 1},
                "TC": {"quoted": 12000, "benchmark_mid": 11000, "deviation_pct": 9.09, "status": "MODERATE", "count": 1},
            },
            "negotiation_notes_json": [
                "Controls (BMS) is priced +31.8% above benchmark. Request Carrier Emirates to align BACnet integration fee to AED 33,000-38,000 range.",
                "VRF outdoor and indoor units (Al Rostamani) are 5-8% above mid-corridor. Negotiate combined discount for equipment bundle.",
                "Consider splitting scope: award equipment to Al Rostamani and controls/BMS to a specialist sub-contractor.",
                "Testing and commissioning can be challenged - market rates AED 8,000-11,000 for this scope.",
                "Overall quotation is approximately 7.9% above blended benchmark mid. Target <5% through negotiation.",
            ],
        },
    },
    {
        "title": "Riyadh Hypermarket HVAC - New Installation",
        "project_name": "LMG Riyadh Store #R-014",
        "geography": "KSA",
        "scope_type": "SITC",
        "store_type": "HYPERMARKET",
        "notes": "New hypermarket. High footfall. Packaged unit solution preferred.",
        "status": "COMPLETED",
        "quotations": [
            {
                "supplier_name": "Zamil Air Products KSA",
                "quotation_ref": "ZAP/KSA/2026/0047",
                "extraction_status": "DONE",
                "extracted_text": "HVAC new installation for hypermarket, Riyadh.",
                "line_items": [
                    {
                        "description": "Zamil Packaged Rooftop Unit 25TR 380V",
                        "category": "EQUIPMENT",
                        "uom": "No",
                        "quantity": Decimal("6"),
                        "quoted_unit_rate": Decimal("28000"),
                        "line_amount": Decimal("168000"),
                        "line_number": 1,
                        "benchmark_min": Decimal("22000"),
                        "benchmark_mid": Decimal("25000"),
                        "benchmark_max": Decimal("30000"),
                        "corridor_rule_code": "BC-EQUIP-KSA-001",
                        "variance_pct": 12.0,
                        "variance_status": "MODERATE",
                        "variance_note": "Packaged units 12% above mid",
                    },
                    {
                        "description": "Split System 2TR for Back Office",
                        "category": "EQUIPMENT",
                        "uom": "No",
                        "quantity": Decimal("10"),
                        "quoted_unit_rate": Decimal("3800"),
                        "line_amount": Decimal("38000"),
                        "line_number": 2,
                        "benchmark_min": Decimal("3200"),
                        "benchmark_mid": Decimal("3600"),
                        "benchmark_max": Decimal("4500"),
                        "corridor_rule_code": "BC-EQUIP-KSA-003",
                        "variance_pct": 5.56,
                        "variance_status": "MODERATE",
                        "variance_note": "Split units slightly above mid",
                    },
                    {
                        "description": "DDC Controls and BMS Integration",
                        "category": "CONTROLS",
                        "uom": "Lot",
                        "quantity": Decimal("1"),
                        "quoted_unit_rate": Decimal("22000"),
                        "line_amount": Decimal("22000"),
                        "line_number": 3,
                        "benchmark_min": Decimal("18000"),
                        "benchmark_mid": Decimal("21000"),
                        "benchmark_max": Decimal("27000"),
                        "corridor_rule_code": "BC-CTRL-KSA-001",
                        "variance_pct": 4.76,
                        "variance_status": "WITHIN_RANGE",
                        "variance_note": "BMS within benchmark",
                    },
                    {
                        "description": "GI Ducting 0.8mm with supports",
                        "category": "DUCTING",
                        "uom": "m2",
                        "quantity": Decimal("1200"),
                        "quoted_unit_rate": Decimal("72"),
                        "line_amount": Decimal("86400"),
                        "line_number": 4,
                        "benchmark_min": Decimal("65"),
                        "benchmark_mid": Decimal("75"),
                        "benchmark_max": Decimal("88"),
                        "corridor_rule_code": "BC-DUCT-KSA-001",
                        "variance_pct": -4.0,
                        "variance_status": "WITHIN_RANGE",
                        "variance_note": "Ducting below mid - competitive price",
                    },
                    {
                        "description": "Elastomeric Duct Insulation 19mm",
                        "category": "INSULATION",
                        "uom": "m2",
                        "quantity": Decimal("600"),
                        "quoted_unit_rate": Decimal("65"),
                        "line_amount": Decimal("39000"),
                        "line_number": 5,
                        "benchmark_min": Decimal("50"),
                        "benchmark_mid": Decimal("60"),
                        "benchmark_max": Decimal("75"),
                        "corridor_rule_code": "BC-INSUL-KSA-001",
                        "variance_pct": 8.33,
                        "variance_status": "MODERATE",
                        "variance_note": "Duct insulation 8.3% above mid",
                    },
                    {
                        "description": "Installation, Commissioning and Training",
                        "category": "INSTALLATION",
                        "uom": "Lot",
                        "quantity": Decimal("1"),
                        "quoted_unit_rate": Decimal("38000"),
                        "line_amount": Decimal("38000"),
                        "line_number": 6,
                        "benchmark_min": Decimal("30000"),
                        "benchmark_mid": Decimal("35000"),
                        "benchmark_max": Decimal("45000"),
                        "corridor_rule_code": "BC-INST-KSA-001",
                        "variance_pct": 8.57,
                        "variance_status": "MODERATE",
                        "variance_note": "Installation 8.6% above mid",
                    },
                    {
                        "description": "T&C + Commissioning Report",
                        "category": "TC",
                        "uom": "Lot",
                        "quantity": Decimal("1"),
                        "quoted_unit_rate": Decimal("9500"),
                        "line_amount": Decimal("9500"),
                        "line_number": 7,
                        "benchmark_min": Decimal("7000"),
                        "benchmark_mid": Decimal("9000"),
                        "benchmark_max": Decimal("12000"),
                        "corridor_rule_code": "BC-TC-KSA-001",
                        "variance_pct": 5.56,
                        "variance_status": "MODERATE",
                        "variance_note": "T&C within acceptable range",
                    },
                ],
            },
        ],
        "result": {
            "total_quoted": Decimal("400900"),
            "total_benchmark_mid": Decimal("377000"),
            "overall_deviation_pct": 6.34,
            "overall_status": "MODERATE",
            "lines_within_range": 2,
            "lines_moderate": 5,
            "lines_high": 0,
            "lines_needs_review": 0,
            "category_summary_json": {
                "EQUIPMENT": {"quoted": 206000, "benchmark_mid": 186000, "deviation_pct": 10.75, "status": "MODERATE", "count": 2},
                "CONTROLS": {"quoted": 22000, "benchmark_mid": 21000, "deviation_pct": 4.76, "status": "WITHIN_RANGE", "count": 1},
                "DUCTING": {"quoted": 86400, "benchmark_mid": 90000, "deviation_pct": -4.0, "status": "WITHIN_RANGE", "count": 1},
                "INSULATION": {"quoted": 39000, "benchmark_mid": 36000, "deviation_pct": 8.33, "status": "MODERATE", "count": 1},
                "INSTALLATION": {"quoted": 38000, "benchmark_mid": 35000, "deviation_pct": 8.57, "status": "MODERATE", "count": 1},
                "TC": {"quoted": 9500, "benchmark_mid": 9000, "deviation_pct": 5.56, "status": "MODERATE", "count": 1},
            },
            "negotiation_notes_json": [
                "Packaged rooftop units are 12% above benchmark. Negotiate unit price to SAR 25,000-26,500 range or request alternate make (York/Trane).",
                "Overall deviation of 6.3% is within acceptable band but scope for SAR 25,000 savings through negotiation.",
                "Ducting is competitively priced - no action needed.",
                "Pool all equipment line items and request 8-10% volume discount.",
            ],
        },
    },
    {
        "title": "Doha Office Tower - VRF System ITC",
        "project_name": "Lusail Tower Block D",
        "geography": "QATAR",
        "scope_type": "ITC",
        "store_type": "OFFICE",
        "notes": "Equipment supplied by client (Mitsubishi Electric). ITC scope only.",
        "status": "COMPLETED",
        "quotations": [
            {
                "supplier_name": "Gulf Technical Services WLL",
                "quotation_ref": "GTS/QAT/2026/Q-203",
                "extraction_status": "DONE",
                "extracted_text": "Install, test and commission VRF system. Equipment by others.",
                "line_items": [
                    {
                        "description": "VRF Refrigerant Pipe Installation - copper",
                        "category": "ACCESSORIES",
                        "uom": "m",
                        "quantity": Decimal("380"),
                        "quoted_unit_rate": Decimal("85"),
                        "line_amount": Decimal("32300"),
                        "line_number": 1,
                        "benchmark_min": Decimal("65"),
                        "benchmark_mid": Decimal("78"),
                        "benchmark_max": Decimal("95"),
                        "corridor_rule_code": "BC-ACC-QATAR-001",
                        "variance_pct": 8.97,
                        "variance_status": "MODERATE",
                        "variance_note": "Copper piping slightly above mid",
                    },
                    {
                        "description": "Flexible Ducting 250mm dia",
                        "category": "DUCTING",
                        "uom": "m",
                        "quantity": Decimal("280"),
                        "quoted_unit_rate": Decimal("38"),
                        "line_amount": Decimal("10640"),
                        "line_number": 2,
                        "benchmark_min": Decimal("28"),
                        "benchmark_mid": Decimal("35"),
                        "benchmark_max": Decimal("45"),
                        "corridor_rule_code": "BC-DUCT-QAT-002",
                        "variance_pct": 8.57,
                        "variance_status": "MODERATE",
                        "variance_note": "Flex duct above mid but within range",
                    },
                    {
                        "description": "Pipe Hangers and Support Steelwork",
                        "category": "ACCESSORIES",
                        "uom": "Lot",
                        "quantity": Decimal("1"),
                        "quoted_unit_rate": Decimal("18000"),
                        "line_amount": Decimal("18000"),
                        "line_number": 3,
                        "benchmark_min": Decimal("12000"),
                        "benchmark_mid": Decimal("16000"),
                        "benchmark_max": Decimal("22000"),
                        "corridor_rule_code": "BC-ACC-QATAR-002",
                        "variance_pct": 12.5,
                        "variance_status": "MODERATE",
                        "variance_note": "Steelwork support 12.5% above mid",
                    },
                    {
                        "description": "Labour - Complete Installation Works",
                        "category": "INSTALLATION",
                        "uom": "Lot",
                        "quantity": Decimal("1"),
                        "quoted_unit_rate": Decimal("28000"),
                        "line_amount": Decimal("28000"),
                        "line_number": 4,
                        "benchmark_min": Decimal("22000"),
                        "benchmark_mid": Decimal("26000"),
                        "benchmark_max": Decimal("34000"),
                        "corridor_rule_code": "BC-INST-QATAR-001",
                        "variance_pct": 7.69,
                        "variance_status": "MODERATE",
                        "variance_note": "Labour above mid",
                    },
                    {
                        "description": "Testing, Commissioning and Handover",
                        "category": "TC",
                        "uom": "Lot",
                        "quantity": Decimal("1"),
                        "quoted_unit_rate": Decimal("7500"),
                        "line_amount": Decimal("7500"),
                        "line_number": 5,
                        "benchmark_min": Decimal("6000"),
                        "benchmark_mid": Decimal("7000"),
                        "benchmark_max": Decimal("9500"),
                        "corridor_rule_code": "BC-TC-QATAR-001",
                        "variance_pct": 7.14,
                        "variance_status": "MODERATE",
                        "variance_note": "T&C rate acceptable",
                    },
                ],
            },
        ],
        "result": {
            "total_quoted": Decimal("96440"),
            "total_benchmark_mid": Decimal("89000"),
            "overall_deviation_pct": 8.36,
            "overall_status": "MODERATE",
            "lines_within_range": 0,
            "lines_moderate": 5,
            "lines_high": 0,
            "lines_needs_review": 0,
            "category_summary_json": {
                "ACCESSORIES": {"quoted": 50300, "benchmark_mid": 46800, "deviation_pct": 7.48, "status": "MODERATE", "count": 2},
                "DUCTING": {"quoted": 10640, "benchmark_mid": 9800, "deviation_pct": 8.57, "status": "MODERATE", "count": 1},
                "INSTALLATION": {"quoted": 28000, "benchmark_mid": 26000, "deviation_pct": 7.69, "status": "MODERATE", "count": 1},
                "TC": {"quoted": 7500, "benchmark_mid": 7000, "deviation_pct": 7.14, "status": "MODERATE", "count": 1},
            },
            "negotiation_notes_json": [
                "Overall deviation of 8.4% is manageable. Entire scope can be negotiated to within 3-5% of benchmark.",
                "Negotiate copper piping and steelwork as single package - target QAR 45,000 combined.",
                "Labour cost is the largest variable - request competitive counter-bid or lump-sum commitment.",
            ],
        },
    },
    {
        "title": "Abu Dhabi Warehouse Cooling - Equipment Only",
        "project_name": "ADL Logistics Hub W-22",
        "geography": "UAE",
        "scope_type": "EQUIPMENT_ONLY",
        "store_type": "WAREHOUSE",
        "notes": "Supply equipment only. Installation by client's team. Evaporative coolers + industrial fans.",
        "status": "PENDING",
        "quotations": [
            {
                "supplier_name": "Emirates Industrial Cooling LLC",
                "quotation_ref": "EIC/ABU/2026/001",
                "extraction_status": "PENDING",
                "extracted_text": "",
                "line_items": [],
            },
        ],
        "result": None,
    },
]


class Command(BaseCommand):
    help = "Seed realistic benchmarking demo data (requests, quotations, line items, results)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear all existing benchmarking data before seeding",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            self.stdout.write("Clearing existing benchmark data...")
            BenchmarkResult.objects.all().delete()
            BenchmarkLineItem.objects.all().delete()
            BenchmarkQuotation.objects.all().delete()
            BenchmarkRequest.objects.all().delete()
            self.stdout.write(self.style.WARNING("  All benchmarking data cleared."))

        # Get or create a system user for created_by
        admin_user = User.objects.filter(is_superuser=True).first()
        if not admin_user:
            admin_user = User.objects.filter(is_staff=True).first()
        if not admin_user:
            admin_user = User.objects.first()

        if not admin_user:
            self.stderr.write("No user found in DB. Run python manage.py createsuperuser first.")
            return

        # Verify corridor rules exist
        corridor_count = BenchmarkCorridorRule.objects.filter(is_active=True).count()
        if corridor_count == 0:
            self.stdout.write(self.style.WARNING(
                "No corridor rules found. Run 'python manage.py seed_benchmark_corridors' first."
            ))

        requests_created = 0
        quotations_created = 0
        line_items_created = 0
        results_created = 0

        for req_data in REQUESTS:
            # Check if already exists (idempotent by title)
            if BenchmarkRequest.objects.filter(title=req_data["title"]).exists():
                self.stdout.write(f"  Skipping (exists): {req_data['title']}")
                continue

            # Create BenchmarkRequest
            bench_request = BenchmarkRequest.objects.create(
                title=req_data["title"],
                project_name=req_data["project_name"],
                geography=req_data["geography"],
                scope_type=req_data["scope_type"],
                store_type=req_data.get("store_type", ""),
                notes=req_data.get("notes", ""),
                status=req_data["status"],
                submitted_by=admin_user,
                created_by=admin_user,
                is_active=True,
            )
            requests_created += 1
            self.stdout.write(f"  Created request: {bench_request.title}")

            for q_data in req_data.get("quotations", []):
                quotation = BenchmarkQuotation.objects.create(
                    request=bench_request,
                    supplier_name=q_data["supplier_name"],
                    quotation_ref=q_data["quotation_ref"],
                    extraction_status=q_data["extraction_status"],
                    extracted_text=q_data.get("extracted_text", ""),
                    document="",
                    created_by=admin_user,
                    is_active=True,
                )
                quotations_created += 1

                for li_data in q_data.get("line_items", []):
                    BenchmarkLineItem.objects.create(
                        quotation=quotation,
                        description=li_data["description"],
                        category=li_data["category"],
                        uom=li_data.get("uom", ""),
                        quantity=li_data.get("quantity"),
                        quoted_unit_rate=li_data.get("quoted_unit_rate"),
                        line_amount=li_data.get("line_amount"),
                        line_number=li_data.get("line_number", 0),
                        benchmark_min=li_data.get("benchmark_min"),
                        benchmark_mid=li_data.get("benchmark_mid"),
                        benchmark_max=li_data.get("benchmark_max"),
                        corridor_rule_code=li_data.get("corridor_rule_code", ""),
                        variance_pct=li_data.get("variance_pct"),
                        variance_status=li_data.get("variance_status", "NEEDS_REVIEW"),
                        variance_note=li_data.get("variance_note", ""),
                        created_by=admin_user,
                        is_active=True,
                    )
                    line_items_created += 1

            if req_data.get("result"):
                r = req_data["result"]
                BenchmarkResult.objects.create(
                    request=bench_request,
                    total_quoted=r["total_quoted"],
                    total_benchmark_mid=r["total_benchmark_mid"],
                    overall_deviation_pct=r["overall_deviation_pct"],
                    overall_status=r["overall_status"],
                    lines_within_range=r.get("lines_within_range", 0),
                    lines_moderate=r.get("lines_moderate", 0),
                    lines_high=r.get("lines_high", 0),
                    lines_needs_review=r.get("lines_needs_review", 0),
                    category_summary_json=r.get("category_summary_json", {}),
                    negotiation_notes_json=r.get("negotiation_notes_json", []),
                    created_by=admin_user,
                    is_active=True,
                )
                results_created += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nDone: {requests_created} requests, {quotations_created} quotations, "
            f"{line_items_created} line items, {results_created} results created."
        ))
