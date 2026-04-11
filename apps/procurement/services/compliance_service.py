"""ComplianceService -- rule-based compliance checks for procurement.

Orchestration order inside check_recommendation():
  1. Core rules  (recommendation_present, confidence_threshold, budget_check)
  2. Quotation rules (three_bid_rule, single_source_risk, price_collusion_check)
  3. Geography-specific regulatory reference checks
  4. Domain-specific rules -- HVAC branch delegates to HVACComplianceService

check_benchmark() checks variance limits and minimum quotation count.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from apps.core.enums import ComplianceStatus
from apps.procurement.models import ProcurementRequest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Geography-specific regulatory requirements
# Keys are ISO 3166-1 alpha-2 country codes (upper-cased).
# ---------------------------------------------------------------------------
_GEO_REQUIREMENTS: Dict[str, Dict[str, Any]] = {
    "AE": {
        "label": "UAE",
        "rule_code": "geo_regulatory_uae",
        "description": "UAE: DEWA/DCD/Trakhees authority submission reference required",
        "keywords": ["dewa", "dcd", "trakhees", "civil defence"],
    },
    "SA": {
        "label": "Saudi Arabia",
        "rule_code": "geo_regulatory_ksa",
        "description": "KSA: SASO/MOMRA energy code compliance reference required",
        "keywords": ["saso", "momra", "sec"],
    },
    "IN": {
        "label": "India",
        "rule_code": "geo_regulatory_india",
        "description": "India: BEE star-rating label required for split/VRF systems",
        "keywords": ["bee", "bureau of energy", "star rating"],
    },
    "QA": {
        "label": "Qatar",
        "rule_code": "geo_regulatory_qatar",
        "description": "Qatar: QCDD/Kahramaa energy regulations reference required",
        "keywords": ["qcdd", "kahramaa"],
    },
}

# Minimum number of supplier quotations to satisfy the 3-bid rule
_MIN_QUOTATION_COUNT = 3

# Budget margin: estimated cost is only flagged if it exceeds budget by this multiplier
_BUDGET_EXCESS_MARGIN = 1.10


class ComplianceService:
    """Stateless compliance checking for procurement recommendations and benchmarks."""

    # ------------------------------------------------------------------
    # Public entry-point
    # ------------------------------------------------------------------

    @staticmethod
    def check_recommendation(
        request: ProcurementRequest,
        recommendation: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run all compliance rules against a procurement recommendation.

        Returns a dict:
          {status, rules_checked, violations, recommendations, domain_detail}
        """
        rules_checked: List[Dict[str, str]] = []
        violations: List[Dict[str, str]] = []
        recommendations_out: List[str] = []
        domain_detail: Dict[str, Any] = {}

        # -- 1. Core rules ------------------------------------------------

        rules_checked.append({
            "rule": "recommendation_present",
            "description": "A recommendation must be provided",
        })
        if not recommendation.get("recommended_option"):
            violations.append({
                "rule": "recommendation_present",
                "detail": "No recommended option provided",
            })

        rules_checked.append({
            "rule": "confidence_threshold",
            "description": "Recommendation confidence must be >= 0.5",
        })
        confidence = float(recommendation.get("confidence") or 0)
        if confidence < 0.5:
            violations.append({
                "rule": "confidence_threshold",
                "detail": f"Confidence {confidence:.2f} is below minimum threshold 0.50",
            })
            recommendations_out.append(
                "Consider gathering more requirements or consulting a domain expert."
            )

        # Budget check (only when budget attribute is present)
        budget = ComplianceService._get_budget(request)
        if budget is not None:
            rules_checked.append({
                "rule": "budget_check",
                "description": "Estimated cost must not exceed approved budget",
            })
            estimated_cost = recommendation.get("estimated_cost")
            if estimated_cost:
                try:
                    cost_f = float(estimated_cost)
                    threshold = budget * _BUDGET_EXCESS_MARGIN
                    if cost_f > threshold:
                        violations.append({
                            "rule": "budget_check",
                            "detail": (
                                f"Estimated cost {cost_f:,.2f} exceeds budget {budget:,.2f} "
                                f"(>{(_BUDGET_EXCESS_MARGIN - 1) * 100:.0f}% margin)"
                            ),
                        })
                except (TypeError, ValueError):
                    pass

        # -- 2. Quotation rules -------------------------------------------
        for item in ComplianceService._check_quotation_rules(request):
            rules_checked.extend(item[0])
            violations.extend(item[1])
            recommendations_out.extend(item[2])

        # -- 3. Geography checks ------------------------------------------
        geo_r, geo_v, geo_rec = ComplianceService._check_geography(request, recommendation)
        rules_checked.extend(geo_r)
        violations.extend(geo_v)
        recommendations_out.extend(geo_rec)

        # -- 4. Domain-specific rules (HVAC) ------------------------------
        domain_code = (getattr(request, "domain_code", "") or "").upper()
        if domain_code == "HVAC":
            try:
                from apps.procurement.services.domain.hvac.hvac_compliance_service import (
                    HVACComplianceService,
                )
                from apps.procurement.services.request_service import AttributeService

                attrs = AttributeService.get_attributes_dict(request)
                hvac_result = HVACComplianceService.check(attrs, recommendation)
                rules_checked.extend(hvac_result.get("rules_checked") or [])
                violations.extend(hvac_result.get("violations") or [])
                recommendations_out.extend(hvac_result.get("recommendations") or [])
                domain_detail = {
                    "hvac_alignment": hvac_result.get("hvac_alignment"),
                    "hvac_compliance_status": str(hvac_result.get("status", "")),
                }
            except Exception as exc:  # noqa: BLE001
                logger.warning("HVACComplianceService check failed (non-fatal): %s", exc)

        # -- Determine overall status -------------------------------------
        fail_count = len(violations)
        if fail_count == 0:
            status = ComplianceStatus.PASS
        elif fail_count == 1:
            status = ComplianceStatus.PARTIAL
        else:
            status = ComplianceStatus.FAIL

        return {
            "status": status,
            "rules_checked": rules_checked,
            "violations": violations,
            "recommendations": list(dict.fromkeys(recommendations_out)),  # deduplicate, preserve order
            "domain_detail": domain_detail,
        }

    # ------------------------------------------------------------------
    # Benchmark check
    # ------------------------------------------------------------------

    @staticmethod
    def check_benchmark(
        request: ProcurementRequest,
        benchmark_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Check benchmark results against compliance rules."""
        rules_checked: List[Dict[str, str]] = []
        violations: List[Dict[str, str]] = []
        recommendations_out: List[str] = []

        # Variance limit
        variance_pct = benchmark_summary.get("variance_pct")
        rules_checked.append({
            "rule": "variance_limit",
            "description": "Overall variance should be <=30% (critically high >50%)",
        })
        if variance_pct is not None:
            try:
                v = abs(float(variance_pct))
                if v > 50:
                    violations.append({
                        "rule": "variance_limit",
                        "detail": (
                            f"Variance {variance_pct}% is critically high (>50%) -- "
                            "escalation to Finance required"
                        ),
                    })
                    recommendations_out.append(
                        "Escalate to Finance for re-pricing or alternative supplier selection."
                    )
                elif v > 30:
                    violations.append({
                        "rule": "variance_limit",
                        "detail": f"Variance {variance_pct}% exceeds 30% threshold",
                    })
            except (TypeError, ValueError):
                pass

        # Benchmark requires at least 2 quotations to be meaningful
        rules_checked.append({
            "rule": "benchmark_min_quotations",
            "description": "Benchmark requires >= 2 quotations to be statistically meaningful",
        })
        quot_manager = getattr(request, "quotations", None)
        quot_count = quot_manager.count() if quot_manager is not None else 0
        if quot_count < 2:
            violations.append({
                "rule": "benchmark_min_quotations",
                "detail": (
                    f"Only {quot_count} quotation(s) available; "
                    "benchmark accuracy is unreliable with fewer than 2."
                ),
            })

        status = ComplianceStatus.PASS if not violations else ComplianceStatus.FAIL
        return {
            "status": status,
            "rules_checked": rules_checked,
            "violations": violations,
            "recommendations": recommendations_out,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_budget(request: ProcurementRequest) -> float | None:
        """Return the numeric budget attribute value if present."""
        attr_manager = getattr(request, "attributes", None)
        if attr_manager is None:
            return None
        for attr in attr_manager.filter(attribute_code="budget"):
            if attr.value_number is not None:
                return float(attr.value_number)
        return None

    @staticmethod
    def _check_quotation_rules(
        request: ProcurementRequest,
    ) -> List[Tuple[List, List, List]]:
        """Apply 3-bid rule, single-source risk, and collusion detection.

        Returns a list of (rules, violations, recommendations) tuples so the
        caller can unpack each group independently.
        """
        results = []

        quot_manager = getattr(request, "quotations", None)
        quotations = list(quot_manager.all()) if quot_manager is not None else []
        quot_count = len(quotations)

        # --- 3-bid rule ---------------------------------------------------
        three_bid_rules: List[Dict[str, str]] = [{
            "rule": "three_bid_rule",
            "description": (
                f"Minimum {_MIN_QUOTATION_COUNT} supplier quotations required "
                "for competitive procurement"
            ),
        }]
        three_bid_violations: List[Dict[str, str]] = []
        three_bid_recs: List[str] = []

        if quot_count == 0:
            # No quotations attached yet -- advisory only, not a blocking violation.
            # At recommendation stage the owner may not have issued an RFQ yet.
            three_bid_recs.append(
                "Quotations have not been submitted yet. "
                f"Ensure at least {_MIN_QUOTATION_COUNT} competitive quotations are obtained "
                "before award decision."
            )
        elif quot_count < _MIN_QUOTATION_COUNT:
            if quot_count == 1:
                detail = (
                    "Only 1 quotation received -- single-source procurement. "
                    "A written justification is required for this procurement."
                )
            else:
                detail = (
                    f"Only {quot_count} quotation(s) received; "
                    f"minimum {_MIN_QUOTATION_COUNT} required for a competitive award."
                )
            three_bid_violations.append({"rule": "three_bid_rule", "detail": detail})
            three_bid_recs.append(
                "Obtain additional quotations or provide a documented "
                "single-source justification signed by an authorised approver."
            )
        results.append((three_bid_rules, three_bid_violations, three_bid_recs))

        # --- Single-source risk -------------------------------------------
        ss_rules: List[Dict[str, str]] = [{
            "rule": "single_source_risk",
            "description": "Procurement from a single vendor must be commercially justified",
        }]
        ss_violations: List[Dict[str, str]] = []
        ss_recs: List[str] = []

        unique_vendors = {
            (q.vendor_name or "").strip().lower()
            for q in quotations
            if q.vendor_name
        }
        if len(unique_vendors) == 1 and quot_count >= 1:
            ss_violations.append({
                "rule": "single_source_risk",
                "detail": (
                    f"All {quot_count} quotation(s) are from the same vendor "
                    f"('{next(iter(unique_vendors))}'). "
                    "Sole-source justification must be on file."
                ),
            })
            ss_recs.append(
                "Document the commercial or technical justification for a sole-source award."
            )
        results.append((ss_rules, ss_violations, ss_recs))

        # --- Price collusion signal (multiple vendors, all identical amounts) ---
        if len(unique_vendors) >= 2:
            pc_rules: List[Dict[str, str]] = [{
                "rule": "price_collusion_check",
                "description": (
                    "Identical submitted prices from different vendors may indicate collusion"
                ),
            }]
            pc_violations: List[Dict[str, str]] = []
            pc_recs: List[str] = []

            amounts = [
                float(q.total_amount)
                for q in quotations
                if q.total_amount is not None
            ]
            if len(amounts) >= 2 and len(set(amounts)) == 1:
                pc_violations.append({
                    "rule": "price_collusion_check",
                    "detail": (
                        f"All {len(amounts)} quotation amounts are identical "
                        f"({amounts[0]:,.2f}). Possible collusion -- refer to Compliance team."
                    ),
                })
                pc_recs.append(
                    "Refer for Compliance investigation before proceeding with vendor award."
                )
            results.append((pc_rules, pc_violations, pc_recs))

        return results

    @staticmethod
    def _check_geography(
        request: ProcurementRequest,
        recommendation: Dict[str, Any],
    ) -> Tuple[List, List, List]:
        """Check that country-specific regulatory references are present in notes."""
        rules: List[Dict[str, str]] = []
        violations: List[Dict[str, str]] = []
        recs: List[str] = []

        country = (str(getattr(request, "geography_country", "") or "")).strip().upper()
        if not country or country not in _GEO_REQUIREMENTS:
            return rules, violations, recs

        geo = _GEO_REQUIREMENTS[country]
        rules.append({
            "rule": geo["rule_code"],
            "description": geo["description"],
        })

        # Build a combined text blob from notes and standards references in the recommendation
        notes_text = " ".join(str(n).lower() for n in (recommendation.get("notes") or []))
        standards_text = str(recommendation.get("standards_notes") or "").lower()
        combined = notes_text + " " + standards_text

        has_any_notes = bool(combined.strip())
        missing = [kw for kw in geo["keywords"] if kw not in combined]

        if missing:
            if has_any_notes:
                # Notes exist but the required authority keywords are absent -- blocking violation
                violations.append({
                    "rule": geo["rule_code"],
                    "detail": (
                        f"{geo['label']} regulatory reference missing from recommendation notes. "
                        f"Expected reference to: {', '.join(missing)}."
                    ),
                })
            # Always add the advisory recommendation regardless
            recs.append(
                f"Add {geo['label']} authority/regulatory submission reference "
                "before IFC/tender issue."
            )

        return rules, violations, recs
