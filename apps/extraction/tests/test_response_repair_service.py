"""Tests for ResponseRepairService — deterministic post-LLM repair rules."""
import pytest

from apps.extraction.services.response_repair_service import ResponseRepairService


# ---------------------------------------------------------------------------
# Rule a — invoice number exclusion
# ---------------------------------------------------------------------------

class TestInvoiceNumberExclusion:

    def test_irn_value_is_cleared(self):
        irn = "a" * 64
        raw = {"invoice_number": irn, "vendor_name": "ACME"}
        ocr = f"IRN: {irn}\nInvoice No.: INV-001"
        result = ResponseRepairService.repair(raw, ocr_text=ocr)
        assert result.was_repaired
        assert result.repaired_json["invoice_number"] == "INV-001"
        assert any("invoice_number" in a for a in result.repair_actions)

    def test_cart_ref_value_triggers_exclusion(self):
        raw = {"invoice_number": "CART-9876", "vendor_name": "Travel Co"}
        ocr = "CART Ref. No.: CART-9876\nInvoice No.: TI-20240115"
        result = ResponseRepairService.repair(raw, ocr_text=ocr)
        assert result.was_repaired
        assert result.repaired_json["invoice_number"] == "TI-20240115"

    def test_hotel_booking_id_triggers_exclusion_and_clears_when_no_recovery(self):
        raw = {"invoice_number": "HBK-001", "vendor_name": "Hotel"}
        ocr = "Hotel Booking ID: HBK-001\nNo invoice number visible"
        result = ResponseRepairService.repair(raw, ocr_text=ocr)
        assert result.was_repaired
        assert result.repaired_json["invoice_number"] == ""
        assert result.warnings  # should warn about clearing

    def test_valid_invoice_number_is_not_touched(self):
        raw = {"invoice_number": "INV/2024/0042", "vendor_name": "ACME"}
        ocr = "Invoice No.: INV/2024/0042"
        result = ResponseRepairService.repair(raw, ocr_text=ocr)
        assert not result.was_repaired
        assert result.repaired_json["invoice_number"] == "INV/2024/0042"

    def test_empty_invoice_number_is_not_touched(self):
        raw = {"invoice_number": "", "vendor_name": "ACME"}
        result = ResponseRepairService.repair(raw, ocr_text="")
        assert not result.was_repaired

    def test_document_no_triggers_exclusion(self):
        raw = {"invoice_number": "DOC-55", "vendor_name": "Corp"}
        ocr = "Document No.: DOC-55\nInvoice No.: INV-55"
        result = ResponseRepairService.repair(raw, ocr_text=ocr)
        assert result.was_repaired
        assert result.repaired_json["invoice_number"] == "INV-55"


# ---------------------------------------------------------------------------
# Rule b — tax percentage recomputation
# ---------------------------------------------------------------------------

class TestTaxPercentageRecomputation:

    def test_recomputes_when_llm_value_is_wrong(self):
        raw = {
            "subtotal": "10000",
            "tax_amount": "1800",
            "tax_percentage": "5",  # wrong — should be 18
        }
        result = ResponseRepairService.repair(raw)
        assert result.was_repaired
        assert abs(float(result.repaired_json["tax_percentage"]) - 18.0) < 0.1

    def test_no_repair_when_within_half_percent_tolerance(self):
        raw = {
            "subtotal": "10000",
            "tax_amount": "1800",
            "tax_percentage": "18.2",  # within 0.5% of 18.0
        }
        result = ResponseRepairService.repair(raw)
        # tax_percentage should NOT be repaired (within tolerance)
        assert "tax_percentage" not in " ".join(result.repair_actions)

    def test_no_repair_when_subtotal_zero(self):
        raw = {"subtotal": "0", "tax_amount": "1800", "tax_percentage": "18"}
        result = ResponseRepairService.repair(raw)
        assert not any("tax_percentage" in a for a in result.repair_actions)

    def test_no_repair_when_tax_amount_missing(self):
        raw = {"subtotal": "10000", "tax_percentage": "18"}
        result = ResponseRepairService.repair(raw)
        assert not any("tax_percentage" in a for a in result.repair_actions)

    def test_handles_currency_symbol_in_amounts(self):
        raw = {
            "subtotal": "₹10,000",
            "tax_amount": "₹1,800",
            "tax_percentage": "5",
        }
        result = ResponseRepairService.repair(raw)
        assert result.was_repaired
        assert abs(float(result.repaired_json["tax_percentage"]) - 18.0) < 0.1


# ---------------------------------------------------------------------------
# Rule c — subtotal / line reconciliation
# ---------------------------------------------------------------------------

class TestSubtotalReconciliation:

    def test_aligns_subtotal_to_line_sum(self):
        raw = {
            "subtotal": "5000",  # wrong
            "line_items": [
                {"item_description": "Consulting", "line_amount": "3000"},
                {"item_description": "Support", "line_amount": "4000"},
            ],
        }
        result = ResponseRepairService.repair(raw)
        assert result.was_repaired
        from decimal import Decimal
        assert Decimal(result.repaired_json["subtotal"]) == Decimal("7000")

    def test_skips_gst_lines_in_sum(self):
        raw = {
            "subtotal": "7000",
            "line_items": [
                {"item_description": "Consulting", "line_amount": "3000"},
                {"item_description": "Support", "line_amount": "4000"},
                {"item_description": "CGST 9%", "line_amount": "630"},
                {"item_description": "SGST 9%", "line_amount": "630"},
            ],
        }
        result = ResponseRepairService.repair(raw)
        # GST lines excluded → sum = 7000, matches current subtotal → no repair
        assert not any("subtotal" in a for a in result.repair_actions)

    def test_no_repair_within_one_unit_tolerance(self):
        raw = {
            "subtotal": "7000.50",
            "line_items": [
                {"item_description": "Service A", "line_amount": "3000"},
                {"item_description": "Service B", "line_amount": "4000"},
            ],
        }
        result = ResponseRepairService.repair(raw)
        assert not any("subtotal" in a for a in result.repair_actions)

    def test_no_repair_when_no_line_items(self):
        raw = {"subtotal": "5000", "line_items": []}
        result = ResponseRepairService.repair(raw)
        assert not result.was_repaired


# ---------------------------------------------------------------------------
# Rule d — line-level tax allocation
# ---------------------------------------------------------------------------

class TestLineTaxAllocation:

    def test_moves_tax_to_service_charge_line(self):
        raw = {
            "subtotal": "8400",
            "tax_amount": "72",
            "line_items": [
                {
                    "item_description": "Base Fare",
                    "line_amount": "8000",
                    "tax_amount": "72",
                    "tax_percentage": "18",
                },
                {
                    "item_description": "Service Charge",
                    "line_amount": "400",
                    "tax_amount": "0",
                    "tax_percentage": "0",
                },
            ],
        }
        result = ResponseRepairService.repair(raw, invoice_category="travel")
        assert result.was_repaired
        lines = result.repaired_json["line_items"]
        base_line = next(li for li in lines if "Base" in li["item_description"])
        svc_line = next(li for li in lines if "Service" in li["item_description"])
        assert base_line["tax_amount"] == "0"
        assert float(svc_line["tax_amount"]) == 72.0

    def test_skips_when_tax_already_on_service_line(self):
        raw = {
            "tax_amount": "72",
            "line_items": [
                {"item_description": "Base Fare", "line_amount": "8000", "tax_amount": "0"},
                {"item_description": "Service Charge", "line_amount": "400", "tax_amount": "72"},
            ],
        }
        result = ResponseRepairService.repair(raw, invoice_category="travel")
        # No reallocation needed
        assert not any("line_tax" in a for a in result.repair_actions)

    def test_does_not_fire_for_goods_category(self):
        raw = {
            "tax_amount": "900",
            "line_items": [
                {"item_description": "Base Fare", "line_amount": "5000", "tax_amount": "900"},
                {"item_description": "Service Charge", "line_amount": "400", "tax_amount": "0"},
            ],
        }
        result = ResponseRepairService.repair(raw, invoice_category="goods")
        assert not any("line_tax" in a for a in result.repair_actions)


# ---------------------------------------------------------------------------
# Rule e — travel line consolidation
# ---------------------------------------------------------------------------

class TestTravelLineConsolidation:

    def test_consolidates_basic_plus_hotel_tax_into_total_fare(self):
        raw = {
            "line_items": [
                {"item_description": "Basic Fare", "line_amount": "8000"},
                {"item_description": "Hotel Taxes", "line_amount": "400"},
                {"item_description": "Total Fare", "line_amount": "8400"},
                {"item_description": "Service Charge", "line_amount": "200"},
            ],
        }
        result = ResponseRepairService.repair(raw, invoice_category="travel")
        assert result.was_repaired
        descs = [li["item_description"] for li in result.repaired_json["line_items"]]
        assert "Basic Fare" not in descs
        assert "Hotel Taxes" not in descs
        assert "Total Fare" in descs
        assert "Service Charge" in descs  # unaffected

    def test_warns_when_total_fare_does_not_match(self):
        raw = {
            "line_items": [
                {"item_description": "Basic Fare", "line_amount": "8000"},
                {"item_description": "Hotel Taxes", "line_amount": "400"},
                {"item_description": "Total Fare", "line_amount": "9999"},  # mismatch
            ],
        }
        result = ResponseRepairService.repair(raw, invoice_category="travel")
        assert result.warnings  # should warn
        # Lines should be untouched
        assert len(result.repaired_json["line_items"]) == 3

    def test_does_not_fire_when_fewer_than_three_lines(self):
        raw = {
            "line_items": [
                {"item_description": "Basic Fare", "line_amount": "8000"},
                {"item_description": "Total Fare", "line_amount": "8000"},
            ],
        }
        result = ResponseRepairService.repair(raw, invoice_category="travel")
        assert not any("consolidat" in a for a in result.repair_actions)


# ---------------------------------------------------------------------------
# Safety / backward compat
# ---------------------------------------------------------------------------

class TestRepairServiceSafety:

    def test_empty_dict_returns_empty(self):
        result = ResponseRepairService.repair({})
        assert result.repaired_json == {}
        assert not result.was_repaired

    def test_none_input_returns_empty(self):
        result = ResponseRepairService.repair(None)
        assert not result.was_repaired

    def test_repair_metadata_embedded_in_key(self):
        """Verify the _repair metadata is NOT added by repair service itself (adapter adds it)."""
        raw = {"invoice_number": "CART-1", "vendor_name": "X"}
        ocr = "CART Ref. No.: CART-1\nInvoice No.: INV-100"
        result = ResponseRepairService.repair(raw, ocr_text=ocr)
        assert result.was_repaired
        # The service itself does NOT embed _repair — adapter does that
        assert "_repair" not in result.repaired_json

    def test_original_not_mutated(self):
        raw = {
            "invoice_number": "CART-9876",
            "subtotal": "1000",
            "tax_amount": "180",
            "tax_percentage": "5",
        }
        ocr = "CART Ref. No.: CART-9876\nInvoice No.: INV-50"
        original_inv_num = raw["invoice_number"]
        ResponseRepairService.repair(raw, ocr_text=ocr)
        # Original dict should be unchanged
        assert raw["invoice_number"] == original_inv_num
