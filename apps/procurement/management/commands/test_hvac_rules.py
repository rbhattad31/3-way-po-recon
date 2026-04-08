"""
Management command: test_hvac_rules
====================================
Runs 20 form-filling test scenarios through the HVAC recommendation engine
and prints a pass / fail table showing which DB rule fired and whether the
recommended system matches the expected outcome.

Usage
-----
    python manage.py test_hvac_rules
    python manage.py test_hvac_rules --verbose   # show full rule detail per case
    python manage.py test_hvac_rules --stop-on-fail

Exit code 0 = all 20 pass  |  exit code 1 = at least one failure
"""
from __future__ import annotations

import sys
from typing import Any, Dict, List, NamedTuple

from django.core.management.base import BaseCommand


# ---------------------------------------------------------------------------
# Test-case definitions
# ---------------------------------------------------------------------------
# Each scenario is designed to exercise ONE specific rule (or at least to
# produce a deterministic expected outcome):
#
#   attrs keys consumed by matches():
#       store_type              -> MALL / STANDALONE / HOSPITAL / WAREHOUSE /
#                                  OFFICE / HYPERMARKET / DATA_CENTER  (or "" = any)
#       area_sq_ft              -> float
#       ambient_temp_max_c      -> float  (used as "ambient" in matches)
#       budget_level            -> LOW / MEDIUM / HIGH  (or "" = unset)
#       energy_efficiency_priority -> LOW / MEDIUM / HIGH  (or "" = unset)
#
#   Extra attrs (used by the heuristic fallback; harmless for DB path):
#       store_id, brand, country, city, store_format, ceiling_height_ft,
#       humidity_level, dust_exposure, heat_load_category, landlord_constraints

class Case(NamedTuple):
    id: int                # 1-20
    description: str       # human label
    expected_rule: str     # e.g. "R1"
    expected_system: str   # e.g. "CHILLER"
    attrs: Dict[str, Any]


CASES: List[Case] = [
    # ── R1: Mall -- any configuration ─────────────────────────────────────────
    Case(
        id=1,
        description="Mall, mid-size, moderate budget",
        expected_rule="R1",
        expected_system="CHILLER",
        attrs={
            "store_type": "MALL",
            "area_sq_ft": 3500,
            "ambient_temp_max_c": 36,
            "budget_level": "MEDIUM",
            "energy_efficiency_priority": "MEDIUM",
        },
    ),
    # ── R2: Small footprint -- under 2000 sq ft ───────────────────────────────
    Case(
        id=2,
        description="Standalone kiosk, 1 200 sq ft, high budget",
        expected_rule="R2",
        expected_system="SPLIT_AC",
        attrs={
            "store_type": "STANDALONE",
            "area_sq_ft": 1200,
            "ambient_temp_max_c": 30,
            "budget_level": "HIGH",
            "energy_efficiency_priority": "HIGH",
        },
    ),
    # ── R3: Standalone large, extreme heat, HIGH energy priority ──────────────
    Case(
        id=3,
        description="Standalone flagship, 7000 sq ft, desert heat 48 C, energy-first",
        expected_rule="R3",
        expected_system="VRF",
        attrs={
            "store_type": "STANDALONE",
            "area_sq_ft": 7000,
            "ambient_temp_max_c": 48,
            "budget_level": "HIGH",
            "energy_efficiency_priority": "HIGH",
        },
    ),
    # ── R4: Standalone large, extreme heat, LOW budget ────────────────────────
    Case(
        id=4,
        description="Standalone superstore, 8500 sq ft, 46 C ambient, tight budget",
        expected_rule="R4",
        expected_system="PACKAGED_DX",
        attrs={
            "store_type": "STANDALONE",
            "area_sq_ft": 8500,
            "ambient_temp_max_c": 46,
            "budget_level": "LOW",
            "energy_efficiency_priority": "LOW",
        },
    ),
    # ── R5: Mid-size [2 000, 5 000) sq ft, LOW budget ─────────────────────────
    Case(
        id=5,
        description="Generic retail, 2800 sq ft, cost-sensitive",
        expected_rule="R5",
        expected_system="PACKAGED_DX",
        attrs={
            "store_type": "",
            "area_sq_ft": 2800,
            "ambient_temp_max_c": 34,
            "budget_level": "LOW",
            "energy_efficiency_priority": "MEDIUM",
        },
    ),
    # ── R6: Mid-size [2 000, 5 000) sq ft, MEDIUM/HIGH budget, HIGH energy ────
    Case(
        id=6,
        description="Boutique retail, 3200 sq ft, premium budget, high efficiency goal",
        expected_rule="R6",
        expected_system="VRF",
        attrs={
            "store_type": "",
            "area_sq_ft": 3200,
            "ambient_temp_max_c": 38,
            "budget_level": "HIGH",
            "energy_efficiency_priority": "HIGH",
        },
    ),
    # ── R7: Mid-size [2 000, 5 000) sq ft, MEDIUM/HIGH budget, LOW/MEDIUM energy
    Case(
        id=7,
        description="Pharmacy, 4000 sq ft, medium budget, moderate efficiency",
        expected_rule="R7",
        expected_system="PACKAGED_DX",
        attrs={
            "store_type": "",
            "area_sq_ft": 4000,
            "ambient_temp_max_c": 37,
            "budget_level": "MEDIUM",
            "energy_efficiency_priority": "MEDIUM",
        },
    ),
    # ── R8: Hospital -- any size ──────────────────────────────────────────────
    Case(
        id=8,
        description="Hospital outpatient wing, 12 000 sq ft, precision control required",
        expected_rule="R8",
        expected_system="VRF",
        attrs={
            "store_type": "HOSPITAL",
            "area_sq_ft": 12000,
            "ambient_temp_max_c": 40,
            "budget_level": "HIGH",
            "energy_efficiency_priority": "HIGH",
        },
    ),
    # ── R9: Warehouse large (>= 10 000 sq ft) ────────────────────────────────
    Case(
        id=9,
        description="Distribution warehouse, 18 000 sq ft, minimal zoning needs",
        expected_rule="R9",
        expected_system="PACKAGED_DX",
        attrs={
            "store_type": "WAREHOUSE",
            "area_sq_ft": 18000,
            "ambient_temp_max_c": 42,
            "budget_level": "LOW",
            "energy_efficiency_priority": "LOW",
        },
    ),
    # ── R10: Warehouse small/mid (< 10 000 sq ft, >= 2 000) ──────────────────
    Case(
        id=10,
        description="Small cold-room warehouse, 6 000 sq ft",
        expected_rule="R10",
        expected_system="SPLIT_AC",
        attrs={
            "store_type": "WAREHOUSE",
            "area_sq_ft": 6000,
            "ambient_temp_max_c": 38,
            "budget_level": "MEDIUM",
            "energy_efficiency_priority": "MEDIUM",
        },
    ),
    # ── R11: Office small (< 3 000 sq ft), budget unset ──────────────────────
    # Budget / energy must be left empty so R5/R6/R7 do not fire first.
    Case(
        id=11,
        description="Branch office, 2400 sq ft, budget not yet decided",
        expected_rule="R11",
        expected_system="SPLIT_AC",
        attrs={
            "store_type": "OFFICE",
            "area_sq_ft": 2400,
            "ambient_temp_max_c": 35,
            "budget_level": "",
            "energy_efficiency_priority": "",
        },
    ),
    # ── R12: Office medium (3 000-15 000), HIGH budget + HIGH energy, area > 5k
    # Area must be >= 5000 so R6 (which has no store-type filter, area < 5000)
    # does NOT fire first.
    Case(
        id=12,
        description="Corporate HQ, 8 000 sq ft, green-star target, generous capex",
        expected_rule="R12",
        expected_system="VRF",
        attrs={
            "store_type": "OFFICE",
            "area_sq_ft": 8000,
            "ambient_temp_max_c": 36,
            "budget_level": "HIGH",
            "energy_efficiency_priority": "HIGH",
        },
    ),
    # ── R13: Office medium (3 000-15 000), LOW/MEDIUM budget, area > 5k ──────
    Case(
        id=13,
        description="Regional office, 7 000 sq ft, standard fit-out budget",
        expected_rule="R13",
        expected_system="PACKAGED_DX",
        attrs={
            "store_type": "OFFICE",
            "area_sq_ft": 7000,
            "ambient_temp_max_c": 35,
            "budget_level": "MEDIUM",
            "energy_efficiency_priority": "MEDIUM",
        },
    ),
    # ── R14: Data Centre -- any size ─────────────────────────────────────────
    # area must be >= 5000 so the generic mid-size rules R5/R6/R7 (area < 5000)
    # do NOT fire first, and area must be >= 2000 so R2 (area < 2000) is skipped.
    Case(
        id=14,
        description="Primary data centre hall, 5 000 sq ft, precision cooling mandatory",
        expected_rule="R14",
        expected_system="CHILLER",
        attrs={
            "store_type": "DATA_CENTER",
            "area_sq_ft": 5000,
            "ambient_temp_max_c": 38,
            "budget_level": "HIGH",
            "energy_efficiency_priority": "HIGH",
        },
    ),
    # ── R15: Hypermarket large (>= 20 000 sq ft) ─────────────────────────────
    Case(
        id=15,
        description="Big-box hypermarket, 28 000 sq ft, central plant preferred",
        expected_rule="R15",
        expected_system="CHILLER",
        attrs={
            "store_type": "HYPERMARKET",
            "area_sq_ft": 28000,
            "ambient_temp_max_c": 41,
            "budget_level": "HIGH",
            "energy_efficiency_priority": "MEDIUM",
        },
    ),
    # ── R16: Hypermarket small/mid (< 20 000 sq ft) ──────────────────────────
    Case(
        id=16,
        description="Compact hypermarket, 12 000 sq ft",
        expected_rule="R16",
        expected_system="PACKAGED_DX",
        attrs={
            "store_type": "HYPERMARKET",
            "area_sq_ft": 12000,
            "ambient_temp_max_c": 38,
            "budget_level": "MEDIUM",
            "energy_efficiency_priority": "MEDIUM",
        },
    ),
    # ── R17: Standalone, large (>= 5000), moderate climate, HIGH energy ───────
    # ambient < 45 so R3/R4 do NOT fire first.
    Case(
        id=17,
        description="Standalone showroom, 6 500 sq ft, mild climate, energy-first",
        expected_rule="R17",
        expected_system="VRF",
        attrs={
            "store_type": "STANDALONE",
            "area_sq_ft": 6500,
            "ambient_temp_max_c": 38,
            "budget_level": "HIGH",
            "energy_efficiency_priority": "HIGH",
        },
    ),
    # ── R18: Standalone mid-size [2k-5k), MEDIUM budget, energy unset ─────────
    # energy_efficiency_priority must be "" so R7 (which has energy LOW_MEDIUM)
    # does NOT fire first.
    Case(
        id=18,
        description="Standalone convenience store, 3 000 sq ft, standard budget, energy TBD",
        expected_rule="R18",
        expected_system="PACKAGED_DX",
        attrs={
            "store_type": "STANDALONE",
            "area_sq_ft": 3000,
            "ambient_temp_max_c": 34,
            "budget_level": "MEDIUM",
            "energy_efficiency_priority": "",
        },
    ),
    # ── R19: Any large site, HIGH budget + LOW energy priority ────────────────
    # area must be >= 5000 so R7 (area < 5000) and R6 do NOT fire first.
    Case(
        id=19,
        description="Campus building connected to district chilled-water, 6 000 sq ft",
        expected_rule="R19",
        expected_system="FCU",
        attrs={
            "store_type": "",
            "area_sq_ft": 6000,
            "ambient_temp_max_c": 34,
            "budget_level": "HIGH",
            "energy_efficiency_priority": "LOW",
        },
    ),
    # ── R20: Default fallback -- nothing else matches ─────────────────────────
    # area=5000 sits on the exclusive upper-bound of R5/R6/R7 (area_sq_ft_max=5000,
    # check is area >= max -> False means the rule is SKIPPED), so those rules do
    # not fire.  All store-type rules need a specific type.  R19 needs budget=HIGH.
    # With everything empty only R20 (priority 999, no filters) matches.
    Case(
        id=20,
        description="Unknown site, area 5 000, no other parameters set",
        expected_rule="R20",
        expected_system="PACKAGED_DX",
        attrs={
            "store_type": "",
            "area_sq_ft": 5000,
            "ambient_temp_max_c": 0,
            "budget_level": "",
            "energy_efficiency_priority": "",
        },
    ),
]


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------
class Command(BaseCommand):
    help = "Run 20 form-filling test scenarios against the HVAC recommendation engine."

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
            default=False,
            help="Print full recommendation detail for each case.",
        )
        parser.add_argument(
            "--stop-on-fail",
            action="store_true",
            default=False,
            help="Abort after the first failing test case.",
        )

    def handle(self, *args, **options):
        from apps.procurement.services.domain.hvac.hvac_recommendation_rules import (
            HVACRecommendationRules,
        )
        from apps.procurement.models import HVACRecommendationRule

        verbose = options["verbose"]
        stop_on_fail = options["stop_on_fail"]

        # -- pre-flight sanity check ------------------------------------------
        total_db_rules = HVACRecommendationRule.objects.filter(is_active=True).count()
        self.stdout.write(
            f"\n  DB rules active: {total_db_rules}  |  Test cases: {len(CASES)}\n"
        )
        if total_db_rules == 0:
            self.stderr.write(
                self.style.ERROR(
                    "No active HVACRecommendationRule records found in database.\n"
                    "Run:  python manage.py seed_hvac_rules\n"
                )
            )
            sys.exit(1)

        PASS = self.style.SUCCESS("PASS")
        FAIL = self.style.ERROR("FAIL")
        WARN = self.style.WARNING

        header = (
            f"  {'#':>3}  {'Description':<55}  {'Expected':^12}  {'Got':^12}  "
            f"{'Rule fired':^10}  {'Result'}"
        )
        sep = "  " + "-" * (len(header) - 2)
        self.stdout.write(header)
        self.stdout.write(sep)

        passed = 0
        failed = 0
        failures = []

        for case in CASES:
            result = HVACRecommendationRules.evaluate(case.attrs)
            got_system = result.get("recommended_system_type", "")
            rule_detail = result.get("reasoning_details", {})
            rule_fired = rule_detail.get("rule_code", "heuristic")

            ok = (got_system == case.expected_system)

            rule_fired_display = rule_fired if rule_fired != "heuristic" else WARN("heuristic")
            status = PASS if ok else FAIL
            expected_str = case.expected_system
            got_str = got_system if got_system else "(empty)"

            self.stdout.write(
                f"  {case.id:>3}  {case.description:<55}  "
                f"{expected_str:^12}  {got_str:^12}  "
                f"{str(rule_fired):^10}  {status}"
            )

            if ok:
                passed += 1
            else:
                failed += 1
                failures.append(case)
                if verbose:
                    self.stdout.write(
                        WARN(f"       Expected rule: {case.expected_rule}   Got: {rule_fired}")
                    )

            if verbose:
                self.stdout.write(
                    f"         attrs     : {_fmt_attrs(case.attrs)}\n"
                    f"         summary   : {result.get('reasoning_summary', '')[:100]}\n"
                )

            if stop_on_fail and not ok:
                break

        self.stdout.write(sep)
        summary = f"\n  Results: {passed} passed, {failed} failed out of {len(CASES)} tests."
        if failed == 0:
            self.stdout.write(self.style.SUCCESS(summary))
        else:
            self.stdout.write(self.style.ERROR(summary))
            self.stdout.write(
                self.style.WARNING(
                    "  Tip: run with --verbose to see full recommendation details.\n"
                    "  Tip: check rule priority order with:  python manage.py "
                    "shell -c \"from apps.procurement.models import HVACRecommendationRule; "
                    "[print(r) for r in HVACRecommendationRule.objects.filter(is_active=True)]\"\n"
                )
            )

        if failed > 0:
            sys.exit(1)


def _fmt_attrs(attrs: dict) -> str:
    parts = []
    for k, v in attrs.items():
        if v not in (None, "", 0):
            parts.append(f"{k}={v}")
    return ", ".join(parts) if parts else "(all defaults)"
