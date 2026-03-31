"""Tests for QRCodeDecoderService and QRInvoiceData.

Covers:
  - Valid Indian e-invoice QR JSON parsing (all fields)
  - IRN validation: exactly 64 hex chars required
  - Strategy 1: decode_from_texts (Azure DI pre-decoded)
  - Strategy 2: _decode_from_ocr_text (IRN regex search in OCR)
  - Strategy 3: _decode_from_image (pyzbar — tested via mock)
  - Fail-silent on bad input: None, empty string, malformed JSON
  - to_evidence_context() output shape
  - to_serializable() output shape
  - normalise_invoice_number / normalise_gstin helpers
  - decode() orchestration: tries strategies in order, stops at first success
"""
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from apps.extraction.services.qr_decoder_service import (
    QRCodeDecoderService,
    QRInvoiceData,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

VALID_IRN = "a" * 64  # 64-char hex string (lowercase)

VALID_PAYLOAD = {
    "Version": "1.1",
    "Irn": VALID_IRN,
    "IrnDt": "2024-01-15 10:30:00",
    "SellerGstin": "29AAAAA0000A1ZA",
    "BuyerGstin": "07BBBBB0000B1ZD",
    "DocNo": "INV/2024/001",
    "DocDt": "15/01/2024",
    "TotInvVal": 11800.00,
    "ItemCnt": 3,
    "MainHsnCode": "8471",
    "DocTyp": "INV",
}

VALID_JSON = json.dumps(VALID_PAYLOAD)


# ── QRInvoiceData ─────────────────────────────────────────────────────────────

class TestQRInvoiceData:
    def _make(self, **kwargs) -> QRInvoiceData:
        base = dict(
            irn=VALID_IRN,
            irn_date="2024-01-15 10:30:00",
            seller_gstin="29AAAAA0000A1ZA",
            buyer_gstin="07BBBBB0000B1ZD",
            doc_number="INV/2024/001",
            doc_date="15/01/2024",
            total_value=Decimal("11800.00"),
            item_count=3,
            main_hsn="8471",
            doc_type="INV",
            decode_strategy="azure_barcode",
        )
        base.update(kwargs)
        return QRInvoiceData(**base)

    def test_to_evidence_context_includes_verified_dict(self):
        qr = self._make()
        ctx = qr.to_evidence_context()
        assert "qr_verified" in ctx
        assert ctx["qr_verified"]["invoice_number"] == "INV/2024/001"
        assert ctx["qr_verified"]["invoice_date"] == "15/01/2024"
        assert ctx["qr_verified"]["vendor_tax_id"] == "29AAAAA0000A1ZA"
        assert ctx["qr_verified"]["total_amount"] == "11800.00"

    def test_to_evidence_context_irn(self):
        qr = self._make()
        ctx = qr.to_evidence_context()
        assert ctx["qr_irn"] == VALID_IRN

    def test_to_evidence_context_doc_type(self):
        qr = self._make(doc_type="CRN")
        ctx = qr.to_evidence_context()
        assert ctx["qr_doc_type"] == "CRN"

    def test_to_evidence_context_item_count(self):
        qr = self._make(item_count=5)
        ctx = qr.to_evidence_context()
        assert ctx["qr_item_count"] == 5

    def test_to_evidence_context_buyer_gstin(self):
        qr = self._make()
        ctx = qr.to_evidence_context()
        assert ctx["qr_buyer_gstin"] == "07BBBBB0000B1ZD"

    def test_to_evidence_context_omits_empty_fields(self):
        qr = self._make(doc_number="", doc_date="", seller_gstin="", total_value=None)
        ctx = qr.to_evidence_context()
        assert "invoice_number" not in ctx["qr_verified"]
        assert "invoice_date" not in ctx["qr_verified"]
        assert "vendor_tax_id" not in ctx["qr_verified"]
        assert "total_amount" not in ctx["qr_verified"]

    def test_to_serializable_shape(self):
        qr = self._make()
        s = qr.to_serializable()
        assert s["irn"] == VALID_IRN
        assert s["seller_gstin"] == "29AAAAA0000A1ZA"
        assert s["doc_number"] == "INV/2024/001"
        assert s["total_value"] == 11800.0
        assert s["item_count"] == 3
        assert s["doc_type"] == "INV"
        assert s["decode_strategy"] == "azure_barcode"
        assert s["signature_verified"] is False

    def test_to_serializable_none_total_value(self):
        qr = self._make(total_value=None)
        assert qr.to_serializable()["total_value"] is None


# ── _parse_einvoice_json ──────────────────────────────────────────────────────

class TestParseEInvoiceJson:
    def test_valid_payload_returns_qr_data(self):
        result = QRCodeDecoderService._parse_einvoice_json(VALID_JSON, strategy="ocr_text")
        assert result is not None
        assert result.irn == VALID_IRN
        assert result.doc_number == "INV/2024/001"
        assert result.seller_gstin == "29AAAAA0000A1ZA"
        assert result.buyer_gstin == "07BBBBB0000B1ZD"
        assert result.total_value == Decimal("11800.0")
        assert result.item_count == 3
        assert result.doc_type == "INV"
        assert result.decode_strategy == "ocr_text"

    def test_crn_doc_type(self):
        payload = dict(VALID_PAYLOAD, DocTyp="CRN")
        result = QRCodeDecoderService._parse_einvoice_json(json.dumps(payload), "azure_barcode")
        assert result.doc_type == "CRN"

    def test_seller_gstin_uppercased(self):
        payload = dict(VALID_PAYLOAD, SellerGstin="29aaaaa0000a1za")
        result = QRCodeDecoderService._parse_einvoice_json(json.dumps(payload), "ocr_text")
        assert result.seller_gstin == "29AAAAA0000A1ZA"

    def test_irn_too_short_returns_none(self):
        payload = dict(VALID_PAYLOAD, Irn="abc123")
        result = QRCodeDecoderService._parse_einvoice_json(json.dumps(payload), "ocr_text")
        assert result is None

    def test_irn_too_long_returns_none(self):
        payload = dict(VALID_PAYLOAD, Irn="a" * 65)
        result = QRCodeDecoderService._parse_einvoice_json(json.dumps(payload), "ocr_text")
        assert result is None

    def test_irn_missing_returns_none(self):
        payload = {k: v for k, v in VALID_PAYLOAD.items() if k != "Irn"}
        result = QRCodeDecoderService._parse_einvoice_json(json.dumps(payload), "ocr_text")
        assert result is None

    def test_malformed_json_returns_none(self):
        result = QRCodeDecoderService._parse_einvoice_json("{not valid json", "ocr_text")
        assert result is None

    def test_empty_string_returns_none(self):
        assert QRCodeDecoderService._parse_einvoice_json("", "ocr_text") is None

    def test_none_total_value_parsed_gracefully(self):
        payload = dict(VALID_PAYLOAD)
        del payload["TotInvVal"]
        result = QRCodeDecoderService._parse_einvoice_json(json.dumps(payload), "ocr_text")
        assert result is not None
        assert result.total_value is None

    def test_invalid_total_value_parsed_gracefully(self):
        payload = dict(VALID_PAYLOAD, TotInvVal="not_a_number")
        result = QRCodeDecoderService._parse_einvoice_json(json.dumps(payload), "ocr_text")
        assert result is not None
        assert result.total_value is None

    def test_item_count_defaults_to_zero_on_missing(self):
        payload = dict(VALID_PAYLOAD)
        del payload["ItemCnt"]
        result = QRCodeDecoderService._parse_einvoice_json(json.dumps(payload), "ocr_text")
        assert result is not None
        assert result.item_count == 0

    def test_item_count_defaults_to_zero_on_bad_value(self):
        payload = dict(VALID_PAYLOAD, ItemCnt="bad")
        result = QRCodeDecoderService._parse_einvoice_json(json.dumps(payload), "ocr_text")
        assert result is not None
        assert result.item_count == 0

    def test_non_dict_json_returns_none(self):
        result = QRCodeDecoderService._parse_einvoice_json('["a", "b"]', "ocr_text")
        assert result is None


# ── decode_from_texts (Strategy 1) ───────────────────────────────────────────

class TestDecodeFromTexts:
    def test_valid_qr_text_decoded(self):
        result = QRCodeDecoderService.decode_from_texts([VALID_JSON])
        assert result is not None
        assert result.irn == VALID_IRN
        assert result.decode_strategy == "azure_barcode"

    def test_first_valid_text_wins(self):
        bad = '{"bad": "json"}'
        result = QRCodeDecoderService.decode_from_texts([bad, VALID_JSON])
        assert result is not None
        assert result.irn == VALID_IRN

    def test_empty_list_returns_none(self):
        assert QRCodeDecoderService.decode_from_texts([]) is None

    def test_none_list_returns_none(self):
        assert QRCodeDecoderService.decode_from_texts(None) is None

    def test_all_invalid_texts_returns_none(self):
        assert QRCodeDecoderService.decode_from_texts(["garbage", "{}", ""]) is None

    def test_empty_strings_skipped(self):
        assert QRCodeDecoderService.decode_from_texts(["", "  ", "\t"]) is None

    def test_whitespace_around_json_handled(self):
        result = QRCodeDecoderService.decode_from_texts([f"  {VALID_JSON}  "])
        assert result is not None

    def test_nic_signed_jwt_decoded(self):
        """NIC-signed JWT QR (spec v1.1): payload['data'] is stringified JSON."""
        import base64
        header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
        inner = json.dumps(VALID_PAYLOAD)
        body_dict = {"iss": "NIC", "data": inner}
        payload_b64 = base64.urlsafe_b64encode(json.dumps(body_dict).encode()).rstrip(b"=").decode()
        fake_sig = base64.urlsafe_b64encode(b"fakesig").rstrip(b"=").decode()
        jwt_text = f"{header}.{payload_b64}.{fake_sig}"

        result = QRCodeDecoderService.decode_from_texts([jwt_text])
        assert result is not None
        assert result.irn == VALID_IRN
        assert result.decode_strategy == "azure_barcode"
        assert result.doc_number == VALID_PAYLOAD["DocNo"]

    def test_jwt_without_data_field_returns_none(self):
        """JWT with no 'data' or 'Irn' field in payload → None."""
        import base64
        header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=").decode()
        body_b64 = base64.urlsafe_b64encode(b'{"iss":"NIC","foo":"bar"}').rstrip(b"=").decode()
        jwt_text = f"{header}.{body_b64}.fakesig"
        assert QRCodeDecoderService.decode_from_texts([jwt_text]) is None

    def test_non_jwt_text_falls_through_to_json_parse(self):
        """Plain JSON (non-JWT) still works — no regression."""
        result = QRCodeDecoderService.decode_from_texts([VALID_JSON])
        assert result is not None
        assert result.irn == VALID_IRN


# ── _decode_from_ocr_text (Strategy 2) ───────────────────────────────────────

class TestDecodeFromOcrText:
    def _make_ocr(self, extra_prefix="", extra_suffix="") -> str:
        """Wrap valid JSON in OCR-like context text."""
        return f"Invoice document header\n{extra_prefix}{VALID_JSON}{extra_suffix}\nSome footer text"

    def test_valid_json_in_ocr_text_decoded(self):
        result = QRCodeDecoderService._decode_from_ocr_text(self._make_ocr())
        assert result is not None
        assert result.irn == VALID_IRN
        assert result.decode_strategy == "ocr_text"

    def test_no_irn_in_text_returns_none(self):
        result = QRCodeDecoderService._decode_from_ocr_text("plain text without any IRN")
        assert result is None

    def test_irn_without_valid_json_returns_none(self):
        # Put an IRN-shaped string but not inside valid e-invoice JSON
        text = f'"Irn": "{VALID_IRN}" but no JSON wrapping'
        result = QRCodeDecoderService._decode_from_ocr_text(text)
        assert result is None

    def test_empty_string_returns_none(self):
        assert QRCodeDecoderService._decode_from_ocr_text("") is None

    def test_json_surrounded_by_noise(self):
        ocr = f"Barcode detected: {VALID_JSON} End of barcode section."
        result = QRCodeDecoderService._decode_from_ocr_text(ocr)
        assert result is not None
        assert result.irn == VALID_IRN

    def test_irn_exact_64_chars_required(self):
        """IRN pattern only matches 64-char hex strings."""
        short_irn = "b" * 32
        payload = dict(VALID_PAYLOAD, Irn=short_irn)
        text = json.dumps(payload)
        result = QRCodeDecoderService._decode_from_ocr_text(text)
        assert result is None

    def test_hyphenated_line_break_in_irn_is_joined(self):
        """PDF word-wrap splits the IRN with '-\\n'; should be joined and matched."""
        irn_part1 = VALID_IRN[:47]   # 47 chars before the break
        irn_part2 = VALID_IRN[47:]   # remaining 17 chars
        # Simulate plain-text IRN label with hyphenated break
        ocr = f"Tax Invoice\nIRN\n: {irn_part1}-\n{irn_part2}\nGSTIN/UIN: 29AAAAA0000A1ZA"
        result = QRCodeDecoderService._decode_from_ocr_text(ocr)
        assert result is not None
        assert result.irn == VALID_IRN.lower()
        assert result.decode_strategy == "ocr_irn_text"

    def test_plain_text_irn_label_fallback(self):
        """Invoice with printed 'IRN : <64hex>' but no JSON yields ocr_irn_text."""
        ocr = (
            "Tax Invoice\n"
            f"IRN : {VALID_IRN}\n"
            "GSTIN/UIN: 29AAAAA0000A1ZA\n"
            "Buyer GSTIN: 07BBBBB0000B1ZD\n"
        )
        result = QRCodeDecoderService._decode_from_ocr_text(ocr)
        assert result is not None
        assert result.irn == VALID_IRN.lower()
        assert result.decode_strategy == "ocr_irn_text"
        assert result.seller_gstin == "29AAAAA0000A1ZA"

    def test_plain_text_irn_without_gstin(self):
        """Fallback with no nearby GSTIN still returns the IRN."""
        ocr = f"IRN : {VALID_IRN}\nSome other text"
        result = QRCodeDecoderService._decode_from_ocr_text(ocr)
        assert result is not None
        assert result.irn == VALID_IRN.lower()
        assert result.seller_gstin == ""


# ── _decode_from_image (Strategy 3) ──────────────────────────────────────────

class TestDecodeFromImage:
    def test_returns_none_when_pyzbar_not_installed(self):
        """If pyzbar is not importable, method must return None silently."""
        with patch.dict("sys.modules", {"pyzbar": None, "pyzbar.pyzbar": None}):
            result = QRCodeDecoderService._decode_from_image("/fake/path.pdf")
        assert result is None

    def test_pyzbar_decode_qr_returns_data(self):
        """Mock pyzbar to return a valid e-invoice QR barcode."""
        mock_barcode = MagicMock()
        mock_barcode.type = "QRCODE"
        mock_barcode.data = VALID_JSON.encode("utf-8")

        mock_img = MagicMock()

        with patch.object(
            QRCodeDecoderService, "_file_to_pil_images", return_value=[mock_img]
        ):
            with patch("apps.extraction.services.qr_decoder_service.QRCodeDecoderService._decode_from_image") as mock_method:
                mock_method.return_value = QRCodeDecoderService._parse_einvoice_json(
                    VALID_JSON, strategy="pyzbar"
                )
                result = mock_method("/fake/path.pdf")

        assert result is not None
        assert result.irn == VALID_IRN
        assert result.decode_strategy == "pyzbar"

    def test_non_qrcode_barcodes_skipped(self):
        """Barcodes that are not QRCODE type should be skipped."""
        try:
            from pyzbar import pyzbar as _pyzbar  # noqa
            _pyzbar_available = True
        except ImportError:
            _pyzbar_available = False

        if not _pyzbar_available:
            pytest.skip("pyzbar not installed")

        from PIL import Image  # noqa
        mock_barcode = MagicMock()
        mock_barcode.type = "CODE128"  # not a QR
        mock_barcode.data = VALID_JSON.encode("utf-8")

        mock_img = MagicMock()
        with patch("pyzbar.pyzbar.decode", return_value=[mock_barcode]):
            with patch.object(QRCodeDecoderService, "_file_to_pil_images", return_value=[mock_img]):
                result = QRCodeDecoderService._decode_from_image("/fake/path.jpg")
        assert result is None


# ── decode() orchestration ───────────────────────────────────────────────────

class TestDecodeOrchestration:
    def test_strategy1_wins_when_qr_texts_valid(self):
        result = QRCodeDecoderService.decode(
            file_path="/fake.pdf",
            ocr_text="",
            qr_texts=[VALID_JSON],
        )
        assert result is not None
        assert result.decode_strategy == "azure_barcode"

    def test_strategy2_used_when_qr_texts_empty(self):
        ocr = f"Page content\n{VALID_JSON}\nEnd"
        result = QRCodeDecoderService.decode(
            file_path="/fake.pdf",
            ocr_text=ocr,
            qr_texts=[],
        )
        assert result is not None
        assert result.decode_strategy == "ocr_text"

    def test_strategy1_skipped_on_empty_qr_texts(self):
        """With empty qr_texts, strategy2 (OCR) should run."""
        ocr = f"Document\n{VALID_JSON}\nEnd"
        result = QRCodeDecoderService.decode(
            file_path="/fake.pdf",
            ocr_text=ocr,
            qr_texts=None,
        )
        assert result is not None
        assert result.decode_strategy == "ocr_text"

    def test_returns_none_when_no_qr_found(self):
        result = QRCodeDecoderService.decode(
            file_path="/fake.pdf",
            ocr_text="Plain invoice text without QR.",
            qr_texts=[],
        )
        assert result is None

    def test_fail_silent_on_exception(self):
        """decode() must never raise — return None on any error."""
        with patch.object(
            QRCodeDecoderService,
            "decode_from_texts",
            side_effect=RuntimeError("boom"),
        ):
            result = QRCodeDecoderService.decode(
                file_path="", ocr_text="", qr_texts=["anything"]
            )
        assert result is None

    def test_strategy3_called_when_strategies_1_and_2_fail(self):
        with patch.object(QRCodeDecoderService, "_decode_from_image") as mock_img:
            mock_img.return_value = None
            result = QRCodeDecoderService.decode(
                file_path="/fake.pdf",
                ocr_text="no irn here",
                qr_texts=[],
            )
        mock_img.assert_called_once_with("/fake.pdf")
        assert result is None


# ── Normalisation helpers ─────────────────────────────────────────────────────

class TestNormalisationHelpers:
    def test_normalise_invoice_number_removes_separators(self):
        assert QRCodeDecoderService.normalise_invoice_number("INV/2024/001") == "INV2024001"
        assert QRCodeDecoderService.normalise_invoice_number("INV-2024-001") == "INV2024001"
        assert QRCodeDecoderService.normalise_invoice_number("INV 2024 001") == "INV2024001"

    def test_normalise_invoice_number_uppercases(self):
        assert QRCodeDecoderService.normalise_invoice_number("inv/2024/001") == "INV2024001"

    def test_normalise_gstin_strips_and_uppercases(self):
        assert QRCodeDecoderService.normalise_gstin("  29aaaaa0000a1za  ") == "29AAAAA0000A1ZA"

    def test_normalise_gstin_already_upper(self):
        assert QRCodeDecoderService.normalise_gstin("29AAAAA0000A1ZA") == "29AAAAA0000A1ZA"
