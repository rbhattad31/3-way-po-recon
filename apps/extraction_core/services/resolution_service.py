"""
JurisdictionResolutionService — Orchestrates jurisdiction resolution
using the 4-tier precedence chain.

Precedence (highest → lowest):
    1. Document-level override  (declared_country_code / declared_regime_code)
    2. Entity-level extraction profile  (EntityExtractionProfile)
    3. System-level runtime settings  (ExtractionRuntimeSettings)
    4. Auto-detection fallback  (JurisdictionResolverService)

Each tier may be in AUTO / FIXED / HYBRID mode which controls whether
detection runs:
    - AUTO   → always run detection
    - FIXED  → use configured jurisdiction, skip detection
    - HYBRID → use configured, detect as validation / fallback;
               generate warning on high-confidence mismatch

Public helper methods for each layer can be called independently,
making the service fully unit-testable without hitting the full
resolution cascade.

This service is STATELESS — all public methods are classmethods.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from apps.core.enums import JurisdictionMode, JurisdictionSource
from apps.extraction_core.models import (
    EntityExtractionProfile,
    ExtractionRuntimeSettings,
    TaxJurisdictionProfile,
)
from apps.extraction_core.services.jurisdiction_resolver import (
    JurisdictionResolution,
    JurisdictionResolverService,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default constants
# ---------------------------------------------------------------------------

DEFAULT_CONFIDENCE_THRESHOLD = 0.70
FIXED_CONFIDENCE = 1.0
HYBRID_ENTITY_CONFIDENCE = 0.95
HYBRID_SYSTEM_CONFIDENCE = 0.90
HYBRID_AGREEMENT_BOOST = 0.05


# ---------------------------------------------------------------------------
# Result data structures
# ---------------------------------------------------------------------------


class MismatchSeverity(str, Enum):
    """Severity of a HYBRID-mode jurisdiction mismatch."""
    NONE = "NONE"
    LOW = "LOW"           # detection below threshold
    HIGH = "HIGH"         # detection at or above threshold


@dataclass
class HybridMismatchDetail:
    """Details about a HYBRID-mode jurisdiction mismatch."""
    has_mismatch: bool = False
    severity: MismatchSeverity = MismatchSeverity.NONE
    configured_country: str = ""
    configured_regime: str = ""
    detected_country: str = ""
    detected_regime: str = ""
    detection_confidence: float = 0.0
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    message: str = ""


@dataclass
class ResolutionResult:
    """
    Outcome of the full jurisdiction resolution pipeline.

    Attributes:
        country_code:       Resolved ISO country code (e.g. "IN").
        regime_code:        Resolved tax regime (e.g. "GST").
        source:             Which tier produced the result (JurisdictionSource).
        confidence:         Overall confidence in the resolution (0.0–1.0).
        resolution_mode:    Mode used (AUTO / FIXED / HYBRID).
        warning_message:    Human-readable warning (e.g. HYBRID mismatch).
        jurisdiction:       Resolved TaxJurisdictionProfile (or None).
        detection_result:   Raw detection output (if detection was run).
        mismatch_detail:    Structured HYBRID mismatch info (if applicable).
        resolved:           Whether resolution succeeded.
        tiers_evaluated:    List of tier names that were evaluated.
    """
    country_code: str = ""
    regime_code: str = ""
    source: str = ""
    confidence: float = 0.0
    resolution_mode: str = ""
    warning_message: str = ""
    jurisdiction: Optional[TaxJurisdictionProfile] = None
    detection_result: Optional[JurisdictionResolution] = None
    mismatch_detail: Optional[HybridMismatchDetail] = None
    resolved: bool = False
    tiers_evaluated: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize for API responses."""
        result = {
            "resolved": self.resolved,
            "country_code": self.country_code,
            "regime_code": self.regime_code,
            "source": self.source,
            "confidence": round(self.confidence, 4),
            "resolution_mode": self.resolution_mode,
            "warning_message": self.warning_message,
            "jurisdiction_id": self.jurisdiction.pk if self.jurisdiction else None,
            "tiers_evaluated": self.tiers_evaluated,
        }
        if self.detection_result:
            result["detection"] = self.detection_result.to_dict()
        if self.mismatch_detail and self.mismatch_detail.has_mismatch:
            result["mismatch_detail"] = {
                "severity": self.mismatch_detail.severity.value,
                "configured_country": self.mismatch_detail.configured_country,
                "configured_regime": self.mismatch_detail.configured_regime,
                "detected_country": self.mismatch_detail.detected_country,
                "detected_regime": self.mismatch_detail.detected_regime,
                "detection_confidence": round(
                    self.mismatch_detail.detection_confidence, 4
                ),
                "threshold": self.mismatch_detail.threshold,
                "message": self.mismatch_detail.message,
            }
        return result


class JurisdictionResolutionService:
    """
    Orchestrates jurisdiction resolution across 4 precedence tiers.

    The public ``resolve()`` method walks down the precedence chain
    and delegates to per-tier helper methods.  Each helper can also be
    called independently for unit testing or selective resolution.

    Hybrid mismatch logic:
        When a tier operates in HYBRID mode, auto-detection runs as a
        guardrail.  If the configured and detected jurisdictions
        disagree:
            - HIGH severity: detection confidence >= threshold
              → strong warning, configured is kept as primary
            - LOW severity:  detection confidence < threshold
              → informational warning, configured is kept
        If they agree, a small confidence boost is applied.
    """

    # ------------------------------------------------------------------
    # Public API — full cascade
    # ------------------------------------------------------------------

    @classmethod
    def resolve(
        cls,
        ocr_text: str,
        *,
        declared_country_code: str = "",
        declared_regime_code: str = "",
        vendor_id: int | None = None,
        tenant=None,
    ) -> ResolutionResult:
        """
        Resolve jurisdiction for a document using the 4-tier chain.

        Args:
            ocr_text:              Raw OCR text (used for auto-detection).
            declared_country_code: Document-level country code override.
            declared_regime_code:  Document-level regime code override.
            vendor_id:             Optional vendor PK for entity profile lookup.

        Returns:
            ResolutionResult with the final jurisdiction, confidence,
            source tier, and any mismatch warnings.
        """
        settings = cls._load_settings(tenant=tenant)
        entity_profile = cls._load_entity_profile(vendor_id, tenant=tenant)
        tiers: list[str] = []

        # --- Tier 1: Document-level override ---
        if declared_country_code or declared_regime_code:
            tiers.append("DOCUMENT_OVERRIDE")
            result = cls.resolve_from_document_override(
                declared_country_code,
                declared_regime_code,
                settings=settings,
                ocr_text=ocr_text,
            )
            if result.resolved:
                result.tiers_evaluated = tiers
                return result

        # --- Tier 2: Entity-level extraction profile ---
        if entity_profile and entity_profile.is_active:
            tiers.append("ENTITY_PROFILE")
            result = cls.resolve_from_entity_profile(
                profile=entity_profile,
                settings=settings,
                ocr_text=ocr_text,
            )
            if result.resolved:
                result.tiers_evaluated = tiers
                return result

        # --- Tier 3: System-level runtime settings ---
        if settings and settings.is_active:
            tiers.append("RUNTIME_SETTINGS")
            result = cls.resolve_from_runtime_settings(
                settings=settings,
                ocr_text=ocr_text,
            )
            if result.resolved:
                result.tiers_evaluated = tiers
                return result

        # --- Tier 4: Auto-detection fallback ---
        if settings and not cls._should_run_detection(settings):
            return ResolutionResult(
                warning_message=(
                    "Auto-detection is disabled by runtime settings and no "
                    "configured jurisdiction could be resolved."
                ),
            )
        tiers.append("AUTO_DETECTION")
        result = cls.resolve_from_auto_detection(ocr_text=ocr_text)
        result.tiers_evaluated = tiers
        return result

    # ------------------------------------------------------------------
    # Tier 1 — Document-level override
    # ------------------------------------------------------------------

    @classmethod
    def resolve_from_document_override(
        cls,
        country_code: str,
        regime_code: str,
        *,
        settings: ExtractionRuntimeSettings | None = None,
        ocr_text: str = "",
    ) -> ResolutionResult:
        """
        Resolve from a document-level jurisdiction override.

        The override is honoured if ``allow_manual_override`` is True
        (or no settings exist).  Invalid overrides (no matching profile)
        return an unresolved result so the cascade continues.

        In HYBRID system mode, detection is run as a validation
        guardrail after accepting the override.

        Args:
            country_code: Declared ISO country code.
            regime_code:  Declared tax regime code.
            settings:     Active system runtime settings (or None).
            ocr_text:     Raw OCR text (for HYBRID validation).

        Returns:
            ResolutionResult — resolved if override is valid & permitted.
        """
        if not country_code and not regime_code:
            return ResolutionResult()

        # Gate: manual overrides may be disabled
        if settings and not settings.allow_manual_override:
            logger.info(
                "Document override provided (country=%s, regime=%s) but "
                "allow_manual_override=False — skipping",
                country_code, regime_code,
            )
            return ResolutionResult()

        jurisdiction = cls._lookup_jurisdiction(country_code, regime_code)
        if not jurisdiction:
            logger.warning(
                "Document override country=%s regime=%s has no matching "
                "jurisdiction profile — falling through",
                country_code, regime_code,
            )
            return ResolutionResult()

        result = ResolutionResult(
            country_code=jurisdiction.country_code,
            regime_code=jurisdiction.tax_regime,
            source=JurisdictionSource.DOCUMENT_OVERRIDE,
            confidence=FIXED_CONFIDENCE,
            resolution_mode=JurisdictionMode.FIXED,
            jurisdiction=jurisdiction,
            resolved=True,
        )

        # HYBRID validation guardrail (if system is in HYBRID mode)
        if settings and settings.jurisdiction_mode == JurisdictionMode.HYBRID:
            cls._apply_hybrid_guardrail(result, ocr_text, settings)

        return result

    # ------------------------------------------------------------------
    # Tier 2 — Entity-level extraction profile
    # ------------------------------------------------------------------

    @classmethod
    def resolve_from_entity_profile(
        cls,
        profile: EntityExtractionProfile,
        *,
        settings: ExtractionRuntimeSettings | None = None,
        ocr_text: str = "",
    ) -> ResolutionResult:
        """
        Resolve from a per-entity (vendor) extraction profile.

        Behaviour depends on the profile's ``jurisdiction_mode``:
            - AUTO   → returns unresolved (fall through to detection).
            - FIXED  → returns the configured jurisdiction directly.
            - HYBRID → returns configured + runs detection guardrail.

        If the configured jurisdiction has no matching DB profile and
        ``fallback_to_detection_on_schema_miss`` is enabled, falls
        back to auto-detection.

        Args:
            profile:  Active EntityExtractionProfile.
            settings: Active system runtime settings (or None).
            ocr_text: Raw OCR text (for HYBRID validation / fallback).

        Returns:
            ResolutionResult — resolved if profile provides a jurisdiction.
        """
        mode = profile.jurisdiction_mode

        if mode == JurisdictionMode.AUTO:
            logger.debug(
                "Entity profile %s is AUTO — deferring to next tier",
                profile.entity_id,
            )
            return ResolutionResult()

        if not profile.default_country_code:
            logger.debug(
                "Entity profile %s has no default_country_code — deferring",
                profile.entity_id,
            )
            return ResolutionResult()

        jurisdiction = cls._lookup_jurisdiction(
            profile.default_country_code,
            profile.default_regime_code,
        )

        # No matching DB profile
        if not jurisdiction:
            return cls._handle_missing_jurisdiction(
                label="Entity profile",
                country_code=profile.default_country_code,
                regime_code=profile.default_regime_code,
                mode=mode,
                settings=settings,
                ocr_text=ocr_text,
            )

        if mode == JurisdictionMode.FIXED:
            return ResolutionResult(
                country_code=jurisdiction.country_code,
                regime_code=jurisdiction.tax_regime,
                source=JurisdictionSource.ENTITY_PROFILE,
                confidence=FIXED_CONFIDENCE,
                resolution_mode=JurisdictionMode.FIXED,
                jurisdiction=jurisdiction,
                resolved=True,
            )

        # HYBRID
        result = ResolutionResult(
            country_code=jurisdiction.country_code,
            regime_code=jurisdiction.tax_regime,
            source=JurisdictionSource.ENTITY_PROFILE,
            confidence=HYBRID_ENTITY_CONFIDENCE,
            resolution_mode=JurisdictionMode.HYBRID,
            jurisdiction=jurisdiction,
            resolved=True,
        )
        cls._apply_hybrid_guardrail(result, ocr_text, settings)
        return result

    # ------------------------------------------------------------------
    # Tier 3 — System-level runtime settings
    # ------------------------------------------------------------------

    @classmethod
    def resolve_from_runtime_settings(
        cls,
        settings: ExtractionRuntimeSettings,
        *,
        ocr_text: str = "",
    ) -> ResolutionResult:
        """
        Resolve from system-level runtime settings.

        Behaviour depends on ``settings.jurisdiction_mode``:
            - AUTO   → returns unresolved (fall through to detection).
            - FIXED  → returns the default jurisdiction directly.
            - HYBRID → returns default + runs detection guardrail.

        Falls back to detection if no matching jurisdiction profile
        exists and ``fallback_to_detection_on_schema_miss`` is enabled.

        Args:
            settings: Active ExtractionRuntimeSettings.
            ocr_text: Raw OCR text (for HYBRID validation / fallback).

        Returns:
            ResolutionResult — resolved if settings provide a jurisdiction.
        """
        mode = settings.jurisdiction_mode

        if mode == JurisdictionMode.AUTO:
            if not cls._should_run_detection(settings):
                logger.info("System settings mode=AUTO but auto-detection is disabled")
                return ResolutionResult()
            logger.debug("System settings mode=AUTO — deferring to auto-detection")
            return ResolutionResult()

        if not settings.default_country_code:
            logger.debug("System settings have no default_country_code — deferring")
            return ResolutionResult()

        jurisdiction = cls._lookup_jurisdiction(
            settings.default_country_code,
            settings.default_regime_code,
        )

        # No matching DB profile
        if not jurisdiction:
            return cls._handle_missing_jurisdiction(
                label="System settings",
                country_code=settings.default_country_code,
                regime_code=settings.default_regime_code,
                mode=mode,
                settings=settings,
                ocr_text=ocr_text,
            )

        if mode == JurisdictionMode.FIXED:
            return ResolutionResult(
                country_code=jurisdiction.country_code,
                regime_code=jurisdiction.tax_regime,
                source=JurisdictionSource.SYSTEM_SETTINGS,
                confidence=FIXED_CONFIDENCE,
                resolution_mode=JurisdictionMode.FIXED,
                jurisdiction=jurisdiction,
                resolved=True,
            )

        # HYBRID
        result = ResolutionResult(
            country_code=jurisdiction.country_code,
            regime_code=jurisdiction.tax_regime,
            source=JurisdictionSource.SYSTEM_SETTINGS,
            confidence=HYBRID_SYSTEM_CONFIDENCE,
            resolution_mode=JurisdictionMode.HYBRID,
            jurisdiction=jurisdiction,
            resolved=True,
        )
        cls._apply_hybrid_guardrail(result, ocr_text, settings)
        return result

    # ------------------------------------------------------------------
    # Tier 4 — Auto-detection
    # ------------------------------------------------------------------

    @classmethod
    def resolve_from_auto_detection(
        cls,
        ocr_text: str,
    ) -> ResolutionResult:
        """
        Resolve by running the JurisdictionResolverService on OCR text.

        This is the lowest-precedence tier and acts as the catch-all
        fallback.  Returns unresolved if detection produces no result.

        Args:
            ocr_text: Raw OCR text to analyse.

        Returns:
            ResolutionResult populated from detection signals.
        """
        detection = cls._run_detection(ocr_text)

        if not detection.resolved:
            logger.info("Auto-detection produced no result for the given OCR text")
            return ResolutionResult(
                detection_result=detection,
                resolution_mode=JurisdictionMode.AUTO,
            )

        return ResolutionResult(
            country_code=detection.country_code,
            regime_code=detection.regime_code,
            source=JurisdictionSource.AUTO_DETECTED,
            confidence=detection.confidence,
            resolution_mode=JurisdictionMode.AUTO,
            jurisdiction=detection.jurisdiction,
            detection_result=detection,
            resolved=True,
        )

    # ------------------------------------------------------------------
    # HYBRID mismatch logic
    # ------------------------------------------------------------------

    @classmethod
    def _apply_hybrid_guardrail(
        cls,
        result: ResolutionResult,
        ocr_text: str,
        settings: ExtractionRuntimeSettings | None,
    ) -> None:
        """
        Run detection as a validation guardrail for HYBRID mode.

        Compares the configured jurisdiction against detected jurisdiction:
          - **Agreement** → small confidence boost, no warning.
          - **High-confidence disagreement** (detection >= threshold)
            → HIGH severity warning; configured jurisdiction stays primary.
          - **Low-confidence disagreement** (detection < threshold)
            → LOW severity warning; configured jurisdiction stays primary.

        Mutates ``result`` in-place: sets ``detection_result``,
        ``mismatch_detail``, ``warning_message``, and may adjust
        ``confidence``.

        Args:
            result:   The current ResolutionResult (must be resolved).
            ocr_text: Raw OCR text for detection.
            settings: Active system settings (for threshold config).
        """
        if not ocr_text or not ocr_text.strip():
            return
        if settings and not cls._should_run_detection(settings):
            logger.info("HYBRID guardrail skipped because auto-detection is disabled")
            return

        threshold = cls._get_confidence_threshold(settings)
        detection = cls._run_detection(ocr_text)
        result.detection_result = detection

        if not detection.resolved:
            return

        configured_cc = result.country_code.upper()
        detected_cc = detection.country_code.upper()
        configured_regime = result.regime_code.upper()
        detected_regime = detection.regime_code.upper()

        # --- Agreement ---
        if configured_cc == detected_cc:
            result.confidence = min(
                result.confidence + HYBRID_AGREEMENT_BOOST, 1.0
            )
            logger.debug(
                "HYBRID agreement: configured=%s/%s matches detected=%s/%s "
                "(confidence boosted to %.4f)",
                configured_cc, configured_regime,
                detected_cc, detected_regime,
                result.confidence,
            )
            return

        # --- Disagreement → build mismatch detail ---
        mismatch = HybridMismatchDetail(
            has_mismatch=True,
            configured_country=result.country_code,
            configured_regime=result.regime_code,
            detected_country=detection.country_code,
            detected_regime=detection.regime_code,
            detection_confidence=detection.confidence,
            threshold=threshold,
        )

        if detection.confidence >= threshold:
            mismatch.severity = MismatchSeverity.HIGH
            mismatch.message = (
                f"HYBRID MISMATCH [HIGH]: Configured jurisdiction "
                f"({result.country_code}/{result.regime_code}) strongly "
                f"disagrees with detected jurisdiction "
                f"({detection.country_code}/{detection.regime_code}, "
                f"confidence={detection.confidence:.4f}, "
                f"threshold={threshold:.2f}). "
                f"Keeping configured as primary."
            )
            logger.warning(mismatch.message)
        else:
            mismatch.severity = MismatchSeverity.LOW
            mismatch.message = (
                f"HYBRID MISMATCH [LOW]: Detection returned different "
                f"jurisdiction ({detection.country_code}/{detection.regime_code}) "
                f"but confidence {detection.confidence:.4f} is below "
                f"threshold {threshold:.2f}. "
                f"Keeping configured as primary."
            )
            logger.info(mismatch.message)

        result.mismatch_detail = mismatch
        result.warning_message = mismatch.message

    # ------------------------------------------------------------------
    # Internal helpers — DB / detection / config
    # ------------------------------------------------------------------

    @classmethod
    def _handle_missing_jurisdiction(
        cls,
        *,
        label: str,
        country_code: str,
        regime_code: str,
        mode: str,
        settings: ExtractionRuntimeSettings | None,
        ocr_text: str,
    ) -> ResolutionResult:
        """
        Handle the case where a configured country/regime has no matching
        TaxJurisdictionProfile in the database.

        In FIXED mode with ``fallback_to_detection_on_schema_miss``
        enabled, falls back to auto-detection.  Otherwise returns
        unresolved.

        Args:
            label:        Human label for log messages (e.g. "Entity profile").
            country_code: Configured country code that had no DB match.
            regime_code:  Configured regime code.
            mode:         JurisdictionMode of the calling tier.
            settings:     Active system settings (for fallback config).
            ocr_text:     Raw OCR text for fallback detection.

        Returns:
            ResolutionResult — either from fallback detection or unresolved.
        """
        logger.warning(
            "%s configured country=%s regime=%s has no matching "
            "jurisdiction profile",
            label, country_code, regime_code,
        )
        should_fallback = (
            settings
            and settings.fallback_to_detection_on_schema_miss
            and cls._should_run_detection(settings)
            and mode in (JurisdictionMode.FIXED, JurisdictionMode.HYBRID)
        )
        if should_fallback:
            logger.info(
                "Falling back to auto-detection (fallback_to_detection_on_schema_miss=True)"
            )
            result = cls.resolve_from_auto_detection(ocr_text)
            # Override source to reflect that this is a fallback path
            if result.resolved:
                result.source = JurisdictionSource.HYBRID_FALLBACK
                result.resolution_mode = JurisdictionMode.HYBRID
            return result

        return ResolutionResult()

    @classmethod
    def _run_detection(cls, ocr_text: str) -> JurisdictionResolution:
        """
        Run auto-detection via the JurisdictionResolverService.

        Isolated for testability — can be mocked in unit tests.
        """
        return JurisdictionResolverService.resolve(ocr_text)

    @classmethod
    def _lookup_jurisdiction(
        cls,
        country_code: str,
        regime_code: str = "",
    ) -> TaxJurisdictionProfile | None:
        """
        Find an active TaxJurisdictionProfile by country + optional regime.

        Isolated for testability — can be mocked in unit tests.

        Args:
            country_code: ISO country code (case-insensitive).
            regime_code:  Tax regime code (case-insensitive, optional).

        Returns:
            Matching profile or None.
        """
        qs = TaxJurisdictionProfile.objects.filter(
            country_code__iexact=country_code,
            is_active=True,
        )
        if regime_code:
            qs = qs.filter(tax_regime__iexact=regime_code)
        return qs.first()

    @classmethod
    def _load_settings(cls, tenant=None) -> ExtractionRuntimeSettings | None:
        """Load the active system runtime settings (or None)."""
        return ExtractionRuntimeSettings.get_active(tenant=tenant)

    @classmethod
    def _load_entity_profile(
        cls,
        vendor_id: int | None,
        tenant=None,
    ) -> EntityExtractionProfile | None:
        """
        Load the extraction profile for a vendor, if any.

        Args:
            vendor_id: Primary key of the vendor, or None.

        Returns:
            Active EntityExtractionProfile or None.
        """
        if not vendor_id:
            return None
        try:
            qs = EntityExtractionProfile.objects.select_related(
                "entity",
            ).filter(entity_id=vendor_id, is_active=True)
            if tenant is not None:
                qs = qs.filter(entity__tenant=tenant)
            return qs.get()
        except EntityExtractionProfile.DoesNotExist:
            return None

    @staticmethod
    def _should_run_detection(settings: ExtractionRuntimeSettings | None) -> bool:
        """Return whether auto-detection is enabled by runtime settings."""
        if settings is None:
            return True
        return bool(getattr(settings, "enable_jurisdiction_detection", True))

    @staticmethod
    def _get_confidence_threshold(
        settings: ExtractionRuntimeSettings | None,
    ) -> float:
        """
        Return the confidence threshold for HYBRID detection guardrail.

        Uses the system setting if available, otherwise falls back to
        the module-level default.
        """
        if settings and settings.confidence_threshold_for_detection is not None:
            return settings.confidence_threshold_for_detection
        return DEFAULT_CONFIDENCE_THRESHOLD
