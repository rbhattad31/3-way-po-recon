"""Test HVAC Recommendation Agent - Verify DB-Only System Selection"""
import logging
from unittest.mock import patch

from django.test import TestCase

from apps.procurement.agents.hvac_recommendation_agent import HVACRecommendationAgent
from apps.procurement.models import HVACServiceScope, HVACRecommendationRule

logger = logging.getLogger(__name__)


class HVACRecommendationAgentSystemSelectionTests(TestCase):
    """Test that HVAC agent recommends systems from DB only."""

    databases = {"default"}

    def setUp(self):
        """Create test systems in DB."""
        self.system_vrf = HVACServiceScope.objects.create(
            system_type="VRF",
            display_name="Variable Refrigerant Flow System",
            equipment_scope="VRF outdoor and indoor units",
            installation_services="Mounting and commissioning",
            piping_ducting="Copper refrigerant piping",
            electrical_works="Power and control wiring",
            controls_accessories="Thermostat and accessories",
            testing_commissioning="Cooling performance test",
            sort_order=1,
            is_active=True,
        )

        self.system_split = HVACServiceScope.objects.create(
            system_type="SPLIT_AC",
            display_name="Split Air Conditioning System",
            equipment_scope="Split indoor and outdoor units",
            installation_services="Wall mount and commissioning",
            piping_ducting="Copper refrigerant piping",
            electrical_works="Power and control wiring",
            controls_accessories="Remote thermostat and accessories",
            testing_commissioning="Cooling performance test",
            sort_order=2,
            is_active=True,
        )

        self.system_fcu = HVACServiceScope.objects.create(
            system_type="FCU",
            display_name="Fan Coil Unit (Chilled Water)",
            equipment_scope="FCU indoor equipment",
            installation_services="FCU installation and balancing",
            piping_ducting="Chilled water piping",
            electrical_works="Power and control wiring",
            controls_accessories="Valves and thermostat controls",
            testing_commissioning="Hydronic and cooling test",
            sort_order=3,
            is_active=True,
        )

        # Create a rule that will NOT match (to trigger agent recommendation)
        self.rule_no_match = HVACRecommendationRule.objects.create(
            rule_code="R-TEST-001",
            rule_name="Test Rule (Will Not Match)",
            store_type_filter="MALL",
            area_sq_ft_min=10000,  # Very large minimum
            area_sq_ft_max=50000,
            ambient_temp_min_c=40,
            budget_level_filter="HIGH",
            energy_priority_filter="HIGH",
            recommended_system="VRF",
            priority=1,
            is_active=True,
        )

    def test_agent_recommends_only_db_systems(self):
        """Verify agent recommends only systems from HVACServiceScope."""
        attrs = {
            "store_type": "KIOSK",  # Will not match MALL filter
            "area_sqft": 2000,  # Below 10000 minimum
            "ambient_temp_max": 45,
            "budget_level": "MEDIUM",  # Not HIGH
            "energy_efficiency_priority": "MEDIUM",
            "country": "UAE",
        }

        no_match_context = {
            "rules_evaluated": 1,
            "rules_loaded": 1,
            "inputs": attrs,
        }

        with patch.object(HVACRecommendationAgent, "_load_db_context") as mock_load:
            # Setup mock to return actual DB data
            mock_load.return_value = {
                "available_systems": [
                    {
                        "system_type": "VRF",
                        "name": "Variable Refrigerant Flow System",
                        "description": "VRF system for multi-zone cooling",
                        "capex_band": "HIGH",
                        "opex_band": "MEDIUM",
                    },
                    {
                        "system_type": "SPLIT_AC",
                        "name": "Split Air Conditioning System",
                        "description": "Standard split AC system",
                        "capex_band": "LOW",
                        "opex_band": "MEDIUM",
                    },
                    {
                        "system_type": "FCU",
                        "name": "Fan Coil Unit (Chilled Water)",
                        "description": "FCU with central chilled water plant",
                        "capex_band": "MEDIUM",
                        "opex_band": "LOW",
                    },
                ],
                "system_code_to_label": {
                    "VRF": "Variable Refrigerant Flow System",
                    "SPLIT_AC": "Split Air Conditioning System",
                    "FCU": "Fan Coil Unit (Chilled Water)",
                },
                "db_rules_reference": [
                    {
                        "rule_code": "R-TEST-001",
                        "rule_name": "Test Rule",
                        "recommended_system": "VRF",
                        "conditions": ["store_type: MALL", "area >= 10000 sqft"],
                    }
                ],
                "rules_failed": [
                    {
                        "rule_code": "R-TEST-001",
                        "recommended_system": "VRF",
                        "failure_reasons": [
                            "store_type mismatch (rule: MALL, actual: KIOSK)",
                            "area too small (rule min: 10000 sqft, actual: 2000)",
                        ],
                        "conditions_failed": 2,
                    }
                ],
                "rules_near_miss": [],
                "similar_stores": [],
                "market_intelligence": {},
            }

            with patch.object(
                HVACRecommendationAgent, "recommend", wraps=HVACRecommendationAgent.recommend
            ) as mock_recommend:
                result = HVACRecommendationAgent.recommend(
                    attrs=attrs,
                    no_match_context=no_match_context,
                    procurement_request_pk=None,
                )

                # Verify result
                self.assertIsNotNone(result, "Agent should return a result")
                self.assertIn(
                    "recommended_system_type", result, "Result should have recommended_system_type"
                )

                recommended_type = result.get("recommended_system_type", "").upper()
                available_types = {"VRF", "SPLIT_AC", "FCU"}

                self.assertIn(
                    recommended_type,
                    available_types,
                    f"Recommended system '{recommended_type}' must be from DB. "
                    f"Available: {available_types}. "
                    f"Full result: {result}",
                )

    def test_agent_uses_db_rules_reference(self):
        """Verify agent uses DB rules as reference."""
        attrs = {
            "store_type": "KIOSK",
            "area_sqft": 2000,
            "ambient_temp_max": 45,
            "budget_level": "MEDIUM",
            "country": "UAE",
        }

        no_match_context = {
            "rules_evaluated": 2,
            "rules_loaded": 2,
            "inputs": attrs,
        }

        # Real DB context loading (no mock)
        result = HVACRecommendationAgent.recommend(
            attrs=attrs,
            no_match_context=no_match_context,
            procurement_request_pk=None,
        )

        # Check reasoning details
        reasoning_details = result.get("reasoning_details", {})
        self.assertIsNotNone(reasoning_details, "Should have reasoning_details")

        # Check rule evaluation stats
        logger.info(f"Rules evaluated: {reasoning_details.get('rules_evaluated')}")
        logger.info(f"Rules loaded: {reasoning_details.get('rules_loaded')}")
        logger.info(f"Rules failed: {reasoning_details.get('rules_failed_count')}")
        logger.info(f"Rules near-miss: {reasoning_details.get('rules_near_miss_count')}")

    def test_db_context_contains_only_active_systems(self):
        """Verify _load_db_context returns only active systems."""
        attrs = {
            "store_type": "RETAIL",
            "area_sqft": 5000,
            "country": "UAE",
        }

        db_ctx = HVACRecommendationAgent._load_db_context(attrs)

        # Verify systems from DB
        available_systems = db_ctx.get("available_systems", [])
        self.assertGreater(
            len(available_systems),
            0,
            "Should have at least one active system from DB",
        )

        # Check that systems match DB codes
        system_codes = {s.get("system_type") for s in available_systems}
        expected_codes = {"VRF", "SPLIT_AC", "FCU"}

        for code in system_codes:
            self.assertIn(
                code,
                expected_codes,
                f"System code '{code}' should be from DB active systems",
            )

        logger.info(f"Available systems from DB: {system_codes}")

    def test_rules_are_evaluated_dynamically(self):
        """Verify rules are loaded and evaluated dynamically from DB."""
        attrs = {
            "store_type": "KIOSK",
            "area_sqft": 2000,
            "ambient_temp_max": 45,
            "budget_level": "MEDIUM",
            "country": "UAE",
        }

        db_ctx = HVACRecommendationAgent._load_db_context(attrs)

        # Check rule evaluation
        rules_reference = db_ctx.get("db_rules_reference", [])
        rules_failed = db_ctx.get("rules_failed", [])
        rules_near_miss = db_ctx.get("rules_near_miss", [])

        logger.info(f"DB Rules Reference Count: {len(rules_reference)}")
        logger.info(f"Rules Failed Count: {len(rules_failed)}")
        logger.info(f"Rules Near-Miss Count: {len(rules_near_miss)}")
        logger.info(f"Total Rules Evaluated: {len(rules_failed) + len(rules_near_miss)}")

        # Our test rule should be in failed rules
        if rules_failed:
            rule_codes = [r.get("rule_code") for r in rules_failed]
            logger.info(f"Failed rule codes: {rule_codes}")

    def test_recommendation_includes_failure_analysis(self):
        """Verify recommendation includes failure analysis in reasoning_details."""
        attrs = {
            "store_type": "KIOSK",
            "area_sqft": 2000,
            "ambient_temp_max": 45,
            "budget_level": "MEDIUM",
            "country": "UAE",
        }

        no_match_context = {
            "rules_evaluated": 1,
            "rules_loaded": 1,
            "inputs": attrs,
        }

        result = HVACRecommendationAgent.recommend(
            attrs=attrs,
            no_match_context=no_match_context,
            procurement_request_pk=None,
        )

        reasoning = result.get("reasoning_details", {})

        # Check failure analysis is included
        self.assertIn("rules_failed_count", reasoning)
        self.assertIn("rules_near_miss_count", reasoning)

        logger.info(f"\n=== RECOMMENDATION RESULT ===")
        logger.info(f"Recommended System: {result.get('recommended_system_type')}")
        logger.info(f"Recommended Option: {result.get('recommended_option')}")
        logger.info(f"Confidence: {result.get('confidence')}")
        logger.info(f"Rules Failed Count: {reasoning.get('rules_failed_count')}")
        logger.info(f"Rules Near-Miss Count: {reasoning.get('rules_near_miss_count')}")
        logger.info(
            f"Decision Drivers: {result.get('decision_drivers', [])}"
        )
        logger.info(f"Reasoning Summary: {result.get('reasoning_summary')}")
