"""Quick test: fire the DB-driven HVAC rules engine and show what each test case matches."""
import os
import sys
import django

# Ensure project root is on the path when run from scripts/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.procurement.hvac.rules_engine import HVACRulesEngine  # noqa: E402

# --------------------------------------------------------------------------
# Test cases -- one per rule you want to verify
# --------------------------------------------------------------------------
TEST_CASES = [
    {
        "label": "R1 -- Mall any config",
        "attrs": {
            "country": "UAE",
            "city": "Dubai",
            "store_type": "MALL",
            "area_sqft": 3500,
            "ambient_temp_max": 47,
            "budget_level": "MEDIUM",
            "energy_efficiency_priority": "MEDIUM",
        },
        "expect_rule": "R1",
    },
    {
        "label": "R2 -- Small footprint (1500 sqft)",
        "attrs": {
            "country": "UAE",
            "city": "Abu Dhabi",
            "store_type": "STANDALONE",
            "area_sqft": 1500,
            "ambient_temp_max": 45,
            "budget_level": "LOW",
            "energy_efficiency_priority": "LOW",
        },
        "expect_rule": "R2",
    },
    {
        "label": "R3 -- Large standalone, extreme heat, HIGH energy priority",
        "attrs": {
            "country": "UAE",
            "city": "Dubai",
            "store_type": "STANDALONE",
            "area_sqft": 6000,
            "ambient_temp_max": 46,
            "budget_level": "HIGH",
            "energy_efficiency_priority": "HIGH",
        },
        "expect_rule": "R3",
    },
    {
        "label": "R20 -- Default fallback (no specific match)",
        "attrs": {
            "country": "KSA",
            "city": "Riyadh",
            "store_type": "STANDALONE",
            "area_sqft": 8000,
            "ambient_temp_max": 42,
            "budget_level": "MEDIUM",
            "energy_efficiency_priority": "MEDIUM",
        },
        "expect_rule": "R20",
    },
]


def run():
    print()
    print("=" * 65)
    print("  HVAC DB Rules Engine -- Test Results")
    print("=" * 65)

    all_pass = True
    for tc in TEST_CASES:
        result = HVACRulesEngine.evaluate("HVAC", tc["attrs"], geography_country=tc["attrs"].get("country", ""))
        details = result.get("reasoning_details", {})
        matched = details.get("rule_matched", "NO_MATCH")
        name = details.get("rule_name", "-")
        prio = details.get("rule_priority", "-")
        system = result.get("system_type_code", "-")
        alt = result.get("alternate_option", "-") or "-"
        rationale = (result.get("reasoning_summary") or "")[:80]
        confident = result.get("confident", False)
        rules_checked = details.get("rules_evaluated", "-")
        expected = tc.get("expect_rule", "")
        passed = (matched == expected)
        if not passed:
            all_pass = False
        status = "PASS" if passed else "FAIL (expected " + expected + ")"

        print()
        print(f"  [{status}] {tc['label']}")
        print(f"    Rule matched  : {matched}  (priority={prio}, rules checked={rules_checked})")
        print(f"    Rule name     : {name}")
        print(f"    Recommended   : {system}")
        print(f"    Alternate     : {alt}")
        print(f"    Confident     : {confident}  (confidence={result.get('confidence', 0):.0%})")
        print(f"    Rationale     : {rationale}")
        if result.get("constraints"):
            for c in result["constraints"][:3]:
                print(f"    [{c['type']}] {c['detail'][:70]}")

    print()
    print("=" * 65)
    if all_pass:
        print("  ALL TESTS PASSED")
    else:
        print("  SOME TESTS FAILED -- check rule conditions above")
    print("=" * 65)
    print()


if __name__ == "__main__":
    run()
