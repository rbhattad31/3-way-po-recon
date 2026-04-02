"""
Tests for MasterDataEnrichmentService -- post-extraction master data matching.

Covers:
- Import correctness (regression: VendorAlias was never in apps.vendors.models)
- _normalize_name helper
- _normalize_po_number helper
- _match_vendor (3-tier: exact tax ID, alias, fuzzy)
- _match_customer (alias + PO buyer fuzzy)
- _lookup_po (exact + normalized)
- _apply_confidence_adjustments
- enrich() top-level orchestration
"""
from __future__ import annotations

import pytest

from apps.extraction_core.services.master_data_enrichment import (
    EnrichmentResult,
    MasterDataEnrichmentService,
    MasterDataMatch,
    POLookupResult,
)

Svc = MasterDataEnrichmentService


# ---------------------------------------------------------------------------
# Helpers -- _normalize_name
# ---------------------------------------------------------------------------


class TestNormalizeName:
    def test_lowercase_and_strip(self):
        assert Svc._normalize_name("  Acme Industries  ") == "acme industries"

    def test_collapse_whitespace(self):
        assert Svc._normalize_name("Acme   Industries") == "acme industries"

    def test_removes_pvt_ltd_suffix(self):
        result = Svc._normalize_name("Tata Motors Pvt Ltd")
        assert "pvt" not in result
        assert "ltd" not in result

    def test_removes_private_limited(self):
        result = Svc._normalize_name("Reliance Private Limited")
        assert "private" not in result
        assert "limited" not in result

    def test_removes_llc_suffix(self):
        result = Svc._normalize_name("Google LLC")
        assert "llc" not in result

    def test_removes_gmbh_suffix(self):
        result = Svc._normalize_name("Siemens GmbH")
        assert "gmbh" not in result

    def test_removes_punctuation(self):
        result = Svc._normalize_name("Al-Safi (Danone)")
        assert "(" not in result
        assert ")" not in result

    def test_empty_returns_empty(self):
        assert Svc._normalize_name("") == ""

    def test_none_returns_empty(self):
        assert Svc._normalize_name(None) == ""


# ---------------------------------------------------------------------------
# Helpers -- _normalize_po_number
# ---------------------------------------------------------------------------


class TestNormalizePONumber:
    def test_uppercase_and_strip(self):
        assert Svc._normalize_po_number("  po-123  ") == "PO123"

    def test_removes_separators(self):
        assert Svc._normalize_po_number("PO-2024/001") == "PO2024001"

    def test_removes_spaces(self):
        assert Svc._normalize_po_number("PO 100 200") == "PO100200"

    def test_empty_returns_empty(self):
        assert Svc._normalize_po_number("") == ""


# ---------------------------------------------------------------------------
# _match_vendor -- Tier 1: Exact tax ID
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMatchVendorTaxId:
    def test_exact_tax_id_match(self):
        from apps.vendors.models import Vendor
        v = Vendor.objects.create(
            code="V001", name="Acme Corp", tax_id="GSTIN123456",
        )
        result = Svc._match_vendor("", "GSTIN123456", "")
        assert result.match_type == "EXACT_TAX_ID"
        assert result.entity_id == v.pk
        assert result.confidence == 0.98

    def test_tax_id_case_insensitive(self):
        from apps.vendors.models import Vendor
        Vendor.objects.create(
            code="V002", name="Beta Inc", tax_id="ABC999",
        )
        result = Svc._match_vendor("", "abc999", "")
        assert result.match_type == "EXACT_TAX_ID"

    def test_tax_id_strips_whitespace(self):
        from apps.vendors.models import Vendor
        Vendor.objects.create(
            code="V003", name="Gamma LLC", tax_id="TAX001",
        )
        result = Svc._match_vendor("", "  TAX001  ", "")
        assert result.match_type == "EXACT_TAX_ID"

    def test_inactive_vendor_not_matched(self):
        from apps.vendors.models import Vendor
        Vendor.objects.create(
            code="V004", name="Dead Corp", tax_id="DEAD01", is_active=False,
        )
        result = Svc._match_vendor("", "DEAD01", "")
        assert result.match_type == "NOT_FOUND"


# ---------------------------------------------------------------------------
# _match_vendor -- Tier 2: Alias
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMatchVendorAlias:
    def test_alias_match(self):
        from apps.vendors.models import Vendor
        from apps.posting_core.models import VendorAliasMapping

        v = Vendor.objects.create(code="V010", name="Acme Corporation")
        normalized = Svc._normalize_name("acme corp")
        VendorAliasMapping.objects.create(
            alias_text="Acme Corp",
            normalized_alias=normalized,
            vendor=v,
            is_active=True,
        )
        result = Svc._match_vendor("Acme Corp", "", "")
        assert result.match_type == "ALIAS"
        assert result.entity_id == v.pk
        assert result.confidence == 0.95
        assert result.matched_value == "Acme Corp"

    def test_inactive_alias_not_matched(self):
        from apps.vendors.models import Vendor
        from apps.posting_core.models import VendorAliasMapping

        v = Vendor.objects.create(code="V011", name="Beta Corp")
        normalized = Svc._normalize_name("beta trading")
        VendorAliasMapping.objects.create(
            alias_text="Beta Trading",
            normalized_alias=normalized,
            vendor=v,
            is_active=False,
        )
        result = Svc._match_vendor("Beta Trading", "", "")
        assert result.match_type != "ALIAS"

    def test_alias_inactive_vendor_not_matched(self):
        from apps.vendors.models import Vendor
        from apps.posting_core.models import VendorAliasMapping

        v = Vendor.objects.create(
            code="V012", name="Closed Corp", is_active=False,
        )
        normalized = Svc._normalize_name("closed corp")
        VendorAliasMapping.objects.create(
            alias_text="Closed Corp",
            normalized_alias=normalized,
            vendor=v,
            is_active=True,
        )
        result = Svc._match_vendor("Closed Corp", "", "")
        assert result.match_type != "ALIAS"


# ---------------------------------------------------------------------------
# _match_vendor -- Tier 3: Fuzzy
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMatchVendorFuzzy:
    def test_fuzzy_match_above_threshold(self):
        from apps.vendors.models import Vendor
        Vendor.objects.create(
            code="V020",
            name="Tata Steel Industries",
            normalized_name=Svc._normalize_name("Tata Steel Industries"),
        )
        # Slightly different input
        result = Svc._match_vendor("Tata Steel Indurstries", "", "")
        assert result.match_type == "FUZZY"
        assert result.similarity >= Svc.FUZZY_THRESHOLD
        assert result.confidence > 0

    def test_fuzzy_no_match_below_threshold(self):
        from apps.vendors.models import Vendor
        Vendor.objects.create(
            code="V021",
            name="Completely Different Name",
            normalized_name=Svc._normalize_name("Completely Different Name"),
        )
        result = Svc._match_vendor("XYZZY Unrelated Corp", "", "")
        assert result.match_type == "NOT_FOUND"

    def test_fuzzy_scoped_by_country(self):
        from apps.vendors.models import Vendor
        Vendor.objects.create(
            code="V030",
            name="Acme India",
            normalized_name=Svc._normalize_name("Acme India"),
            country="IN",
        )
        Vendor.objects.create(
            code="V031",
            name="Acme US",
            normalized_name=Svc._normalize_name("Acme US"),
            country="US",
        )
        # Search India-scoped should prefer the IN vendor
        result = Svc._match_vendor("Acme India", "", "IN")
        assert result.match_type == "FUZZY"
        assert result.entity_code == "V030"

    def test_empty_inputs_returns_not_found(self):
        result = Svc._match_vendor("", "", "")
        assert result.match_type == "NOT_FOUND"


# ---------------------------------------------------------------------------
# _match_vendor -- Tier priority: tax ID > alias > fuzzy
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMatchVendorPriority:
    def test_tax_id_wins_over_alias_and_fuzzy(self):
        from apps.vendors.models import Vendor
        from apps.posting_core.models import VendorAliasMapping

        v_tax = Vendor.objects.create(
            code="VP01", name="Tax Match Vendor", tax_id="TAX777",
        )
        v_alias = Vendor.objects.create(
            code="VP02", name="Alias Vendor",
        )
        VendorAliasMapping.objects.create(
            alias_text="Some Supplier",
            normalized_alias=Svc._normalize_name("Some Supplier"),
            vendor=v_alias,
            is_active=True,
        )
        # Providing tax_id should match via tier 1, ignoring alias
        result = Svc._match_vendor("Some Supplier", "TAX777", "")
        assert result.match_type == "EXACT_TAX_ID"
        assert result.entity_id == v_tax.pk


# ---------------------------------------------------------------------------
# _match_customer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMatchCustomer:
    def test_customer_alias_match(self):
        from apps.vendors.models import Vendor
        from apps.posting_core.models import VendorAliasMapping

        v = Vendor.objects.create(code="C001", name="McDonalds KSA")
        normalized = Svc._normalize_name("MCD KSA")
        VendorAliasMapping.objects.create(
            alias_text="MCD KSA",
            normalized_alias=normalized,
            vendor=v,
            is_active=True,
        )
        result = Svc._match_customer("MCD KSA")
        assert result.match_type == "ALIAS"
        assert result.entity_id == v.pk
        assert result.confidence == 0.90

    def test_customer_po_buyer_fuzzy_match(self):
        from apps.documents.models import PurchaseOrder
        PurchaseOrder.objects.create(
            po_number="PO-CUST-001",
            buyer_name="Alpha Retail Holdings",
        )
        result = Svc._match_customer("Alpha Retail Holding")
        assert result.match_type == "FUZZY"
        assert result.similarity >= Svc.FUZZY_THRESHOLD

    def test_customer_empty_returns_not_found(self):
        result = Svc._match_customer("")
        assert result.match_type == "NOT_FOUND"


# ---------------------------------------------------------------------------
# _lookup_po
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLookupPO:
    def test_exact_po_match(self):
        from apps.documents.models import PurchaseOrder
        from apps.vendors.models import Vendor

        v = Vendor.objects.create(code="VP50", name="PO Vendor")
        po = PurchaseOrder.objects.create(
            po_number="PO-2024-001",
            vendor=v,
            status="OPEN",
            total_amount=10000,
            currency="USD",
        )
        result = Svc._lookup_po("PO-2024-001")
        assert result.found is True
        assert result.po_id == po.pk
        assert result.vendor_name == "PO Vendor"
        assert result.po_status == "OPEN"
        assert result.confidence == 0.95

    def test_normalized_po_match(self):
        from apps.documents.models import PurchaseOrder

        PurchaseOrder.objects.create(
            po_number="PO-2024-002",
            normalized_po_number="PO2024002",
        )
        result = Svc._lookup_po("PO/2024/002")
        assert result.found is True

    def test_po_not_found(self):
        result = Svc._lookup_po("NONEXISTENT-PO")
        assert result.found is False
        assert result.po_number == "NONEXISTENT-PO"

    def test_empty_po_returns_default(self):
        result = Svc._lookup_po("")
        assert result.found is False
        assert result.po_number == ""


# ---------------------------------------------------------------------------
# _apply_confidence_adjustments
# ---------------------------------------------------------------------------


class TestConfidenceAdjustments:
    def _make_field(self, confidence=0.8, extracted=True, raw_value="test"):
        """Create a simple mock for a header field result."""

        class _FR:
            pass

        fr = _FR()
        fr.confidence = confidence
        fr.extracted = extracted
        fr.raw_value = raw_value
        return fr

    def test_vendor_match_boosts_confidence(self):
        fr = self._make_field(confidence=0.80)

        class _ER:
            header_fields = {"vendor_name": fr}

        enrichment = EnrichmentResult(
            vendor_match=MasterDataMatch(
                match_type="EXACT_TAX_ID", confidence=0.98,
            ),
        )
        Svc._apply_confidence_adjustments(_ER(), enrichment)
        assert fr.confidence == pytest.approx(0.85)
        assert enrichment.confidence_adjustments["vendor_name"] == pytest.approx(0.05)

    def test_vendor_not_found_penalizes(self):
        fr = self._make_field(confidence=0.80, raw_value="Unknown Vendor")

        class _ER:
            header_fields = {"vendor_name": fr}

        enrichment = EnrichmentResult()  # vendor NOT_FOUND by default
        Svc._apply_confidence_adjustments(_ER(), enrichment)
        assert fr.confidence == pytest.approx(0.72)
        assert "Unknown Vendor" in enrichment.warnings[0]

    def test_po_match_boosts_po_confidence(self):
        po_fr = self._make_field(confidence=0.70)

        class _ER:
            header_fields = {"po_number": po_fr}

        enrichment = EnrichmentResult(
            po_lookup=POLookupResult(found=True, confidence=0.95),
        )
        Svc._apply_confidence_adjustments(_ER(), enrichment)
        assert po_fr.confidence == pytest.approx(0.75)

    def test_po_vendor_cross_match_extra_boost(self):
        vfr = self._make_field(confidence=0.80)
        pfr = self._make_field(confidence=0.70)

        class _ER:
            header_fields = {"vendor_name": vfr, "po_number": pfr}

        enrichment = EnrichmentResult(
            vendor_match=MasterDataMatch(
                match_type="FUZZY", entity_id=42, confidence=0.85,
            ),
            po_lookup=POLookupResult(
                found=True, vendor_id=42, confidence=0.95,
            ),
        )
        Svc._apply_confidence_adjustments(_ER(), enrichment)
        # vendor_name gets VENDOR_MATCH_BOOST + PO_VENDOR_MATCH_BOOST
        expected_delta = Svc.VENDOR_MATCH_BOOST + Svc.PO_VENDOR_MATCH_BOOST
        assert enrichment.confidence_adjustments["vendor_name"] == pytest.approx(
            expected_delta
        )

    def test_po_vendor_mismatch_warns(self):
        vfr = self._make_field(confidence=0.80)
        pfr = self._make_field(confidence=0.70)

        class _ER:
            header_fields = {"vendor_name": vfr, "po_number": pfr}

        enrichment = EnrichmentResult(
            vendor_match=MasterDataMatch(
                match_type="FUZZY",
                entity_id=42,
                entity_name="Vendor A",
                confidence=0.85,
            ),
            po_lookup=POLookupResult(
                found=True,
                vendor_id=99,
                vendor_name="Vendor B",
                confidence=0.95,
            ),
        )
        Svc._apply_confidence_adjustments(_ER(), enrichment)
        assert any("mismatch" in w.lower() for w in enrichment.warnings)


# ---------------------------------------------------------------------------
# enrich() -- top-level orchestration
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestEnrichOrchestration:
    def test_enrich_handles_empty_extraction_result(self):
        """enrich() should not crash on a bare object with no fields."""

        class _ER:
            document_intelligence = None
            header_fields = {}

        result = Svc.enrich(extraction_result=_ER())
        assert result.vendor_match.match_type == "NOT_FOUND"
        assert result.customer_match.match_type == "NOT_FOUND"
        assert result.po_lookup.found is False
        assert result.duration_ms >= 0

    def test_enrich_with_supplier_name(self):
        """enrich() passes supplier_name from extraction to _match_vendor."""
        from apps.vendors.models import Vendor

        v = Vendor.objects.create(
            code="E001",
            name="Test Supplier",
            normalized_name=Svc._normalize_name("Test Supplier"),
        )

        class _Intel:
            supplier_name = "Test Supplier"
            buyer_name = ""
            primary_po_number = ""
            parties = None

        class _ER:
            document_intelligence = _Intel()
            header_fields = {}

        result = Svc.enrich(extraction_result=_ER())
        assert result.vendor_match.match_type == "FUZZY"
        assert result.vendor_match.entity_id == v.pk

    def test_enrich_catches_exceptions_gracefully(self):
        """enrich() should catch errors in vendor/customer/PO matching and add warnings."""
        from unittest.mock import patch

        class _Intel:
            supplier_name = "some vendor"
            buyer_name = "some buyer"
            primary_po_number = ""
            parties = None

        class _ER:
            document_intelligence = _Intel()
            header_fields = {}

        with patch.object(
            Svc, "_match_vendor", side_effect=RuntimeError("vendor boom"),
        ), patch.object(
            Svc, "_match_customer", side_effect=RuntimeError("customer boom"),
        ):
            result = Svc.enrich(extraction_result=_ER())
        assert isinstance(result, EnrichmentResult)
        assert "Vendor matching failed" in result.warnings
        assert "Customer matching failed" in result.warnings


# ---------------------------------------------------------------------------
# Import regression -- the original bug
# ---------------------------------------------------------------------------


class TestImportRegression:
    """Ensure the fixed imports work correctly."""

    def test_vendor_alias_import_in_match_vendor(self):
        """_match_vendor must import VendorAliasMapping from posting_core, not VendorAlias from vendors."""
        import inspect
        source = inspect.getsource(Svc._match_vendor)
        assert "VendorAliasMapping" in source
        assert "from apps.posting_core.models import VendorAliasMapping" in source
        # Must NOT import VendorAlias
        assert "VendorAlias" not in source.replace("VendorAliasMapping", "")

    def test_vendor_alias_import_in_match_customer(self):
        """_match_customer must import VendorAliasMapping from posting_core, not VendorAlias from vendors."""
        import inspect
        source = inspect.getsource(Svc._match_customer)
        assert "VendorAliasMapping" in source
        assert "from apps.posting_core.models import VendorAliasMapping" in source
        assert "VendorAlias" not in source.replace("VendorAliasMapping", "")


# ---------------------------------------------------------------------------
# Dataclass output
# ---------------------------------------------------------------------------


class TestDataclassSerialization:
    def test_master_data_match_to_dict(self):
        m = MasterDataMatch(
            match_type="EXACT_TAX_ID",
            entity_id=1,
            entity_code="V001",
            entity_name="Acme",
            matched_value="TAX123",
            similarity=1.0,
            confidence=0.98,
        )
        d = m.to_dict()
        assert d["match_type"] == "EXACT_TAX_ID"
        assert d["entity_id"] == 1
        assert d["similarity"] == 1.0

    def test_po_lookup_result_to_dict_found(self):
        r = POLookupResult(
            found=True,
            po_id=10,
            po_number="PO-001",
            vendor_id=5,
            vendor_name="V",
            po_status="OPEN",
            total_amount=1000.0,
            currency="USD",
            confidence=0.95,
        )
        d = r.to_dict()
        assert d["found"] is True
        assert d["po_id"] == 10

    def test_po_lookup_result_to_dict_not_found(self):
        r = POLookupResult(po_number="X")
        d = r.to_dict()
        assert d["found"] is False
        assert "po_id" not in d

    def test_enrichment_result_match_confidence_average(self):
        er = EnrichmentResult(
            vendor_match=MasterDataMatch(match_type="FUZZY", confidence=0.80),
            customer_match=MasterDataMatch(match_type="ALIAS", confidence=0.90),
            po_lookup=POLookupResult(found=True, confidence=0.95),
        )
        # (0.80 + 0.90 + 0.95) / 3
        assert er.match_confidence == pytest.approx(0.8833, abs=0.01)

    def test_enrichment_result_match_confidence_no_matches(self):
        er = EnrichmentResult()
        assert er.match_confidence == 0.0
