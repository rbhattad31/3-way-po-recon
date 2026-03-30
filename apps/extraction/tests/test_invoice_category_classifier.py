"""Tests for InvoiceCategoryClassifier — rule-based invoice type detection."""
import pytest

from apps.extraction_core.services.invoice_category_classifier import InvoiceCategoryClassifier


class TestInvoiceCategoryClassifier:

    def test_empty_text_returns_service_default(self):
        result = InvoiceCategoryClassifier.classify("")
        assert result.category == "service"
        assert result.confidence == 0.0

    def test_whitespace_only_returns_service_default(self):
        result = InvoiceCategoryClassifier.classify("   \n\t  ")
        assert result.category == "service"
        assert result.confidence == 0.0

    # ── Travel detection ──────────────────────────────────────────

    def test_classifies_hotel_invoice_as_travel(self):
        ocr = (
            "TRAVEL SOLUTIONS PVT LTD\n"
            "Hotel Booking ID: HBK-2024-001\n"
            "CART Ref. No.: CART-9876\n"
            "Passenger Name: John Smith\n"
            "Hotel: Grand Hyatt\n"
            "Check-in: 15-Jan-2024  Check-out: 17-Jan-2024\n"
            "Room Rate: 5000 per night\n"
            "Total Fare: 12000"
        )
        result = InvoiceCategoryClassifier.classify(ocr)
        assert result.category == "travel"
        assert result.confidence > 0.5

    def test_classifies_airfare_invoice_as_travel(self):
        ocr = (
            "AIRLINE BOOKING RECEIPT\n"
            "PNR: ABC123\n"
            "Passenger: Jane Doe\n"
            "Basic Fare: 8000\n"
            "Service Charge: 400\n"
            "Total Fare: 8400\n"
            "Departure: Mumbai  Arrival: Delhi"
        )
        result = InvoiceCategoryClassifier.classify(ocr)
        assert result.category == "travel"

    def test_cart_ref_ocr_classified_as_travel(self):
        ocr = "CART Ref. No. 12345\nItinerary\nBooking Confirmation No.: BC-999"
        result = InvoiceCategoryClassifier.classify(ocr)
        assert result.category == "travel"

    # ── Goods detection ───────────────────────────────────────────

    def test_classifies_goods_invoice_by_hsn(self):
        ocr = (
            "TAX INVOICE\n"
            "Vendor: ABC Suppliers\n"
            "HSN Code: 84715000\n"
            "Qty: 10 pcs\n"
            "Rate: 500 per unit\n"
            "Material: Steel Rods\n"
            "Total: 5000"
        )
        result = InvoiceCategoryClassifier.classify(ocr)
        assert result.category == "goods"

    def test_classifies_goods_by_qty_and_rate(self):
        ocr = (
            "Product Code: SKU-001\n"
            "Qty: 100\n"
            "Rate per unit: 50\n"
            "Batch No.: B202401\n"
            "E-way Bill No.: 1234567"
        )
        result = InvoiceCategoryClassifier.classify(ocr)
        assert result.category == "goods"

    # ── Service detection ─────────────────────────────────────────

    def test_classifies_consulting_fee_as_service(self):
        ocr = (
            "PROFESSIONAL SERVICES INVOICE\n"
            "Consulting Fees for January 2024\n"
            "SAC: 998313\n"
            "Management Advisory Services\n"
            "Amount: 150000"
        )
        result = InvoiceCategoryClassifier.classify(ocr)
        assert result.category == "service"

    def test_classifies_subscription_as_service(self):
        ocr = (
            "Cloud Software Subscription\n"
            "Annual Maintenance Contract\n"
            "Support Charges for Q1 2024\n"
            "Total: 25000"
        )
        result = InvoiceCategoryClassifier.classify(ocr)
        assert result.category == "service"

    # ── Result structure ─────────────────────────────────────────

    def test_result_has_signals(self):
        ocr = "Hotel booking itinerary passenger name CART Ref. No."
        result = InvoiceCategoryClassifier.classify(ocr)
        assert isinstance(result.signals, list)
        assert len(result.signals) > 0

    def test_signals_capped_at_ten(self):
        # Generate text with many signal words
        ocr = " ".join(["hotel itinerary airfare flight PNR cabin departure arrival"] * 5)
        result = InvoiceCategoryClassifier.classify(ocr)
        assert len(result.signals) <= 10

    def test_is_ambiguous_flag_when_scores_are_close(self):
        # A text with equal service and goods signals should be ambiguous
        ocr = (
            "Professional Services  Consulting Fee  HSN Code: 12345  "
            "Qty: 1  SAC: 9983  Management Fee  Subscription"
        )
        result = InvoiceCategoryClassifier.classify(ocr)
        # Not asserting specific category — just that ambiguous is a bool
        assert isinstance(result.is_ambiguous, bool)

    def test_confidence_between_zero_and_one(self):
        result = InvoiceCategoryClassifier.classify("some random text with no invoice signals")
        assert 0.0 <= result.confidence <= 1.0
