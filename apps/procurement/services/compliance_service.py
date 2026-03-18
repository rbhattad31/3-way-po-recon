"""ComplianceService — rule-based compliance checks for procurement."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from apps.core.enums import ComplianceStatus
from apps.procurement.models import ProcurementRequest

logger = logging.getLogger(__name__)


class ComplianceService:
    """Stateless compliance checking for procurement recommendations and benchmarks.

    Rules are defined here and can be expanded per domain/schema_code.
    """

    @staticmethod
    def check_recommendation(
        request: ProcurementRequest,
        recommendation: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Check a recommendation against compliance rules.

        Returns:
            dict with keys: status, rules_checked, violations, recommendations
        """
        rules_checked = []
        violations = []
        recommendations = []

        # Rule 1: Recommendation must have a non-empty option
        rules_checked.append({"rule": "recommendation_present", "description": "A recommendation must be provided"})
        if not recommendation.get("recommended_option"):
            violations.append({"rule": "recommendation_present", "detail": "No recommended option provided"})

        # Rule 2: Confidence must be above threshold
        rules_checked.append({"rule": "confidence_threshold", "description": "Confidence must be >= 0.5"})
        confidence = recommendation.get("confidence", 0)
        if confidence < 0.5:
            violations.append({
                "rule": "confidence_threshold",
                "detail": f"Confidence {confidence:.2f} is below minimum threshold 0.50",
            })
            recommendations.append("Consider gathering more requirements or consulting a domain expert.")

        # Rule 3: Budget constraint (if budget attribute exists)
        budget = None
        for attr in request.attributes.filter(attribute_code="budget"):
            if attr.value_number is not None:
                budget = float(attr.value_number)
        if budget is not None:
            rules_checked.append({"rule": "budget_check", "description": "Recommendation should be within budget"})
            estimated_cost = recommendation.get("estimated_cost")
            if estimated_cost and float(estimated_cost) > budget:
                violations.append({
                    "rule": "budget_check",
                    "detail": f"Estimated cost {estimated_cost} exceeds budget {budget}",
                })

        # Determine overall status
        if violations:
            status = ComplianceStatus.FAIL if len(violations) >= 2 else ComplianceStatus.PARTIAL
        else:
            status = ComplianceStatus.PASS

        return {
            "status": status,
            "rules_checked": rules_checked,
            "violations": violations,
            "recommendations": recommendations,
        }

    @staticmethod
    def check_benchmark(
        request: ProcurementRequest,
        benchmark_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Check benchmark results against compliance rules."""
        rules_checked = []
        violations = []

        rules_checked.append({"rule": "variance_limit", "description": "Overall variance should be ≤30%"})
        variance_pct = benchmark_summary.get("variance_pct")
        if variance_pct is not None and abs(float(variance_pct)) > 30:
            violations.append({
                "rule": "variance_limit",
                "detail": f"Variance {variance_pct}% exceeds 30% threshold",
            })

        status = ComplianceStatus.PASS if not violations else ComplianceStatus.FAIL
        return {
            "status": status,
            "rules_checked": rules_checked,
            "violations": violations,
            "recommendations": [],
        }
