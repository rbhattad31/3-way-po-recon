from django.test import TestCase

from apps.benchmarking.agents.Vendor_Recommendation_Agent_BM import (
    BenchmarkVendorRecommendationAgent,
)


class VendorRecommendationAgentTests(TestCase):
    def test_high_ratio_is_evaluated_against_benchmarked_lines(self):
        vendor_cards = [
            {
                "quotation_id": 101,
                "supplier_name": "Vendor A",
                "deviation_pct": 10.0,
                "line_count": 8,
                "benchmarked_line_count": 3,
                "status_counts": {
                    "WITHIN_RANGE": 2,
                    "MODERATE": 0,
                    "HIGH": 1,
                    "NEEDS_REVIEW": 5,
                },
                "live_reference_count": 0,
            }
        ]

        result = BenchmarkVendorRecommendationAgent.recommend(vendor_cards)

        self.assertFalse(result["recommended"])
        self.assertTrue(result["summary"])
        self.assertGreater(len(result["summary"].strip()), 10)

    def test_rejects_when_no_benchmarked_lines(self):
        vendor_cards = [
            {
                "quotation_id": 102,
                "supplier_name": "Vendor B",
                "deviation_pct": None,
                "line_count": 5,
                "benchmarked_line_count": 0,
                "status_counts": {
                    "WITHIN_RANGE": 0,
                    "MODERATE": 0,
                    "HIGH": 0,
                    "NEEDS_REVIEW": 5,
                },
                "live_reference_count": 0,
            }
        ]

        result = BenchmarkVendorRecommendationAgent.recommend(vendor_cards)

        self.assertFalse(result["recommended"])
        self.assertTrue(result["summary"])
        self.assertGreater(len(result["summary"].strip()), 10)
