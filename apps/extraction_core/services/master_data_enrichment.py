"""
MasterDataEnrichmentService — Post-extraction master data matching.

Runs **after** extraction + normalization to enrich the extraction result
with matched master data references:

    - Vendor matching (exact tax ID → alias → fuzzy name)
    - Customer/buyer matching
    - PO lookup (from extracted PO references)
    - Contract reference resolution

Adjusts field confidence based on match quality.  All matching is
jurisdiction-aware and country-agnostic — no hardcoded country logic.

Design:
    - Clean separation from extraction logic
    - No direct DB updates — returns enrichment result
    - Service-layer pattern (stateless classmethods)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


@dataclass
class MasterDataMatch:
    """A single master data match result."""

    match_type: str  # EXACT_TAX_ID | ALIAS | FUZZY | NOT_FOUND
    entity_id: int | None = None
    entity_code: str = ""
    entity_name: str = ""
    matched_value: str = ""   # what was matched (alias text, etc.)
    similarity: float = 0.0   # 0.0–1.0
    confidence: float = 0.0   # match confidence

    def to_dict(self) -> dict:
        return {
            "match_type": self.match_type,
            "entity_id": self.entity_id,
            "entity_code": self.entity_code,
            "entity_name": self.entity_name,
            "matched_value": self.matched_value,
            "similarity": round(self.similarity, 4),
            "confidence": round(self.confidence, 4),
        }


@dataclass
class POLookupResult:
    """PO lookup result."""

    found: bool = False
    po_id: int | None = None
    po_number: str = ""
    vendor_id: int | None = None
    vendor_name: str = ""
    po_status: str = ""
    total_amount: float | None = None
    currency: str = ""
    confidence: float = 0.0

    def to_dict(self) -> dict:
        d: dict = {
            "found": self.found,
            "po_number": self.po_number,
            "confidence": round(self.confidence, 4),
        }
        if self.found:
            d["po_id"] = self.po_id
            d["vendor_id"] = self.vendor_id
            d["vendor_name"] = self.vendor_name
            d["po_status"] = self.po_status
            d["total_amount"] = self.total_amount
            d["currency"] = self.currency
        return d


@dataclass
class EnrichmentResult:
    """
    Complete master data enrichment output.

    Attached to ExtractionResult after normalization + validation.
    """

    vendor_match: MasterDataMatch = field(
        default_factory=lambda: MasterDataMatch(match_type="NOT_FOUND"),
    )
    customer_match: MasterDataMatch = field(
        default_factory=lambda: MasterDataMatch(match_type="NOT_FOUND"),
    )
    po_lookup: POLookupResult = field(default_factory=POLookupResult)
    #: True when the extracted vendor was detected as the user's own company
    self_company_detected: bool = False
    #: Original vendor name before self-company swap (empty if no swap)
    original_vendor_name: str = ""
    #: Confidence adjustments applied (field_key → delta)
    confidence_adjustments: dict[str, float] = field(default_factory=dict)
    #: Enrichment warnings
    warnings: list[str] = field(default_factory=list)
    #: Duration in milliseconds
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return {
            "vendor_match": self.vendor_match.to_dict(),
            "customer_match": self.customer_match.to_dict(),
            "po_lookup": self.po_lookup.to_dict(),
            "confidence_adjustments": {
                k: round(v, 4)
                for k, v in self.confidence_adjustments.items()
            },
            "warnings": self.warnings,
            "duration_ms": self.duration_ms,
        }

    @property
    def vendor_id(self) -> int | None:
        return self.vendor_match.entity_id

    @property
    def customer_id(self) -> int | None:
        return self.customer_match.entity_id

    @property
    def match_confidence(self) -> float:
        """Overall match confidence (average of non-zero matches)."""
        scores = [
            m.confidence
            for m in [self.vendor_match, self.customer_match]
            if m.confidence > 0
        ]
        if self.po_lookup.found:
            scores.append(self.po_lookup.confidence)
        return sum(scores) / len(scores) if scores else 0.0


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class MasterDataEnrichmentService:
    """
    Post-extraction master data matching service.

    Runs after extraction + normalization to match extracted entities
    against the vendor/customer/PO master data.

    Integrates with the jurisdiction-aware pipeline — uses resolved
    ``country_code`` to scope lookups when appropriate.
    """

    # Fuzzy match thresholds
    FUZZY_THRESHOLD = 0.70       # minimum similarity for fuzzy match
    FUZZY_HIGH_THRESHOLD = 0.85  # high-confidence fuzzy match
    # Confidence adjustments
    VENDOR_MATCH_BOOST = 0.05    # boost when vendor matches
    VENDOR_MISMATCH_PENALTY = -0.08  # penalty when vendor found but mismatched
    PO_MATCH_BOOST = 0.05        # boost when PO matches
    PO_VENDOR_MATCH_BOOST = 0.03  # extra boost when PO vendor matches invoice vendor

    @classmethod
    def enrich(
        cls,
        *,
        extraction_result: Any,
        country_code: str = "",
        regime_code: str = "",
    ) -> EnrichmentResult:
        """
        Enrich extraction result with master data matches.

        Args:
            extraction_result: The ExtractionResult from the pipeline.
            country_code:      Resolved jurisdiction country code.
            regime_code:       Resolved jurisdiction regime code.

        Returns:
            EnrichmentResult with vendor/customer/PO matches.
        """
        from django.utils import timezone

        start = timezone.now()
        result = EnrichmentResult()

        # Extract inputs from the extraction result
        intel = getattr(extraction_result, "document_intelligence", None)
        supplier_name = ""
        supplier_tax_id = ""
        buyer_name = ""
        po_number = ""

        if intel:
            supplier_name = getattr(intel, "supplier_name", "") or ""
            buyer_name = getattr(intel, "buyer_name", "") or ""
            po_number = getattr(intel, "primary_po_number", "") or ""

            # Get supplier tax ID from party info
            parties = getattr(intel, "parties", None)
            if parties:
                p = getattr(parties, "primary_supplier", None)
                if p:
                    supplier_tax_id = getattr(p, "tax_id", "") or ""

        # Also check header fields for fallbacks
        header_fields = getattr(extraction_result, "header_fields", {})
        if not supplier_name:
            vendor_field = header_fields.get("vendor_name") or header_fields.get("supplier_name")
            if vendor_field and getattr(vendor_field, "extracted", False):
                supplier_name = (
                    getattr(vendor_field, "normalized_value", "")
                    or getattr(vendor_field, "raw_value", "")
                )
        if not supplier_tax_id:
            tax_id_field = header_fields.get("supplier_gstin") or header_fields.get("supplier_tax_id")
            if tax_id_field and getattr(tax_id_field, "extracted", False):
                supplier_tax_id = (
                    getattr(tax_id_field, "normalized_value", "")
                    or getattr(tax_id_field, "raw_value", "")
                )
        if not buyer_name:
            buyer_field = header_fields.get("buyer_name") or header_fields.get("customer_name")
            if buyer_field and getattr(buyer_field, "extracted", False):
                buyer_name = (
                    getattr(buyer_field, "normalized_value", "")
                    or getattr(buyer_field, "raw_value", "")
                )
        if not po_number:
            po_field = header_fields.get("po_number")
            if po_field and getattr(po_field, "extracted", False):
                po_number = (
                    getattr(po_field, "normalized_value", "")
                    or getattr(po_field, "raw_value", "")
                )

        # ── 0. Self-company detection ─────────────────────────────────
        # If the LLM accidentally picked OUR company (the buyer) as the
        # vendor, detect and swap vendor <-> buyer.
        try:
            swapped = cls._detect_and_swap_self_company(
                supplier_name, supplier_tax_id, buyer_name,
            )
            if swapped:
                result.self_company_detected = True
                result.original_vendor_name = supplier_name
                result.warnings.append(
                    f"Self-company detected as vendor ('{supplier_name}'). "
                    f"Swapped with buyer ('{buyer_name}')."
                )
                logger.warning(
                    "Self-company swap: vendor='%s' -> '%s'",
                    supplier_name, buyer_name,
                )
                supplier_name, buyer_name = swapped
        except Exception:
            logger.exception("Self-company detection failed")

        # ── 1. Vendor matching ────────────────────────────────────────
        try:
            result.vendor_match = cls._match_vendor(
                supplier_name, supplier_tax_id, country_code,
            )
            if result.vendor_match.match_type != "NOT_FOUND":
                logger.info(
                    "Vendor matched: %s → %s (%s, conf=%.2f)",
                    supplier_name,
                    result.vendor_match.entity_name,
                    result.vendor_match.match_type,
                    result.vendor_match.confidence,
                )
        except Exception:
            logger.exception("Vendor matching failed")
            result.warnings.append("Vendor matching failed")

        # ── 2. Customer matching ──────────────────────────────────────
        try:
            result.customer_match = cls._match_customer(buyer_name)
            if result.customer_match.match_type != "NOT_FOUND":
                logger.info(
                    "Customer matched: %s → %s (%s)",
                    buyer_name,
                    result.customer_match.entity_name,
                    result.customer_match.match_type,
                )
        except Exception:
            logger.exception("Customer matching failed")
            result.warnings.append("Customer matching failed")

        # ── 3. PO lookup ─────────────────────────────────────────────
        try:
            result.po_lookup = cls._lookup_po(po_number)
            if result.po_lookup.found:
                logger.info(
                    "PO found: %s (vendor=%s, status=%s)",
                    po_number,
                    result.po_lookup.vendor_name,
                    result.po_lookup.po_status,
                )
        except Exception:
            logger.exception("PO lookup failed")
            result.warnings.append("PO lookup failed")

        # ── 4. Confidence adjustments ─────────────────────────────────
        cls._apply_confidence_adjustments(extraction_result, result)

        elapsed = (timezone.now() - start).total_seconds() * 1000
        result.duration_ms = int(elapsed)

        return result

    # ------------------------------------------------------------------
    # Vendor matching
    # ------------------------------------------------------------------

    @classmethod
    def _match_vendor(
        cls,
        supplier_name: str,
        supplier_tax_id: str,
        country_code: str,
    ) -> MasterDataMatch:
        """
        Match supplier against vendor master using 3-tier strategy:
        1. Exact tax ID match
        2. Alias match (exact normalized)
        3. Fuzzy name match
        """
        from apps.vendors.models import Vendor
        from apps.posting_core.models import VendorAliasMapping

        if not supplier_name and not supplier_tax_id:
            return MasterDataMatch(match_type="NOT_FOUND")

        # ── Tier 1: Exact tax ID ──
        if supplier_tax_id:
            tax_id_clean = supplier_tax_id.strip().upper()
            vendor = (
                Vendor.objects.filter(
                    tax_id__iexact=tax_id_clean,
                    is_active=True,
                )
                .first()
            )
            if vendor:
                return MasterDataMatch(
                    match_type="EXACT_TAX_ID",
                    entity_id=vendor.pk,
                    entity_code=vendor.code,
                    entity_name=vendor.name,
                    matched_value=tax_id_clean,
                    similarity=1.0,
                    confidence=0.98,
                )

        if not supplier_name:
            return MasterDataMatch(match_type="NOT_FOUND")

        normalized_input = cls._normalize_name(supplier_name)

        # ── Tier 2: Alias match ──
        alias = (
            VendorAliasMapping.objects.select_related("vendor")
            .filter(
                normalized_alias=normalized_input,
                vendor__is_active=True,
                is_active=True,
            )
            .first()
        )
        if alias:
            return MasterDataMatch(
                match_type="ALIAS",
                entity_id=alias.vendor.pk,
                entity_code=alias.vendor.code,
                entity_name=alias.vendor.name,
                matched_value=alias.alias_text,
                similarity=1.0,
                confidence=0.95,
            )

        # ── Tier 3: Fuzzy name match ──
        # Scope vendors by country if provided
        vendor_qs = Vendor.objects.filter(is_active=True)
        if country_code:
            # Try country-scoped first, fall back to all
            country_vendors = vendor_qs.filter(
                country__iexact=country_code,
            )
            if country_vendors.exists():
                vendor_qs = country_vendors

        # Limit candidates to prevent performance issues
        candidates = vendor_qs.values_list(
            "pk", "code", "name", "normalized_name",
        )[:500]

        best_match = None
        best_sim = 0.0

        for pk, code, name, norm_name in candidates:
            # Compare against normalized name
            target = norm_name or cls._normalize_name(name)
            sim = SequenceMatcher(
                None, normalized_input, target,
            ).ratio()

            if sim > best_sim:
                best_sim = sim
                best_match = (pk, code, name)

        if best_match and best_sim >= cls.FUZZY_THRESHOLD:
            pk, code, name = best_match
            confidence = (
                0.90 if best_sim >= cls.FUZZY_HIGH_THRESHOLD else
                0.70 + (best_sim - cls.FUZZY_THRESHOLD) * 1.3
            )
            return MasterDataMatch(
                match_type="FUZZY",
                entity_id=pk,
                entity_code=code,
                entity_name=name,
                matched_value=supplier_name,
                similarity=best_sim,
                confidence=min(confidence, 0.92),
            )

        return MasterDataMatch(match_type="NOT_FOUND")

    # ------------------------------------------------------------------
    # Customer matching
    # ------------------------------------------------------------------

    @classmethod
    def _match_customer(cls, buyer_name: str) -> MasterDataMatch:
        """
        Match buyer entity.

        Uses vendor master as the buyer could also be a known entity.
        Falls back to fuzzy matching on PO buyer_name fields.
        """
        from apps.documents.models import PurchaseOrder
        from apps.vendors.models import Vendor
        from apps.posting_core.models import VendorAliasMapping

        if not buyer_name:
            return MasterDataMatch(match_type="NOT_FOUND")

        normalized = cls._normalize_name(buyer_name)

        # Check vendor master first (buyer might be a known vendor)
        alias = (
            VendorAliasMapping.objects.select_related("vendor")
            .filter(
                normalized_alias=normalized,
                vendor__is_active=True,
                is_active=True,
            )
            .first()
        )
        if alias:
            return MasterDataMatch(
                match_type="ALIAS",
                entity_id=alias.vendor.pk,
                entity_code=alias.vendor.code,
                entity_name=alias.vendor.name,
                matched_value=alias.alias_text,
                similarity=1.0,
                confidence=0.90,
            )

        # Check PO buyer_name for known buyer entities
        buyer_names = (
            PurchaseOrder.objects.exclude(buyer_name="")
            .values_list("buyer_name", flat=True)
            .distinct()[:200]
        )

        best_name = ""
        best_sim = 0.0
        for bn in buyer_names:
            sim = SequenceMatcher(
                None, normalized, cls._normalize_name(bn),
            ).ratio()
            if sim > best_sim:
                best_sim = sim
                best_name = bn

        if best_name and best_sim >= cls.FUZZY_THRESHOLD:
            return MasterDataMatch(
                match_type="FUZZY",
                entity_id=None,
                entity_code="",
                entity_name=best_name,
                matched_value=buyer_name,
                similarity=best_sim,
                confidence=0.65 + best_sim * 0.2,
            )

        return MasterDataMatch(match_type="NOT_FOUND")

    # ------------------------------------------------------------------
    # PO lookup
    # ------------------------------------------------------------------

    @classmethod
    def _lookup_po(cls, po_number: str) -> POLookupResult:
        """
        Look up PO by number (exact then normalized).
        """
        from django.db.models import Sum

        from apps.documents.models import PurchaseOrder
        from apps.core.enums import ERPReferenceBatchStatus, ERPReferenceBatchType
        from apps.posting_core.models import ERPPOReference, ERPReferenceImportBatch

        if not po_number:
            return POLookupResult()

        po_clean = po_number.strip()

        # Exact match
        po = PurchaseOrder.objects.filter(po_number__iexact=po_clean).first()

        # Try normalized match
        if not po:
            normalized = cls._normalize_po_number(po_clean)
            po = PurchaseOrder.objects.filter(
                normalized_po_number__iexact=normalized,
            ).first()

        if not po:
            # Fallback to imported ERP open-PO snapshot if transactional PO is unavailable.
            batch = (
                ERPReferenceImportBatch.objects
                .filter(
                    batch_type=ERPReferenceBatchType.OPEN_PO,
                    status=ERPReferenceBatchStatus.COMPLETED,
                )
                .order_by("-imported_at")
                .first()
            )
            if not batch:
                return POLookupResult(po_number=po_clean)

            ref_qs = ERPPOReference.objects.filter(
                batch=batch,
                is_open=True,
                po_number__iexact=po_clean,
            )
            ref = ref_qs.order_by("po_line_number").first()
            if not ref:
                return POLookupResult(po_number=po_clean)

            agg = ref_qs.aggregate(total=Sum("line_amount"))
            total_amount = agg.get("total")

            return POLookupResult(
                found=True,
                po_id=None,
                po_number=ref.po_number,
                vendor_id=None,
                vendor_name=ref.vendor_code or "",
                po_status=ref.status or "OPEN",
                total_amount=float(total_amount) if total_amount is not None else None,
                currency=ref.currency or "",
                confidence=0.75,
            )

        vendor_name = ""
        vendor_id = None
        if po.vendor:
            vendor_name = po.vendor.name
            vendor_id = po.vendor.pk

        return POLookupResult(
            found=True,
            po_id=po.pk,
            po_number=po.po_number,
            vendor_id=vendor_id,
            vendor_name=vendor_name,
            po_status=po.status,
            total_amount=float(po.total_amount) if po.total_amount else None,
            currency=po.currency,
            confidence=0.95,
        )

    # ------------------------------------------------------------------
    # Confidence adjustments
    # ------------------------------------------------------------------

    @classmethod
    def _apply_confidence_adjustments(
        cls,
        extraction_result: Any,
        enrichment: EnrichmentResult,
    ) -> None:
        """
        Adjust field confidence based on master data match quality.

        Boosts confidence when matched, penalizes when mismatched.
        """
        header_fields = getattr(extraction_result, "header_fields", {})

        # Vendor match → adjust vendor_name field
        vendor_field_key = None
        for key in ("vendor_name", "supplier_name"):
            if key in header_fields:
                vendor_field_key = key
                break

        if vendor_field_key and vendor_field_key in header_fields:
            fr = header_fields[vendor_field_key]
            if enrichment.vendor_match.match_type != "NOT_FOUND":
                delta = cls.VENDOR_MATCH_BOOST
                fr.confidence = min(fr.confidence + delta, 1.0)
                enrichment.confidence_adjustments[vendor_field_key] = delta
            elif fr.extracted:
                delta = cls.VENDOR_MISMATCH_PENALTY
                fr.confidence = max(fr.confidence + delta, 0.0)
                enrichment.confidence_adjustments[vendor_field_key] = delta
                enrichment.warnings.append(
                    f"Vendor '{fr.raw_value}' not found in master data"
                )

        # PO match → adjust po_number field
        if "po_number" in header_fields:
            po_fr = header_fields["po_number"]
            if enrichment.po_lookup.found:
                delta = cls.PO_MATCH_BOOST
                po_fr.confidence = min(po_fr.confidence + delta, 1.0)
                enrichment.confidence_adjustments["po_number"] = delta

                # Cross-validate: PO vendor matches extraction vendor?
                if (
                    enrichment.vendor_match.entity_id
                    and enrichment.po_lookup.vendor_id
                    and enrichment.vendor_match.entity_id
                    == enrichment.po_lookup.vendor_id
                ):
                    # Extra boost for cross-validated match
                    if vendor_field_key and vendor_field_key in header_fields:
                        vfr = header_fields[vendor_field_key]
                        extra = cls.PO_VENDOR_MATCH_BOOST
                        vfr.confidence = min(vfr.confidence + extra, 1.0)
                        prev = enrichment.confidence_adjustments.get(
                            vendor_field_key, 0,
                        )
                        enrichment.confidence_adjustments[vendor_field_key] = (
                            prev + extra
                        )
                elif (
                    enrichment.vendor_match.entity_id
                    and enrichment.po_lookup.vendor_id
                    and enrichment.vendor_match.entity_id
                    != enrichment.po_lookup.vendor_id
                ):
                    enrichment.warnings.append(
                        f"PO vendor mismatch: extracted vendor "
                        f"({enrichment.vendor_match.entity_name}) differs "
                        f"from PO vendor ({enrichment.po_lookup.vendor_name})"
                    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Self-company detection
    # ------------------------------------------------------------------

    @classmethod
    def _detect_and_swap_self_company(
        cls,
        supplier_name: str,
        supplier_tax_id: str,
        buyer_name: str,
    ) -> tuple[str, str] | None:
        """Check if the extracted vendor is actually the user's own company.

        Returns ``(new_supplier_name, new_buyer_name)`` if a swap is
        needed, or ``None`` if no match.  The caller should replace
        ``supplier_name`` and ``buyer_name`` with the returned values
        before proceeding to vendor matching.
        """
        from apps.accounts.models import CompanyProfile, CompanyAlias, CompanyTaxID

        profiles = CompanyProfile.objects.filter(is_active=True)
        if not profiles.exists():
            return None  # No company profiles configured -- skip

        # Build lookup sets once
        all_names: set[str] = set()
        all_tax_ids: set[str] = set()

        for profile in profiles:
            if profile.name:
                all_names.add(cls._normalize_name(profile.name))
            if profile.legal_name:
                all_names.add(cls._normalize_name(profile.legal_name))
            if profile.tax_id:
                all_tax_ids.add(profile.tax_id.strip().upper())

        # Add all aliases
        for alias in CompanyAlias.objects.filter(company__is_active=True):
            if alias.normalized_alias:
                all_names.add(alias.normalized_alias)

        # Add all additional tax IDs
        for tid in CompanyTaxID.objects.filter(company__is_active=True):
            if tid.tax_id:
                all_tax_ids.add(tid.tax_id.strip().upper())

        # Check if extracted vendor matches our company
        is_self = False

        # 1. Tax ID match (strongest signal)
        if supplier_tax_id:
            if supplier_tax_id.strip().upper() in all_tax_ids:
                is_self = True
                logger.info(
                    "Self-company detected via tax ID: %s", supplier_tax_id,
                )

        # 2. Name match
        if not is_self and supplier_name:
            norm_supplier = cls._normalize_name(supplier_name)
            if norm_supplier and norm_supplier in all_names:
                is_self = True
                logger.info(
                    "Self-company detected via name: '%s'", supplier_name,
                )

        if not is_self:
            return None

        # Swap: the real vendor is in the buyer field
        if buyer_name:
            return buyer_name, supplier_name
        else:
            # No buyer_name to swap with -- just flag it
            logger.warning(
                "Self-company detected but no buyer_name to swap with. "
                "Vendor '%s' may be incorrectly identified.",
                supplier_name,
            )
            return None

    @classmethod
    def _normalize_name(cls, name: str) -> str:
        """Normalize a name for matching: lowercase, strip, collapse whitespace."""
        if not name:
            return ""
        name = name.lower().strip()
        # Remove common suffixes
        for suffix in (
            " pvt ltd", " pvt. ltd.", " private limited",
            " limited", " ltd", " ltd.", " llc", " inc",
            " inc.", " corp", " corp.", " co.", " company",
            " gmbh", " ag", " sa", " sarl", " srl",
            " llp", " plc",
        ):
            if name.endswith(suffix):
                name = name[: -len(suffix)].strip()
        # Collapse whitespace
        name = re.sub(r"\s+", " ", name)
        # Remove punctuation except alphanumeric and space
        name = re.sub(r"[^\w\s]", "", name)
        return name.strip()

    @classmethod
    def _normalize_po_number(cls, po_number: str) -> str:
        """Normalize PO number for matching."""
        if not po_number:
            return ""
        # Uppercase, strip leading zeros after prefix
        normalized = po_number.upper().strip()
        # Remove common separators
        normalized = re.sub(r"[\s\-/]+", "", normalized)
        return normalized
