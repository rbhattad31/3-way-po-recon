"""
JurisdictionResolverService — Deterministic-first jurisdiction resolution.

Resolves which TaxJurisdictionProfile applies to a given document by
analysing OCR text for tax ID patterns (GSTIN, TRN, VAT), currency
keywords, and address signals.  Uses a multi-signal scoring mechanism
that aggregates evidence across detectors.

Signal chain (in collection order):

    1. Built-in tax-ID patterns  (GSTIN → IN, TRN → AE, VAT → SA)
    2. DB-defined tax_id_regex per profile
    3. Currency symbol / code detection
    4. Keyword / address heuristics
    5. Explicit caller-provided hint
    6. (Future) LLM fallback

Scoring: signals are grouped by country_code and aggregated using
diminishing-returns addition so multiple weak signals can outweigh a
single moderate one, but the aggregate is capped at 0.99.

The service is STATELESS — every public method is a classmethod.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from apps.extraction_core.models import TaxJurisdictionProfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class JurisdictionSignal:
    """A single signal contributing to jurisdiction resolution."""
    method: str          # e.g. GSTIN_PATTERN, TRN_PATTERN, CURRENCY, KEYWORDS
    jurisdiction_id: int
    country_code: str
    tax_regime: str
    confidence: float    # 0.0–1.0
    evidence: str = ""   # snippet that triggered this signal


@dataclass
class JurisdictionResolution:
    """Result of jurisdiction resolution."""
    jurisdiction: Optional[TaxJurisdictionProfile] = None
    country_code: str = ""
    regime_code: str = ""
    confidence: float = 0.0
    method: str = ""
    signals: list[JurisdictionSignal] = field(default_factory=list)
    resolved: bool = False

    def to_dict(self) -> dict:
        return {
            "resolved": self.resolved,
            "jurisdiction_id": self.jurisdiction.pk if self.jurisdiction else None,
            "country_code": self.country_code,
            "regime_code": self.regime_code,
            "confidence": round(self.confidence, 4),
            "method": self.method,
            "signals": [
                {
                    "method": s.method,
                    "country_code": s.country_code,
                    "tax_regime": s.tax_regime,
                    "confidence": round(s.confidence, 4),
                    "evidence": s.evidence[:200],
                }
                for s in self.signals
            ],
        }


# ---------------------------------------------------------------------------
# Built-in tax-ID patterns  (work even without DB profiles)
# ---------------------------------------------------------------------------

# GSTIN: 2-digit state code + 10-char PAN + 1 entity code + Z + check digit
_GSTIN_PATTERN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z][A-Z\d]\b")

# UAE TRN: exactly 15 digits (often prefixed with "TRN" or "Tax Registration")
_TRN_PATTERN = re.compile(r"(?:TRN|Tax\s*Registration\s*(?:Number|No\.?))\s*:?\s*(\d{15})\b", re.IGNORECASE)
_TRN_BARE_PATTERN = re.compile(r"\b\d{15}\b")

# Saudi VAT: 15 digits starting with "3" (ZATCA format)
_SAUDI_VAT_PATTERN = re.compile(r"\b3\d{14}\b")

# Map: (pattern, country_code, tax_regime, method_label, confidence)
_BUILTIN_TAX_ID_RULES: list[tuple[re.Pattern, str, str, str, float]] = [
    (_GSTIN_PATTERN,      "IN", "GST", "GSTIN_PATTERN",    0.97),
    (_TRN_PATTERN,        "AE", "VAT", "TRN_PATTERN",      0.96),
    (_SAUDI_VAT_PATTERN,  "SA", "VAT", "SAUDI_VAT_PATTERN", 0.95),
]


# ---------------------------------------------------------------------------
# Built-in currency / keyword maps
# ---------------------------------------------------------------------------

_CURRENCY_MAP: dict[str, tuple[str, str]] = {
    # token → (country_code, regime)
    "INR":  ("IN", "GST"),
    "₹":    ("IN", "GST"),
    "AED":  ("AE", "VAT"),
    "SAR":  ("SA", "VAT"),
    "﷼":    ("SA", "VAT"),
}

_KEYWORD_RULES: dict[str, dict] = {
    "IN": {
        "regime": "GST",
        # tier-1: strong tax-system keywords (weight × 2)
        "strong": [
            "gstin", "cgst", "sgst", "igst", "utgst", "cess",
            "gst identification", "gst number", "gst no",
            "hsn code", "sac code", "reverse charge",
            "e-way bill",
        ],
        # tier-2: moderate geo / entity keywords
        "moderate": [
            "india", "gst", "pan number", "pan no",
            "maharashtra", "karnataka", "tamil nadu", "delhi",
            "gujarat", "andhra pradesh", "telangana", "kerala",
            "uttar pradesh", "rajasthan", "bengal", "bihar",
            "pincode", "pin code",
        ],
        # tier-3: weak hints
        "weak": [
            "pvt ltd", "private limited", "limited company",
            "indian rupee", "rupees",
        ],
    },
    "AE": {
        "regime": "VAT",
        "strong": [
            "trn", "tax registration number", "uae vat",
            "federal tax authority", "fta",
        ],
        "moderate": [
            "uae", "united arab emirates", "dubai", "abu dhabi",
            "sharjah", "ajman", "fujairah", "ras al khaimah",
            "umm al quwain", "emirates", "dirham",
        ],
        "weak": [
            "free zone", "fze", "fzc", "fz-llc",
        ],
    },
    "SA": {
        "regime": "VAT",
        "strong": [
            "zatca", "saudi vat", "vat registration",
            "vat id", "tax invoice", "الهيئة العامة للزكاة",
            "الرقم الضريبي",
        ],
        "moderate": [
            "saudi", "saudi arabia", "ksa", "kingdom of saudi",
            "riyadh", "jeddah", "dammam", "makkah", "madinah",
            "riyal", "halalas",
        ],
        "weak": [
            "cr number", "commercial registration",
        ],
    },
}


class JurisdictionResolverService:
    """Resolves TaxJurisdictionProfile from OCR text using a signal chain
    with fallback scoring that aggregates multiple signals per jurisdiction."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def resolve(
        cls,
        ocr_text: str,
        *,
        hint_country_code: str | None = None,
        hint_tax_regime: str | None = None,
    ) -> JurisdictionResolution:
        """
        Main entry point.  Analyses *ocr_text* and returns the best-matching
        jurisdiction profile with confidence and evidence.

        Args:
            ocr_text: Raw OCR text from the document.
            hint_country_code: Optional explicit country code from caller.
            hint_tax_regime: Optional explicit tax regime from caller.

        Returns:
            JurisdictionResolution with country_code, regime_code,
            confidence, and all collected signals.
        """
        if not ocr_text or not ocr_text.strip():
            logger.warning("Empty OCR text provided to JurisdictionResolverService")
            return JurisdictionResolution()

        profiles = cls._load_active_profiles()
        cc_map = {p.country_code.upper(): p for p in profiles}

        signals: list[JurisdictionSignal] = []

        # 1 — Built-in tax-ID patterns (GSTIN / TRN / Saudi VAT)
        signals.extend(cls._resolve_from_builtin_patterns(cc_map, ocr_text))

        # 2 — DB-defined tax_id_regex per profile
        signals.extend(cls._resolve_from_db_regex(profiles, ocr_text))

        # 3 — Currency detection
        signals.extend(cls._resolve_from_currency(cc_map, ocr_text))

        # 4 — Keyword / address heuristics (tiered)
        signals.extend(cls._resolve_from_keywords(cc_map, ocr_text))

        # 5 — Explicit hint
        if hint_country_code or hint_tax_regime:
            signals.extend(
                cls._resolve_from_hint(profiles, hint_country_code, hint_tax_regime)
            )

        if not signals:
            logger.info("No jurisdiction signals detected from OCR text")
            return JurisdictionResolution()

        # --- Aggregate scoring per country_code ---
        return cls._score_and_select(signals, cc_map)

    # ------------------------------------------------------------------
    # Signal resolvers
    # ------------------------------------------------------------------

    @classmethod
    def _resolve_from_builtin_patterns(
        cls,
        cc_map: dict[str, TaxJurisdictionProfile],
        ocr_text: str,
    ) -> list[JurisdictionSignal]:
        """Match hardcoded GSTIN / TRN / Saudi VAT patterns."""
        signals: list[JurisdictionSignal] = []

        for pattern, cc, regime, method, confidence in _BUILTIN_TAX_ID_RULES:
            matches = pattern.findall(ocr_text)
            if not matches:
                continue

            evidence = matches[0] if isinstance(matches[0], str) else str(matches[0])
            profile = cc_map.get(cc)
            jurisdiction_id = profile.pk if profile else 0

            signals.append(
                JurisdictionSignal(
                    method=method,
                    jurisdiction_id=jurisdiction_id,
                    country_code=cc,
                    tax_regime=regime,
                    confidence=confidence,
                    evidence=evidence[:200],
                )
            )

        # Special handling: bare 15-digit number could be UAE TRN if we
        # didn't already match via the labelled TRN pattern
        if not any(s.method == "TRN_PATTERN" for s in signals):
            bare_matches = _TRN_BARE_PATTERN.findall(ocr_text)
            # Only consider if there are UAE keyword signals or currency
            # (bare 15-digit numbers are ambiguous on their own)
            if bare_matches:
                profile = cc_map.get("AE")
                if profile:
                    signals.append(
                        JurisdictionSignal(
                            method="TRN_BARE_CANDIDATE",
                            jurisdiction_id=profile.pk,
                            country_code="AE",
                            tax_regime="VAT",
                            confidence=0.55,
                            evidence=bare_matches[0][:200],
                        )
                    )

        return signals

    @classmethod
    def _resolve_from_db_regex(
        cls,
        profiles: list[TaxJurisdictionProfile],
        ocr_text: str,
    ) -> list[JurisdictionSignal]:
        """Match per-profile tax_id_regex stored in the database."""
        signals: list[JurisdictionSignal] = []
        for p in profiles:
            if not p.tax_id_regex:
                continue
            try:
                matches = re.findall(p.tax_id_regex, ocr_text)
            except re.error:
                logger.warning(
                    "Invalid tax_id_regex for profile %s: %s", p, p.tax_id_regex
                )
                continue
            if matches:
                evidence = matches[0] if isinstance(matches[0], str) else str(matches[0])
                signals.append(
                    JurisdictionSignal(
                        method="TAX_ID_REGEX",
                        jurisdiction_id=p.pk,
                        country_code=p.country_code,
                        tax_regime=p.tax_regime,
                        confidence=0.96,
                        evidence=evidence[:200],
                    )
                )
        return signals

    @classmethod
    def _resolve_from_currency(
        cls,
        cc_map: dict[str, TaxJurisdictionProfile],
        ocr_text: str,
    ) -> list[JurisdictionSignal]:
        """Detect currency codes / symbols in OCR text."""
        signals: list[JurisdictionSignal] = []
        text_upper = ocr_text.upper()
        seen: set[str] = set()  # avoid duplicate signals per country

        for token, (cc, regime) in _CURRENCY_MAP.items():
            if cc in seen:
                continue
            if token.upper() in text_upper or token in ocr_text:
                profile = cc_map.get(cc)
                jurisdiction_id = profile.pk if profile else 0
                signals.append(
                    JurisdictionSignal(
                        method="CURRENCY_DETECTION",
                        jurisdiction_id=jurisdiction_id,
                        country_code=cc,
                        tax_regime=regime,
                        confidence=0.65,
                        evidence=f"Currency token '{token}' found",
                    )
                )
                seen.add(cc)

        return signals

    @classmethod
    def _resolve_from_keywords(
        cls,
        cc_map: dict[str, TaxJurisdictionProfile],
        ocr_text: str,
    ) -> list[JurisdictionSignal]:
        """Tiered keyword detection: strong / moderate / weak keywords
        with different weight contributions."""
        signals: list[JurisdictionSignal] = []
        text_lower = ocr_text.lower()

        for cc, rules in _KEYWORD_RULES.items():
            regime = rules["regime"]
            profile = cc_map.get(cc)
            jurisdiction_id = profile.pk if profile else 0

            strong_hits = [kw for kw in rules["strong"] if kw in text_lower]
            moderate_hits = [kw for kw in rules["moderate"] if kw in text_lower]
            weak_hits = [kw for kw in rules["weak"] if kw in text_lower]

            # Weighted score: strong=0.15, moderate=0.08, weak=0.03
            raw_score = (
                len(strong_hits) * 0.15
                + len(moderate_hits) * 0.08
                + len(weak_hits) * 0.03
            )

            if raw_score <= 0:
                continue

            # Confidence: base 0.20 + raw_score, capped at 0.80
            confidence = min(0.20 + raw_score, 0.80)

            all_hits = strong_hits + moderate_hits + weak_hits
            signals.append(
                JurisdictionSignal(
                    method="KEYWORD_DETECTION",
                    jurisdiction_id=jurisdiction_id,
                    country_code=cc,
                    tax_regime=regime,
                    confidence=round(confidence, 4),
                    evidence=f"Keywords ({len(all_hits)}): {', '.join(all_hits[:8])}",
                )
            )

        return signals

    @classmethod
    def _resolve_from_hint(
        cls,
        profiles: list[TaxJurisdictionProfile],
        country_code: str | None,
        tax_regime: str | None,
    ) -> list[JurisdictionSignal]:
        """Resolve from explicitly provided country/regime hints."""
        signals: list[JurisdictionSignal] = []
        for p in profiles:
            match = True
            if country_code and p.country_code.upper() != country_code.upper():
                match = False
            if tax_regime and p.tax_regime.upper() != tax_regime.upper():
                match = False
            if match and (country_code or tax_regime):
                signals.append(
                    JurisdictionSignal(
                        method="EXPLICIT_HINT",
                        jurisdiction_id=p.pk,
                        country_code=p.country_code,
                        tax_regime=p.tax_regime,
                        confidence=0.90,
                        evidence=f"hint_country={country_code}, hint_regime={tax_regime}",
                    )
                )
        return signals

    # ------------------------------------------------------------------
    # Fallback scoring — aggregates all signals per jurisdiction
    # ------------------------------------------------------------------

    @classmethod
    def _score_and_select(
        cls,
        signals: list[JurisdictionSignal],
        cc_map: dict[str, TaxJurisdictionProfile],
    ) -> JurisdictionResolution:
        """
        Aggregate signals per country_code using diminishing-returns addition:

            combined = 1 - ∏(1 - cᵢ)  for each signal cᵢ

        This ensures multiple moderate signals can beat a single moderate one,
        while the result is always capped below 1.0.  We then cap at 0.99.

        The winning jurisdiction is the one with the highest aggregate score.
        """
        # Group signals by country_code
        by_country: dict[str, list[JurisdictionSignal]] = defaultdict(list)
        for s in signals:
            by_country[s.country_code].append(s)

        best_cc = ""
        best_score = 0.0
        best_method = ""
        scores: dict[str, float] = {}

        for cc, cc_signals in by_country.items():
            # Sort by confidence descending for deterministic method label
            cc_signals.sort(key=lambda s: s.confidence, reverse=True)

            # Diminishing-returns aggregation
            complement = 1.0
            for s in cc_signals:
                complement *= (1.0 - s.confidence)
            aggregate = min(1.0 - complement, 0.99)
            scores[cc] = round(aggregate, 4)

            if aggregate > best_score:
                best_score = aggregate
                best_cc = cc
                # Primary method is the highest-confidence signal's method
                best_method = cc_signals[0].method

        logger.info(
            "Jurisdiction scoring: %s -> winner=%s (%.4f)",
            scores, best_cc, best_score,
        )

        # Resolve to a profile (may be None if no DB profiles exist)
        profile = cc_map.get(best_cc)
        regime = ""
        if profile:
            regime = profile.tax_regime
        elif best_cc and by_country.get(best_cc):
            regime = by_country[best_cc][0].tax_regime

        return JurisdictionResolution(
            jurisdiction=profile,
            country_code=best_cc,
            regime_code=regime,
            confidence=round(best_score, 4),
            method=best_method,
            signals=signals,
            resolved=True,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @classmethod
    def _load_active_profiles(cls) -> list[TaxJurisdictionProfile]:
        return list(TaxJurisdictionProfile.objects.filter(is_active=True))
