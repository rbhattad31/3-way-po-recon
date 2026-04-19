#!/usr/bin/env python
"""
Quick validation script for HVAC Recommendation Agent DB system loading.
Run: python test_agent_db_validation.py
"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.procurement.agents.hvac_recommendation_agent import HVACRecommendationAgent
from apps.procurement.models import HVACServiceScope, HVACRecommendationRule

def main():
    print("\n" + "="*60)
    print("HVAC AGENT DATABASE SYSTEM VALIDATION")
    print("="*60 + "\n")

    # 1. Check available systems in DB
    print("1. CHECKING DATABASE SYSTEMS:")
    print("-" * 40)
    systems = HVACServiceScope.objects.filter(is_active=True)
    print(f"Total Active Systems in DB: {systems.count()}")
    if systems.count() > 0:
        for sys in systems[:3]:
            print(f"  ✓ {sys.system_type}: {sys.display_name}")
        if systems.count() > 3:
            print(f"  ... and {systems.count() - 3} more")
    else:
        print("  ⚠️  WARNING: No active systems found in database!")
        print("     Agent will have no DB systems to recommend from!")

    # 2. Check rules in DB
    print("\n2. CHECKING DATABASE RULES:")
    print("-" * 40)
    rules = HVACRecommendationRule.objects.filter(is_active=True)
    print(f"Total Active Rules in DB: {rules.count()}")
    if rules.count() > 0:
        for rule in rules[:2]:
            print(f"  ✓ {rule.rule_code} → {rule.recommended_system}")
        if rules.count() > 2:
            print(f"  ... and {rules.count() - 2} more")
    else:
        print("  ⚠️  WARNING: No active rules found in database!")

    # 3. Test agent's _load_db_context method
    print("\n3. TESTING AGENT CONTEXT LOADING:")
    print("-" * 40)
    test_attrs = {
        "store_type": "RETAIL",
        "area_sqft": 3000,
        "budget_level": "MEDIUM",
        "ambient_temp_max": 45,
        "country": "UAE",
    }

    try:
        db_ctx = HVACRecommendationAgent._load_db_context(test_attrs)
        print("✓ Context loaded successfully")

        available_systems = db_ctx.get('available_systems', [])
        print(f"  - Available Systems from DB: {len(available_systems)}")
        for i, sys in enumerate(available_systems[:2], 1):
            print(f"    {i}. {sys.get('system_type')} - {sys.get('name')}")

        db_rules = db_ctx.get('db_rules_reference', [])
        print(f"  - DB Rules Reference: {len(db_rules)}")

        rules_failed = db_ctx.get('rules_failed', [])
        print(f"  - Rules That Failed: {len(rules_failed)}")
        if rules_failed:
            print(f"    Sample: {rules_failed[0].get('rule_code')} - {len(rules_failed[0].get('failure_reasons', []))} failures")

        rules_near_miss = db_ctx.get('rules_near_miss', [])
        print(f"  - Rules Near-Miss (1-2 failures): {len(rules_near_miss)}")
        if rules_near_miss:
            print(f"    Best match: {rules_near_miss[0].get('rule_code')} - {rules_near_miss[0].get('conditions_failed')} failures")

    except Exception as e:
        print(f"✗ Error loading context: {e}")
        return 1

    # 4. Test agent recommendation
    print("\n4. TESTING AGENT RECOMMENDATION:")
    print("-" * 40)
    try:
        result = HVACRecommendationAgent.recommend(
            attrs=test_attrs,
            no_match_context={"rules_evaluated": len(rules), "rules_loaded": len(rules)},
            procurement_request_pk=None,
        )
        print("✓ Recommendation generated")

        recommended_system = result.get('recommended_system_type')
        print(f"  - Recommended System: {recommended_system}")

        # Verify the system is from the DB
        system_in_db = HVACServiceScope.objects.filter(
            system_type=recommended_system,
            is_active=True
        ).exists()

        if system_in_db:
            print(f"    ✓ System '{recommended_system}' verified in database")
        else:
            print(f"    ✗ ERROR: System '{recommended_system}' NOT in database!")
            return 1

        confidence = result.get('confidence', 0)
        print(f"  - Confidence Score: {confidence:.2f} (0.0-1.0)")

        human_review = result.get('human_validation_required', False)
        print(f"  - Human Review Required: {human_review}")

        reasoning = result.get('reasoning_details', {})
        if reasoning:
            print(f"  - Rules Evaluated: {reasoning.get('rules_evaluated', 0)}")
            print(f"  - Rules Failed: {reasoning.get('rules_failed_count', 0)}")
            print(f"  - Rules Near-Miss: {reasoning.get('rules_near_miss_count', 0)}")

    except Exception as e:
        print(f"✗ Error in recommendation: {e}")
        import traceback
        traceback.print_exc()
        return 1

    print("\n" + "="*60)
    print("✓ VALIDATION COMPLETE - ALL CHECKS PASSED")
    print("="*60 + "\n")
    return 0

if __name__ == '__main__':
    sys.exit(main())
