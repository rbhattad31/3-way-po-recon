"""QR Code decoder service for Indian e-Invoice QR codes.

Indian e-invoices (GST mandate, businesses > ₹5Cr turnover) carry a
digitally-signed QR code containing key invoice fields registered on the
Invoice Registration Portal (IRP / NIC).

QR payload format (GSTN e-invoice spec v1.1):
{
    "Version": "1.1",
    "Irn":          "<64-char sha256 hash>",
    "IrnDt":        "2024-01-15 10:30:00",
    "SellerGstin":  "29AAAAA0000A1ZA",
    "BuyerGstin":   "07BBBBB0000B1ZD",
    "DocNo":        "INV/2024/001",
    "DocDt":        "15/01/2024",
    "TotInvVal":    11800.00,
    "ItemCnt":      3,
    "MainHsnCode":  "8471",
    "DocTyp":       "INV"          # INV | CRN | DBN
}

Two decode strategies (attempted in order):
  1. OCR text parsing — search for e-invoice JSON / IRN pattern in ocr_text.
     Azure DI prebuilt-read returns barcode values in result.pages[].barcodes;
     the adapter appends these as separate qr_texts strings (see extraction_adapter.py).
  2. pyzbar pixel-level image decode — optional; requires:
       pip install pyzbar Pillow
     and optionally PyMuPDF (for PDF pages) or pdf2image as fallback.

Signature verification (NIC public certificate) is not performed here; the
decoded values are used as high-confidence extraction hints, not as a
security control.  To verify, fetch the NIC cert from:
  https://einvoice1.gst.gov.in/Others/PublicKey

Design rules:
  - Fail-silent everywhere — never raises, always returns Optional[QRInvoiceData].
  - No mandatory new dependencies (pyzbar + image libs are optional).
  - Decoder is stateless; all methods are static.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import List, Optional

logger = logging.getLogger(__name__)

# ── Regex patterns ────────────────────────────────────────────────────────────

# The IRN is a 64-character lowercase hex string (JSON payload format)
_IRN_RE = re.compile(r'"Irn"\s*:\s*"([a-fA-F0-9]{64})"')

# Plain-text IRN label as printed on the invoice (e.g. "IRN : 30853e...")
_PLAIN_IRN_RE = re.compile(r'\bIRN\b\s*[:\-]?\s*([a-fA-F0-9]{64})', re.IGNORECASE)

# Broad JSON candidate matcher (no nested braces, min 60 chars)
_JSON_CANDIDATE_RE = re.compile(r'\{[^{}]{60,}\}', re.DOTALL)

# GSTIN pattern: 15-char alphanumeric per GST spec
_GSTIN_RE = re.compile(
    r'\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b'
)

# Normalise invoice number for comparison (strip spaces/dashes/slashes)
_NORM_INV_RE = re.compile(r'[\s\-/]')


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class QRInvoiceData:
    """Structured data decoded from an Indian GST e-invoice QR code."""

    irn: str = ""
    """64-character Invoice Reference Number (sha256 hash assigned by IRP)."""

    irn_date: str = ""
    """Date-time the IRN was registered: 'YYYY-MM-DD HH:MM:SS'."""

    seller_gstin: str = ""
    """Supplier's 15-character GSTIN."""

    buyer_gstin: str = ""
    """Buyer's 15-character GSTIN (empty for B2C invoices)."""

    doc_number: str = ""
    """Invoice / document number as registered on IRP."""

    doc_date: str = ""
    """Invoice date — may be 'DD/MM/YYYY' or 'YYYY-MM-DD' depending on IRP version."""

    total_value: Optional[Decimal] = None
    """Total invoice value (TotInvVal) from the QR payload."""

    item_count: int = 0
    """Number of line items (ItemCnt)."""

    main_hsn: str = ""
    """HSN/SAC code of the primary line item."""

    doc_type: str = "INV"
    """Document type: 'INV' (invoice), 'CRN' (credit note), 'DBN' (debit note)."""

    raw_payload: dict = field(default_factory=dict)
    """Full decoded JSON payload for audit trail."""

    decode_strategy: str = ""
    """Which strategy succeeded: 'azure_barcode' | 'ocr_text' | 'pyzbar'."""

    signature_verified: bool = False
    """True only if NIC public-key signature verification passed."""

    def to_evidence_context(self) -> dict:
        """Build the evidence_context dict consumed by FieldConfidenceService.

        The 'qr_verified' sub-dict maps extraction field names → QR values.
        Fields listed here are treated as ground-truth by the confidence scorer.
        """
        verified: dict = {}
        if self.doc_number:
            verified["invoice_number"] = self.doc_number
        if self.doc_date:
            verified["invoice_date"] = self.doc_date
        if self.seller_gstin:
            verified["vendor_tax_id"] = self.seller_gstin
        if self.total_value is not None:
            verified["total_amount"] = str(self.total_value)
        return {
            "qr_verified": verified,
            "qr_irn": self.irn,
            "qr_doc_type": self.doc_type,
            "qr_item_count": self.item_count,
            "qr_buyer_gstin": self.buyer_gstin,
        }

    def to_serializable(self) -> dict:
        """JSON-serialisable dict for embedding in raw_response['_qr']."""
        return {
            "irn": self.irn,
            "irn_date": self.irn_date,
            "seller_gstin": self.seller_gstin,
            "buyer_gstin": self.buyer_gstin,
            "doc_number": self.doc_number,
            "doc_date": self.doc_date,
            "total_value": float(self.total_value) if self.total_value is not None else None,
            "item_count": self.item_count,
            "main_hsn": self.main_hsn,
            "doc_type": self.doc_type,
            "decode_strategy": self.decode_strategy,
            "signature_verified": self.signature_verified,
        }


# ── Service ───────────────────────────────────────────────────────────────────

class QRCodeDecoderService:
    """Decode Indian GST e-invoice QR codes. Fail-silent by design."""

    @staticmethod
    def decode(
        file_path: str,
        ocr_text: str = "",
        qr_texts: Optional[List[str]] = None,
    ) -> Optional[QRInvoiceData]:
        """Attempt to decode e-invoice QR data from a document.

        Strategies tried in order (first success wins):
          1. Pre-decoded Azure DI barcode strings (qr_texts param)
          2. OCR text JSON / IRN pattern search (ocr_text param)
          3. pyzbar pixel-level decode from image file (optional dep)

        Args:
            file_path:  Path to the original PDF/image file.
            ocr_text:   Full OCR text returned by Azure DI or native extractor.
            qr_texts:   List of already-decoded QR code strings from Azure DI
                        barcodes API (may be empty or None).

        Returns:
            QRInvoiceData on success, None if no e-invoice QR found or decode fails.
        """
        try:
            # Strategy 1: pre-decoded strings from Azure DI barcodes
            if qr_texts:
                result = QRCodeDecoderService.decode_from_texts(qr_texts)
                if result:
                    return result

            # Strategy 2: search OCR text for IRN / JSON pattern
            if ocr_text:
                result = QRCodeDecoderService._decode_from_ocr_text(ocr_text)
                if result:
                    return result

            # Strategy 3: pixel-level image decode with pyzbar
            if file_path:
                result = QRCodeDecoderService._decode_from_image(file_path)
                if result:
                    return result

            return None

        except Exception as exc:
            logger.debug("QRCodeDecoderService.decode error: %s", exc)
            return None

    @staticmethod
    def decode_from_texts(qr_texts: List[str]) -> Optional[QRInvoiceData]:
        """Parse QR data from a list of pre-decoded strings.

        Suitable for Azure DI barcode API output where the QR has already been
        decoded to text before being passed to the pipeline.

        The Indian IRP QR can be either:
          a. Plain JSON  — older spec (v1.0) or some vendors
          b. Signed JWT  — NIC spec v1.1 (RS256, iss="NIC").
             The invoice data is in payload["data"] as a stringified JSON.
        """
        for text in (qr_texts or []):
            if not text or not text.strip():
                continue
            text = text.strip()
            try:
                # Try unwrapping as a NIC-signed JWT first
                unwrapped = QRCodeDecoderService._unwrap_jwt(text)
                result = QRCodeDecoderService._parse_einvoice_json(
                    unwrapped or text, strategy="azure_barcode"
                )
                if result:
                    return result
            except Exception:
                continue
        return None

    @staticmethod
    def _unwrap_jwt(text: str) -> Optional[str]:
        """If *text* is a JWT, base64-decode the payload and return the inner
        e-invoice JSON string.  Returns None if the text is not a JWT.

        Indian IRP JWTs (iss="NIC") carry the invoice data as a JSON string
        inside payload["data"]:
            {"iss": "NIC", "data": "{\"DocNo\":\"...\", ...}"}
        """
        import base64 as _b64
        parts = text.split(".")
        if len(parts) != 3:
            return None
        try:
            payload_b64 = parts[1]
            # Re-pad for standard base64
            payload_b64 += "=" * (4 - len(payload_b64) % 4)
            payload = json.loads(_b64.urlsafe_b64decode(payload_b64))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        # NIC JWT: data field is the stringified e-invoice JSON
        data = payload.get("data")
        if isinstance(data, str) and data.strip().startswith("{"):
            return data
        # Fallback: maybe the payload itself is the e-invoice dict
        if "Irn" in payload:
            return json.dumps(payload)
        return None

    # ── Strategy 1: OCR text parsing ─────────────────────────────────────────

    @staticmethod
    def _decode_from_ocr_text(ocr_text: str) -> Optional[QRInvoiceData]:
        """Search OCR text for e-invoice JSON containing an IRN field."""

        # Pre-process: join hyphenated line-breaks (PDF word-wrap artefact).
        # e.g. "30853e...a2223-\n7d6768..." → "30853e...a22237d6768..."
        ocr_text = re.sub(r'-\n([0-9a-fA-F])', r'\1', ocr_text)

        # ── Path A: QR JSON payload appeared in OCR text ─────────────────────
        # (happens when Azure DI includes barcode content inline in OCR)
        irn_match = _IRN_RE.search(ocr_text)
        if irn_match:
            irn_pos = irn_match.start()
            # Expand a window around the IRN location
            win_start = max(0, irn_pos - 200)
            win_end = min(len(ocr_text), irn_pos + 2000)
            window = ocr_text[win_start:win_end]

            # Try each JSON-shaped substring in the window
            for m in _JSON_CANDIDATE_RE.finditer(window):
                result = QRCodeDecoderService._parse_einvoice_json(
                    m.group(0), strategy="ocr_text"
                )
                if result:
                    return result

            # Fall back: try each line that starts with '{' and contains "Irn"
            for line in window.splitlines():
                line = line.strip()
                if line.startswith("{") and '"Irn"' in line:
                    result = QRCodeDecoderService._parse_einvoice_json(
                        line, strategy="ocr_text"
                    )
                    if result:
                        return result

        # ── Path B: plain-text IRN label printed on the invoice face ─────────
        # (e.g. "IRN : 30853e...").  This yields only the IRN — no JSON
        # payload — so we build a minimal QRInvoiceData and harvest any
        # GSTIN values from nearby text.
        plain_irn_match = _PLAIN_IRN_RE.search(ocr_text)
        if plain_irn_match:
            irn_val = plain_irn_match.group(1).lower()
            # Collect all GSTINs from the full text; first is usually seller
            all_gstins = _GSTIN_RE.findall(ocr_text)
            seller_gstin = all_gstins[0] if all_gstins else ""
            buyer_gstin = all_gstins[1] if len(all_gstins) > 1 else ""
            return QRInvoiceData(
                irn=irn_val,
                seller_gstin=seller_gstin,
                buyer_gstin=buyer_gstin,
                decode_strategy="ocr_irn_text",
            )

        return None

    # ── Strategy 2: pyzbar pixel decode ──────────────────────────────────────

    @staticmethod
    def _decode_from_image(file_path: str) -> Optional[QRInvoiceData]:
        """Decode QR code bytes directly from an image using pyzbar.

        Requires: pip install pyzbar Pillow
        Also needs PyMuPDF (fitz) or pdf2image for PDF inputs.
        All imports are conditional — returns None silently if libs absent.
        """
        try:
            from pyzbar.pyzbar import decode as pyzbar_decode  # type: ignore
            from PIL import Image  # type: ignore
        except ImportError:
            logger.debug("pyzbar/Pillow not installed — skipping image QR decode")
            return None

        images = QRCodeDecoderService._file_to_pil_images(file_path)
        for img in images:
            try:
                barcodes = pyzbar_decode(img)
            except Exception as exc:
                logger.debug("pyzbar decode error: %s", exc)
                continue
            for bc in barcodes:
                if getattr(bc, "type", "") != "QRCODE":
                    continue
                try:
                    text = bc.data.decode("utf-8", errors="replace")
                except Exception:
                    continue
                result = QRCodeDecoderService._parse_einvoice_json(
                    text, strategy="pyzbar"
                )
                if result:
                    return result

        return None

    @staticmethod
    def _file_to_pil_images(file_path: str) -> list:
        """Convert file to a list of PIL Image objects (first 2 pages only)."""
        try:
            from PIL import Image  # type: ignore
        except ImportError:
            return []

        if not file_path.lower().endswith(".pdf"):
            try:
                return [Image.open(file_path)]
            except Exception:
                return []

        # PDF: try PyMuPDF first, then pdf2image
        try:
            import fitz  # type: ignore  # PyMuPDF
            import io
            doc = fitz.open(file_path)
            imgs = []
            for page_num in range(min(2, len(doc))):
                pix = doc[page_num].get_pixmap(dpi=150)
                imgs.append(Image.open(io.BytesIO(pix.tobytes("png"))))
            return imgs
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("PyMuPDF page render error: %s", exc)

        try:
            from pdf2image import convert_from_path  # type: ignore
            return convert_from_path(
                file_path, dpi=150, first_page=1, last_page=2
            )
        except ImportError:
            logger.debug("pdf2image not installed — cannot decode QR from PDF via pyzbar")
        except Exception as exc:
            logger.debug("pdf2image error: %s", exc)

        return []

    # ── JSON parsing ─────────────────────────────────────────────────────────

    @staticmethod
    def _parse_einvoice_json(text: str, strategy: str) -> Optional[QRInvoiceData]:
        """Parse raw text as an e-invoice QR JSON payload.

        Returns QRInvoiceData only when the payload contains a valid 64-char IRN.
        """
        if not text or not text.strip():
            return None
        try:
            payload = json.loads(text.strip())
        except (json.JSONDecodeError, ValueError):
            return None

        if not isinstance(payload, dict):
            return None

        irn = payload.get("Irn", "")
        if not isinstance(irn, str) or len(irn) != 64:
            return None  # Not a valid e-invoice QR

        total_value: Optional[Decimal] = None
        raw_total = payload.get("TotInvVal")
        if raw_total is not None:
            try:
                total_value = Decimal(str(raw_total))
            except InvalidOperation:
                pass

        item_count = 0
        try:
            item_count = int(payload.get("ItemCnt", 0) or 0)
        except (ValueError, TypeError):
            pass

        return QRInvoiceData(
            irn=irn,
            irn_date=str(payload.get("IrnDt", "")),
            seller_gstin=str(payload.get("SellerGstin", "")).upper(),
            buyer_gstin=str(payload.get("BuyerGstin", "")).upper(),
            doc_number=str(payload.get("DocNo", "")),
            doc_date=str(payload.get("DocDt", "")),
            total_value=total_value,
            item_count=item_count,
            main_hsn=str(payload.get("MainHsnCode", "")),
            doc_type=str(payload.get("DocTyp", "INV")).upper(),
            raw_payload=payload,
            decode_strategy=strategy,
        )

    # ── Normalisation helpers ─────────────────────────────────────────────────

    @staticmethod
    def normalise_invoice_number(value: str) -> str:
        """Strip spaces / dashes / slashes; uppercase — for comparison."""
        return _NORM_INV_RE.sub("", value).upper()

    @staticmethod
    def normalise_gstin(value: str) -> str:
        """Uppercase and strip whitespace."""
        return value.strip().upper()
