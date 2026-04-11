"""RecommendationService -- orchestrates the product/solution recommendation flow.

Phase 1 agentic bridge: AI invocation now routes through ProcurementAgentOrchestrator
so every LLM call has standard audit, trace, and execution records consistent
with the wider platform. Deterministic logic is unchanged.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from django.db import transaction

from apps.auditlog.services import AuditService
from apps.core.decorators import observed_service
from apps.core.enums import (
    AnalysisRunStatus,
    AnalysisRunType,
    ComplianceStatus,
    ProcurementRequestStatus,
)
from apps.core.trace import TraceContext
from apps.procurement.models import (
    AnalysisRun,
    ComplianceResult,
    ProcurementRequest,
    RecommendationResult,
)
from apps.procurement.runtime import ProcurementAgentMemory, ProcurementAgentOrchestrator
from apps.procurement.services.analysis_run_service import AnalysisRunService
from apps.procurement.services.request_service import (
    AttributeService,
    ProcurementRequestService,
)

logger = logging.getLogger(__name__)


class RecommendationService:
    """Orchestrates the recommendation analysis flow.

    Implements Section 5.3 of the HVAC Procurement Requirement Document.

    Steps:
      1. Validate input completeness and normalize categorical values.
      2. Determine project archetype: MALL_FCU_INTERFACE, STANDALONE_RETAIL,
         HIGH_LOAD_LARGE_FORMAT, or RETROFIT_REPLACEMENT.
      3. Run deterministic decision rules against core discriminators (store
         type, area, heat load, ambient, humidity, dust, landlord restrictions,
         budget posture).
      4. Use AI reasoning to generate explanation, edge-case handling, and
         trade-off statements where multiple options remain feasible.
      5. Run standards validation and compliance check.
      6. Persist recommendation result, confidence score, source classes used,
         and recommendation narrative.
    """

    @staticmethod
    @observed_service("procurement.recommendation.run", audit_event="RECOMMENDATION_RUN_STARTED")
    def run_recommendation(
        request: ProcurementRequest,
        run: AnalysisRun,
        *,
        use_ai: bool = True,
        request_user: Any = None,
    ) -> RecommendationResult:
        AnalysisRunService.start_run(run)

        thought_log: list = []
        source_classes: list = []

        try:
            # ── Step 1: Validate input completeness + normalize ──────────────────
            attrs = AttributeService.get_attributes_dict(request)
            validation_report = RecommendationService._validate_and_normalize(attrs)
            source_classes.append("InputValidator")
            thought_log.append({
                "step": 1,
                "stage": "VALIDATE_NORMALIZE",
                "decision": "VALIDATED" if validation_report["passed"] else "INCOMPLETE",
                "reasoning": (
                    f"{len(validation_report['missing'])} required field(s) missing: "
                    f"{validation_report['missing']}. "
                    f"{validation_report['normalized_count']} field(s) normalized. "
                    + ("Proceeding with partial data." if not validation_report["passed"] else "All required fields present.")
                ),
            })

            # ── Step 2: Determine project archetype ──────────────────────────────
            archetype = RecommendationService._determine_archetype(attrs)
            source_classes.append("ArchetypeClassifier")
            thought_log.append({
                "step": 2,
                "stage": "ARCHETYPE_CLASSIFICATION",
                "decision": archetype["code"],
                "reasoning": archetype["reasoning"],
            })

            # ── Step 3: Deterministic decision rules ─────────────────────────────
            rule_result = RecommendationService._apply_rules(request, attrs)
            # Attach archetype to rule result so it propagates to output_payload
            rule_result["archetype"] = archetype
            source_classes.append("HVACRulesEngine")
            # If the HVAC agent stepped in (no DB rule matched), record it separately
            if (rule_result.get("reasoning_details") or {}).get("source") == "hvac_agent":
                source_classes.append("HVACRecommendationAgent")
            thought_log.append({
                "step": 3,
                "stage": "DETERMINISTIC_RULES",
                "decision": (
                    rule_result.get("system_type_code")
                    or ("CONFIDENT" if rule_result.get("confident") else "DEFERRED_TO_AI")
                ),
                "reasoning": (rule_result.get("reasoning_summary") or "")[:400],
            })

            # ── Step 4: AI reasoning (edge-cases + trade-offs) ───────────────────
            # AI is invoked only when the rules engine could not produce a
            # confident deterministic result (missing attrs, unknown type, etc.).
            ai_result = None
            if use_ai and not rule_result.get("confident", False):
                ai_result = RecommendationService._invoke_ai_via_orchestrator(
                    request=request,
                    run=run,
                    attrs=attrs,
                    rule_result=rule_result,
                    archetype=archetype,
                    validation_report=validation_report,
                    request_user=request_user,
                )
                source_classes.append("RecommendationAgent")
                thought_log.append({
                    "step": 4,
                    "stage": "AI_REASONING",
                    "decision": (ai_result or {}).get("recommended_option") or "NO_RESULT",
                    "reasoning": ((ai_result or {}).get("reasoning_summary") or "")[:400],
                })
            else:
                thought_log.append({
                    "step": 4,
                    "stage": "AI_REASONING",
                    "decision": "SKIPPED_RULES_CONFIDENT",
                    "reasoning": (
                        "Deterministic rules engine produced a confident result "
                        f"(confidence={rule_result.get('confidence', 0):.2f}); "
                        "AI step not required."
                    ),
                })

            # Merge deterministic + AI results
            final = RecommendationService._merge_recommendation_result(rule_result, ai_result)

            # ── Step 5: Standards validation + compliance check ──────────────────
            # Phase A: rule-based checks (always runs)
            compliance_status = ComplianceStatus.NOT_CHECKED
            compliance_data = None
            ai_compliance_data = None
            if final.get("recommended_option"):
                from apps.procurement.services.compliance_service import ComplianceService
                compliance_data = ComplianceService.check_recommendation(request, final)
                compliance_status = compliance_data.get("status", ComplianceStatus.NOT_CHECKED)
            source_classes.append("ComplianceService")

            # Phase B: AI augmentation -- invoked when rule engine returns PARTIAL
            # (some violations flagged but the recommendation is not outright rejected).
            # The AI is asked to surface additional risks the deterministic rules miss.
            if (
                compliance_status == ComplianceStatus.PARTIAL
                and use_ai
                and final.get("recommended_option")
            ):
                try:
                    from apps.procurement.agents.compliance_agent import ComplianceAgent

                    ai_context = dict(final)
                    ai_context["violations"] = (compliance_data or {}).get("violations") or []
                    ai_compliance_data = ComplianceAgent.check(
                        request=request,
                        context=ai_context,
                        attrs=attrs,  # attrs already fetched in step 1
                    )
                    # Merge AI findings into compliance_data
                    if ai_compliance_data and compliance_data:
                        compliance_data["rules_checked"] = (
                            list(compliance_data.get("rules_checked") or [])
                            + list(ai_compliance_data.get("rules_checked") or [])
                        )
                        compliance_data["violations"] = (
                            list(compliance_data.get("violations") or [])
                            + list(ai_compliance_data.get("violations") or [])
                        )
                        compliance_data["recommendations"] = list(
                            dict.fromkeys(
                                list(compliance_data.get("recommendations") or [])
                                + [
                                    str(r)
                                    for r in (ai_compliance_data.get("recommendations") or [])
                                ]
                            )
                        )
                        compliance_data["ai_augmented"] = True
                        compliance_data["domain_flags"] = ai_compliance_data.get("domain_flags") or []
                        compliance_data["geography_flags"] = ai_compliance_data.get("geography_flags") or []

                    # Re-evaluate overall status from merged violation count
                    total_violations = len(compliance_data.get("violations") or [])
                    if total_violations == 0:
                        compliance_status = ComplianceStatus.PASS
                    elif total_violations == 1:
                        compliance_status = ComplianceStatus.PARTIAL
                    else:
                        compliance_status = ComplianceStatus.FAIL

                    source_classes.append("ComplianceAgent")
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "ComplianceAgent (AI) raised an error (non-fatal, keeping rule result): %s",
                        exc,
                    )

            ai_violations_count = len((ai_compliance_data or {}).get("violations") or [])
            thought_log.append({
                "step": 5,
                "stage": "STANDARDS_VALIDATION",
                "decision": str(compliance_status),
                "reasoning": (
                    f"Compliance check: {compliance_status}. "
                    + (
                        f"Violations: {len(compliance_data.get('violations') or [])} "
                        f"(incl. {ai_violations_count} from AI). "
                        f"Standards checked: {len(compliance_data.get('rules_checked') or [])}. "
                        f"AI augmented: {bool(compliance_data and compliance_data.get('ai_augmented'))}."
                        if compliance_data else "No compliance data returned."
                    )
                ),
            })

            # ── Step 6: Persist ──────────────────────────────────────────────────
            final["source_classes_used"] = source_classes
            final["archetype"] = archetype

            with transaction.atomic():
                result = RecommendationResult.objects.create(
                    run=run,
                    recommended_option=final.get("recommended_option", "No recommendation"),
                    reasoning_summary=final.get("reasoning_summary", ""),
                    reasoning_details_json=final.get("reasoning_details"),
                    confidence_score=RecommendationService._normalize_confidence(final.get("confidence", 0.0)),
                    constraints_json=final.get("constraints"),
                    compliance_status=compliance_status,
                    output_payload_json=final,
                    tenant=tenant,
                )

                if compliance_data:
                    ComplianceResult.objects.create(
                        run=run,
                        compliance_status=compliance_status,
                        rules_checked_json=compliance_data.get("rules_checked"),
                        violations_json=compliance_data.get("violations"),
                        recommendations_json=compliance_data.get("recommendations"),
                        tenant=tenant,
                    )

                # Save step-by-step thought log on the run record for full traceability
                run.thought_process_log = thought_log
                run.save(update_fields=["thought_process_log"])

            # Finalize run and update request status
            AnalysisRunService.complete_run(
                run,
                output_summary=result.recommended_option,
                confidence_score=result.confidence_score,
            )
            new_status = (
                ProcurementRequestStatus.COMPLETED
                if compliance_status != ComplianceStatus.FAIL
                else ProcurementRequestStatus.REVIEW_REQUIRED
            )
            ProcurementRequestService.update_status(request, new_status, user=run.triggered_by)

            return result

        except Exception as exc:
            AnalysisRunService.fail_run(run, str(exc))
            ProcurementRequestService.update_status(
                request, ProcurementRequestStatus.FAILED, user=run.triggered_by,
            )
            raise

    # ------------------------------------------------------------------
    # Step 1 -- Input validation + normalization
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_and_normalize(attrs: Dict[str, Any]) -> Dict[str, Any]:
        """Validate attribute completeness and normalize values in-place.

        Returns a validation report dict:
          {
            passed: bool,              -- all required fields present
            missing: list[str],        -- required field codes that are absent
            normalized_count: int,     -- number of fields normalized
            warnings: list[str],       -- non-blocking issues
          }
        SELECT values are uppercased; NUMBER strings are cast to float;
        blank/None TEXT values remain as-is but are reported as warnings.
        """
        try:
            from apps.procurement.hvac.constants import HVAC_REQUIRED_FOR_RECOMMENDATION, HVAC_ATTRIBUTE_SCHEMA
            required_codes = HVAC_REQUIRED_FOR_RECOMMENDATION
            _SELECT_CODES = {s["code"] for s in HVAC_ATTRIBUTE_SCHEMA if s.get("data_type") == "SELECT"}
            _NUMBER_CODES = {s["code"] for s in HVAC_ATTRIBUTE_SCHEMA if s.get("data_type") == "NUMBER"}
        except ImportError:
            required_codes = set()
            _SELECT_CODES: set = set()
            _NUMBER_CODES: set = set()

        missing = []
        warnings = []
        normalized_count = 0

        for code, val in list(attrs.items()):
            if val is None or (isinstance(val, str) and not val.strip()):
                continue  # will be caught by the missing check below

            if code in _NUMBER_CODES:
                # Cast numeric fields whether they arrive as int, float, or string
                try:
                    casted = float(val)
                    if casted != val:  # avoid writing back if already a matching float
                        attrs[code] = casted
                        normalized_count += 1
                except (ValueError, TypeError):
                    warnings.append(
                        f"{code}: expected NUMBER, got non-numeric value '{val}' "
                        "-- field will be ignored by the rules engine"
                    )
            elif code in _SELECT_CODES and isinstance(val, str):
                # Normalize SELECT values to uppercase
                upper = val.strip().upper()
                if upper != val:
                    attrs[code] = upper
                    normalized_count += 1
            elif isinstance(val, str):
                # TEXT fields -- strip leading/trailing whitespace only
                stripped = val.strip()
                if stripped != val:
                    attrs[code] = stripped
                    normalized_count += 1
        # Check required fields
        for code in required_codes:
            v = attrs.get(code)
            if v is None or (isinstance(v, str) and not v.strip()):
                missing.append(code)

        return {
            "passed": len(missing) == 0,
            "missing": missing,
            "normalized_count": normalized_count,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # Step 2 -- Project archetype classification
    # ------------------------------------------------------------------

    @staticmethod
    def _determine_archetype(attrs: Dict[str, Any]) -> Dict[str, str]:
        """Classify the project into one of four archetypes.

        Archetypes (from Section 5.3 of the requirement document):
          MALL_FCU_INTERFACE    -- Mall store with chilled water backbone
          STANDALONE_RETAIL     -- Freestanding retail / office format
          HIGH_LOAD_LARGE_FORMAT -- High heat load >= 20,000 sqft large store
          RETROFIT_REPLACEMENT  -- Existing HVAC system being replaced / extended

        Returns {"code": str, "reasoning": str}.
        """
        store_type = (attrs.get("store_type") or "").upper()
        area_sqft = 0.0
        try:
            area_sqft = float(attrs.get("area_sqft") or 0)
        except (TypeError, ValueError):
            pass
        heat_load = (attrs.get("heat_load_category") or "").upper()
        landlord = (attrs.get("landlord_constraints") or "").lower()
        existing_hvac = (attrs.get("existing_hvac_type") or "").lower()

        # RETROFIT_REPLACEMENT: non-trivial existing HVAC described
        existing_signals = ["chilled water", "split", "vrf", "packaged", "fcu", "ahu", "central"]
        if existing_hvac and any(sig in existing_hvac for sig in existing_signals):
            return {
                "code": "RETROFIT_REPLACEMENT",
                "reasoning": (
                    f"Existing HVAC type '{existing_hvac[:80]}' indicates a retrofit or "
                    "replacement project. Compatibility with existing infrastructure is a key constraint."
                ),
            }

        # MALL_FCU_INTERFACE: mall with chilled water availability
        cw_signals = ["chilled water", " cw ", "chw", "building chilled"]
        if store_type == "MALL" and any(sig in landlord for sig in cw_signals):
            return {
                "code": "MALL_FCU_INTERFACE",
                "reasoning": (
                    "Mall store with chilled water infrastructure indicated in landlord constraints. "
                    "FCU on central chilled water plant is the primary archetype."
                ),
            }

        # HIGH_LOAD_LARGE_FORMAT: large area + high heat load
        if heat_load == "HIGH" and area_sqft >= 20000:
            return {
                "code": "HIGH_LOAD_LARGE_FORMAT",
                "reasoning": (
                    f"Large format store ({area_sqft:,.0f} sqft) with HIGH heat load category. "
                    "Chiller plant or high-capacity VRF / packaged system archetype."
                ),
            }

        # STANDALONE_RETAIL: default for non-mall formats
        return {
            "code": "STANDALONE_RETAIL",
            "reasoning": (
                f"Store type '{store_type}' with no overriding signals for other archetypes. "
                "Standard standalone retail system selection pathway applies."
            ),
        }

    # ------------------------------------------------------------------
    # Phase 1: AI routing through the orchestrator bridge
    # ------------------------------------------------------------------

    @staticmethod
    def _invoke_ai_via_orchestrator(
        *,
        request: ProcurementRequest,
        run: AnalysisRun,
        attrs: Dict[str, Any],
        rule_result: Dict[str, Any],
        archetype: Dict[str, Any] | None = None,
        validation_report: Dict[str, Any] | None = None,
        request_user: Any = None,
    ) -> Dict[str, Any] | None:
        """Route the AI recommendation call through ProcurementAgentOrchestrator.

        This replaces direct RecommendationGraphService.run() with a standardised
        bridge that adds audit events, execution records, and Langfuse tracing.
        Downstream, the orchestrator still calls RecommendationGraphService.
        """
        from apps.procurement.services.recommendation_graph_service import RecommendationGraphService

        orchestrator = ProcurementAgentOrchestrator()

        def _agent_fn(ctx):
            return RecommendationGraphService.run(
                request=request,
                run=run,
                attributes=attrs,
                rule_result=rule_result,
                archetype=archetype,
                validation_context=validation_report,
            )

        orch_result = orchestrator.run(
            run=run,
            agent_type="recommendation",
            agent_fn=_agent_fn,
            extra_context={
                "rule_result": rule_result,
                "constraints": rule_result.get("constraints") or [],
            },
            request_user=request_user,
        )

        if orch_result.status == "failed":
            logger.warning(
                "RecommendationService: orchestrator agent failed -- falling back to rule result. Error: %s",
                orch_result.error,
            )
            return None

        return orch_result.output or None

    # ------------------------------------------------------------------
    # Deterministic helpers (unchanged from before)
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_rules(
        request: ProcurementRequest,
        attrs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Apply deterministic rule-based recommendation logic.

        Routes to the domain-specific rule engine. Currently supports HVAC.
        For other domains returns a low-confidence empty result so AI picks up.

        Returns a dict with:
          recommended_option (str), reasoning_summary (str),
          confident (bool), confidence (float), constraints (list),
          reasoning_details (dict)
        """
        domain = getattr(request, "domain_code", "") or ""

        if domain.upper() == "HVAC":
            try:
                from apps.procurement.hvac.rules_engine import HVACRulesEngine
                geography = ""
                if hasattr(request, "geography_country") and request.geography_country:
                    geography = request.geography_country
                elif hasattr(request, "location") and request.location:
                    geography = str(request.location)
                rule_result = HVACRulesEngine.evaluate(
                    domain_code="HVAC",
                    attrs=attrs,
                    geography_country=geography,
                )
            except Exception:
                logger.exception("HVACRulesEngine.evaluate failed -- falling back to HVAC agent.")
                rule_result = {
                    "recommended_option": "",
                    "reasoning_summary": "Rules engine error -- invoking HVAC recommendation agent.",
                    "confident": False,
                    "confidence": 0.0,
                    "constraints": [],
                    "reasoning_details": {"source": "rules_engine_error"},
                }

            # -- Rules engine produced a confident match: return immediately -------
            if rule_result.get("confident") and rule_result.get("recommended_option"):
                return rule_result

            # -- Rules engine was not confident: invoke HVACRecommendationAgent ----
            # Only escalate to the agent when the attributes are present but no rule
            # matched (missing_attrs means incomplete input -- the agent cannot help
            # with missing data any better than the rules engine).
            rd = rule_result.get("reasoning_details") or {}
            has_missing_attrs = bool(rd.get("missing_attrs"))
            if not has_missing_attrs:
                logger.info(
                    "RecommendationService: no DB rule matched for HVAC request pk=%s "
                    "(%d rules evaluated) -- invoking HVACRecommendationAgent.recommend()",
                    getattr(request, "pk", "?"),
                    rd.get("rules_evaluated", 0),
                )
                try:
                    from apps.procurement.agents.hvac_recommendation_agent import (
                        HVACRecommendationAgent,
                    )
                    agent_result = HVACRecommendationAgent.recommend(
                        attrs=attrs,
                        no_match_context=rd,
                        procurement_request_pk=getattr(request, "pk", None),
                    )
                    # If the agent produced a usable recommendation, return it.
                    # Even a low-confidence agent result is preferable to an empty
                    # rules result because it carries reasoning_summary + constraints.
                    if agent_result.get("recommended_option"):
                        logger.info(
                            "HVACRecommendationAgent.recommend: returning system=%s "
                            "confidence=%.2f for request pk=%s",
                            agent_result.get("system_type_code"),
                            agent_result.get("confidence", 0),
                            getattr(request, "pk", "?"),
                        )
                        return agent_result
                    # Agent returned empty -- fall through to original no-match result
                    logger.warning(
                        "HVACRecommendationAgent.recommend returned no system for pk=%s; "
                        "returning original no-match result for orchestrator AI fallback.",
                        getattr(request, "pk", "?"),
                    )
                except Exception:
                    logger.exception(
                        "HVACRecommendationAgent.recommend raised an exception for pk=%s; "
                        "returning original no-match result.",
                        getattr(request, "pk", "?"),
                    )

            # Fall through: return the rules engine no-match result so the
            # orchestrator's generic AI step (RecommendationGraphService) picks it up.
            return rule_result

        # Non-HVAC domain: no deterministic rules yet
        return {
            "recommended_option": "",
            "reasoning_summary": f"No deterministic rules for domain '{domain}' -- deferring to AI.",
            "confident": False,
            "confidence": 0.0,
            "constraints": [],
            "reasoning_details": {"source": "rules_engine", "domain": domain},
        }

    # ------------------------------------------------------------------
    # Result merging and normalization helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_recommendation_result(
        rule_result: Dict[str, Any],
        ai_result: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        """Merge deterministic rule output with AI result.

        Priority:
          - If rules produced a confident result, use it directly.
          - If AI produced output, use AI (which may already incorporate rule hints).
          - Fallback: best available from either source.
        """
        if rule_result.get("confident") and rule_result.get("recommended_option"):
            # Rules engine found a confident match -- return it directly.
            # AI was not invoked (see gate above), so ai_result is None here.
            # If somehow ai_result exists, merge constraints only.
            merged = dict(rule_result)
            if ai_result:
                ai_constraints = ai_result.get("constraints") or []
                rule_constraints = merged.get("constraints") or []
                merged["constraints"] = rule_constraints + [
                    c for c in ai_constraints if c not in rule_constraints
                ]
            return merged

        if ai_result and ai_result.get("recommended_option"):
            # AI produced a result -- carry over rule constraints as extra context
            merged = dict(ai_result)
            rule_constraints = rule_result.get("constraints") or []
            ai_constraints = merged.get("constraints") or []
            merged["constraints"] = rule_constraints + [
                c for c in ai_constraints if c not in rule_constraints
            ]
            return merged

        # Neither source produced a usable option -- return best available
        return rule_result if rule_result.get("recommended_option") else (ai_result or rule_result)

    @staticmethod
    def _normalize_confidence(value: Any) -> float:
        """Convert confidence to float in [0.0, 1.0]."""
        try:
            f = float(value)
            return max(0.0, min(1.0, f))
        except (TypeError, ValueError):
            return 0.0
