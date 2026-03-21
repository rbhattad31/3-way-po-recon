"""
Management command to seed jurisdiction profiles, schemas, and field definitions.

Usage:
    python manage.py seed_extraction_config
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.extraction_configs.models import NormalizationProfile, TaxFieldDefinition
from apps.extraction_core.models import ExtractionSchemaDefinition, TaxJurisdictionProfile


# ---------------------------------------------------------------------------
# Jurisdiction profiles
# ---------------------------------------------------------------------------

JURISDICTIONS = [
    {
        "country_code": "IN",
        "country_name": "India",
        "tax_regime": "GST",
        "regime_full_name": "Goods and Services Tax",
        "default_currency": "INR",
        "tax_id_label": "GSTIN",
        "tax_id_regex": r"\b\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z][A-Z\d]\b",
        "date_formats": ["DD/MM/YYYY", "DD-MM-YYYY", "DD-MMM-YYYY"],
        "locale_code": "en_IN",
        "fiscal_year_start_month": 4,
        "config_json": {
            "reverse_charge_supported": True,
            "e_invoicing_mandatory": True,
            "has_state_level_tax": True,
            "tax_components": ["CGST", "SGST", "IGST", "CESS"],
        },
    },
    {
        "country_code": "AE",
        "country_name": "United Arab Emirates",
        "tax_regime": "VAT",
        "regime_full_name": "Value Added Tax",
        "default_currency": "AED",
        "tax_id_label": "TRN",
        "tax_id_regex": r"\b\d{15}\b",
        "date_formats": ["DD/MM/YYYY", "MM/DD/YYYY", "YYYY-MM-DD"],
        "locale_code": "en_AE",
        "fiscal_year_start_month": 1,
        "config_json": {
            "reverse_charge_supported": True,
            "e_invoicing_mandatory": False,
            "standard_vat_rate": 5.0,
            "tax_components": ["VAT"],
        },
    },
    {
        "country_code": "SA",
        "country_name": "Saudi Arabia",
        "tax_regime": "VAT",
        "regime_full_name": "Value Added Tax (ZATCA)",
        "default_currency": "SAR",
        "tax_id_label": "VAT ID",
        "tax_id_regex": r"\b3\d{14}\b",
        "date_formats": ["DD/MM/YYYY", "YYYY-MM-DD"],
        "locale_code": "ar_SA",
        "fiscal_year_start_month": 1,
        "config_json": {
            "reverse_charge_supported": True,
            "e_invoicing_mandatory": True,
            "zatca_compliant": True,
            "standard_vat_rate": 15.0,
            "tax_components": ["VAT"],
        },
    },
]

# ---------------------------------------------------------------------------
# Normalization profiles
# ---------------------------------------------------------------------------

NORMALIZATION_PROFILES = {
    "IN": {
        "date_input_formats": ["DD/MM/YYYY", "DD-MM-YYYY", "DD-MMM-YYYY", "DD.MM.YYYY"],
        "date_output_format": "YYYY-MM-DD",
        "decimal_separator": ".",
        "thousands_separator": ",",
        "currency_symbol": "₹",
    },
    "AE": {
        "date_input_formats": ["DD/MM/YYYY", "MM/DD/YYYY", "YYYY-MM-DD"],
        "date_output_format": "YYYY-MM-DD",
        "decimal_separator": ".",
        "thousands_separator": ",",
        "currency_symbol": "AED",
    },
    "SA": {
        "date_input_formats": ["DD/MM/YYYY", "YYYY-MM-DD"],
        "date_output_format": "YYYY-MM-DD",
        "decimal_separator": ".",
        "thousands_separator": ",",
        "currency_symbol": "SAR",
    },
}

# ---------------------------------------------------------------------------
# Schema definitions (one per jurisdiction for INVOICE doc type)
# ---------------------------------------------------------------------------

SCHEMAS = {
    "IN": {
        "name": "India GST Invoice Schema",
        "document_type": "INVOICE",
        "schema_version": "1.0",
        "header_fields_json": [
            "invoice_number", "invoice_date", "due_date",
            "supplier_name", "supplier_gstin", "supplier_address",
            "buyer_name", "buyer_gstin", "buyer_address",
            "place_of_supply", "po_number", "currency",
            "total_amount", "total_taxable_amount", "total_tax_amount",
            "grand_total", "amount_in_words",
        ],
        "line_item_fields_json": [
            "item_description", "hsn_sac_code", "quantity", "unit",
            "unit_price", "taxable_amount",
            "cgst_rate", "cgst_amount",
            "sgst_rate", "sgst_amount",
            "igst_rate", "igst_amount",
            "cess_rate", "cess_amount",
            "line_total",
        ],
        "tax_fields_json": [
            "total_cgst", "total_sgst", "total_igst", "total_cess",
            "is_reverse_charge", "supply_type",
        ],
    },
    "AE": {
        "name": "UAE VAT Invoice Schema",
        "document_type": "INVOICE",
        "schema_version": "1.0",
        "header_fields_json": [
            "invoice_number", "invoice_date", "due_date",
            "supplier_name", "supplier_trn", "supplier_address",
            "buyer_name", "buyer_trn", "buyer_address",
            "po_number", "currency",
            "total_amount", "total_taxable_amount", "total_vat_amount",
            "grand_total", "amount_in_words",
        ],
        "line_item_fields_json": [
            "item_description", "quantity", "unit",
            "unit_price", "taxable_amount",
            "vat_rate", "vat_amount",
            "line_total",
        ],
        "tax_fields_json": [
            "total_vat", "is_reverse_charge",
        ],
    },
    "SA": {
        "name": "Saudi VAT Invoice Schema (ZATCA)",
        "document_type": "INVOICE",
        "schema_version": "1.0",
        "header_fields_json": [
            "invoice_number", "invoice_date", "due_date",
            "supplier_name", "supplier_vat_id", "supplier_address",
            "buyer_name", "buyer_vat_id", "buyer_address",
            "po_number", "currency",
            "total_amount", "total_taxable_amount", "total_vat_amount",
            "grand_total", "amount_in_words",
        ],
        "line_item_fields_json": [
            "item_description", "quantity", "unit",
            "unit_price", "taxable_amount",
            "vat_rate", "vat_amount",
            "line_total",
        ],
        "tax_fields_json": [
            "total_vat", "is_reverse_charge",
        ],
    },
}

# ---------------------------------------------------------------------------
# Common + jurisdiction-specific field definitions
# ---------------------------------------------------------------------------

COMMON_FIELDS = [
    # Header fields
    {"field_key": "invoice_number", "display_name": "Invoice Number", "data_type": "STRING", "category": "HEADER", "is_mandatory": True, "sort_order": 1, "aliases": ["inv no", "invoice no", "bill number", "bill no"]},
    {"field_key": "invoice_date", "display_name": "Invoice Date", "data_type": "DATE", "category": "HEADER", "is_mandatory": True, "sort_order": 2, "aliases": ["inv date", "date of invoice", "bill date"]},
    {"field_key": "due_date", "display_name": "Due Date", "data_type": "DATE", "category": "HEADER", "is_mandatory": False, "sort_order": 3, "aliases": ["payment due date", "due by"]},
    {"field_key": "po_number", "display_name": "PO Number", "data_type": "STRING", "category": "HEADER", "is_mandatory": False, "sort_order": 4, "aliases": ["purchase order", "po no", "po ref"]},
    {"field_key": "currency", "display_name": "Currency", "data_type": "STRING", "category": "HEADER", "is_mandatory": False, "sort_order": 5, "aliases": ["ccy", "currency code"]},
    {"field_key": "total_amount", "display_name": "Total Amount", "data_type": "CURRENCY", "category": "HEADER", "is_mandatory": True, "sort_order": 10, "aliases": ["net amount", "subtotal"]},
    {"field_key": "total_taxable_amount", "display_name": "Total Taxable Amount", "data_type": "CURRENCY", "category": "HEADER", "is_mandatory": False, "sort_order": 11, "aliases": ["taxable value", "assessable value"]},
    {"field_key": "total_tax_amount", "display_name": "Total Tax Amount", "data_type": "CURRENCY", "category": "HEADER", "is_mandatory": False, "sort_order": 12, "aliases": ["tax total", "total tax"]},
    {"field_key": "grand_total", "display_name": "Grand Total", "data_type": "CURRENCY", "category": "HEADER", "is_mandatory": True, "sort_order": 13, "aliases": ["invoice total", "total payable", "amount due"]},
    {"field_key": "amount_in_words", "display_name": "Amount in Words", "data_type": "STRING", "category": "HEADER", "is_mandatory": False, "sort_order": 14, "aliases": ["total in words"]},
    # Party fields
    {"field_key": "supplier_name", "display_name": "Supplier Name", "data_type": "STRING", "category": "PARTY", "is_mandatory": True, "sort_order": 1, "aliases": ["vendor name", "seller name", "from"]},
    {"field_key": "supplier_address", "display_name": "Supplier Address", "data_type": "ADDRESS", "category": "PARTY", "is_mandatory": False, "sort_order": 2, "aliases": ["vendor address", "seller address"]},
    {"field_key": "buyer_name", "display_name": "Buyer Name", "data_type": "STRING", "category": "PARTY", "is_mandatory": False, "sort_order": 5, "aliases": ["customer name", "bill to", "to"]},
    {"field_key": "buyer_address", "display_name": "Buyer Address", "data_type": "ADDRESS", "category": "PARTY", "is_mandatory": False, "sort_order": 6, "aliases": ["customer address", "bill to address"]},
    # Line item fields
    {"field_key": "item_description", "display_name": "Item Description", "data_type": "STRING", "category": "LINE_ITEM", "is_mandatory": True, "sort_order": 1, "aliases": ["description", "particulars", "item name"]},
    {"field_key": "quantity", "display_name": "Quantity", "data_type": "DECIMAL", "category": "LINE_ITEM", "is_mandatory": True, "sort_order": 2, "aliases": ["qty"]},
    {"field_key": "unit", "display_name": "Unit of Measure", "data_type": "STRING", "category": "LINE_ITEM", "is_mandatory": False, "sort_order": 3, "aliases": ["uom", "unit"]},
    {"field_key": "unit_price", "display_name": "Unit Price", "data_type": "CURRENCY", "category": "LINE_ITEM", "is_mandatory": True, "sort_order": 4, "aliases": ["rate", "price"]},
    {"field_key": "taxable_amount", "display_name": "Taxable Amount", "data_type": "CURRENCY", "category": "LINE_ITEM", "is_mandatory": False, "sort_order": 5, "aliases": ["taxable value"]},
    {"field_key": "line_total", "display_name": "Line Total", "data_type": "CURRENCY", "category": "LINE_ITEM", "is_mandatory": False, "sort_order": 10, "aliases": ["amount", "total"]},
    # Tax fields (common)
    {"field_key": "is_reverse_charge", "display_name": "Reverse Charge", "data_type": "BOOLEAN", "category": "TAX", "is_tax_field": True, "sort_order": 50, "aliases": ["reverse charge applicable", "rcm"]},
]

INDIA_FIELDS = [
    {"field_key": "supplier_gstin", "display_name": "Supplier GSTIN", "data_type": "TAX_ID", "category": "PARTY", "is_mandatory": True, "is_tax_field": True, "sort_order": 3, "validation_regex": r"\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z][A-Z\d]", "aliases": ["gstin", "gst no", "gst number"]},
    {"field_key": "buyer_gstin", "display_name": "Buyer GSTIN", "data_type": "TAX_ID", "category": "PARTY", "is_mandatory": False, "is_tax_field": True, "sort_order": 7, "validation_regex": r"\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z][A-Z\d]", "aliases": ["buyer gst", "customer gstin"]},
    {"field_key": "place_of_supply", "display_name": "Place of Supply", "data_type": "STRING", "category": "HEADER", "is_mandatory": False, "sort_order": 6, "aliases": ["pos", "state code"]},
    {"field_key": "hsn_sac_code", "display_name": "HSN/SAC Code", "data_type": "STRING", "category": "LINE_ITEM", "is_tax_field": True, "sort_order": 1, "aliases": ["hsn", "sac", "hsn code", "sac code"]},
    {"field_key": "cgst_rate", "display_name": "CGST Rate (%)", "data_type": "PERCENTAGE", "category": "LINE_ITEM", "is_tax_field": True, "sort_order": 6},
    {"field_key": "cgst_amount", "display_name": "CGST Amount", "data_type": "CURRENCY", "category": "LINE_ITEM", "is_tax_field": True, "sort_order": 7},
    {"field_key": "sgst_rate", "display_name": "SGST Rate (%)", "data_type": "PERCENTAGE", "category": "LINE_ITEM", "is_tax_field": True, "sort_order": 8},
    {"field_key": "sgst_amount", "display_name": "SGST Amount", "data_type": "CURRENCY", "category": "LINE_ITEM", "is_tax_field": True, "sort_order": 9},
    {"field_key": "igst_rate", "display_name": "IGST Rate (%)", "data_type": "PERCENTAGE", "category": "LINE_ITEM", "is_tax_field": True, "sort_order": 10},
    {"field_key": "igst_amount", "display_name": "IGST Amount", "data_type": "CURRENCY", "category": "LINE_ITEM", "is_tax_field": True, "sort_order": 11},
    {"field_key": "cess_rate", "display_name": "Cess Rate (%)", "data_type": "PERCENTAGE", "category": "LINE_ITEM", "is_tax_field": True, "sort_order": 12},
    {"field_key": "cess_amount", "display_name": "Cess Amount", "data_type": "CURRENCY", "category": "LINE_ITEM", "is_tax_field": True, "sort_order": 13},
    {"field_key": "total_cgst", "display_name": "Total CGST", "data_type": "CURRENCY", "category": "TAX", "is_tax_field": True, "sort_order": 1},
    {"field_key": "total_sgst", "display_name": "Total SGST", "data_type": "CURRENCY", "category": "TAX", "is_tax_field": True, "sort_order": 2},
    {"field_key": "total_igst", "display_name": "Total IGST", "data_type": "CURRENCY", "category": "TAX", "is_tax_field": True, "sort_order": 3},
    {"field_key": "total_cess", "display_name": "Total Cess", "data_type": "CURRENCY", "category": "TAX", "is_tax_field": True, "sort_order": 4},
    {"field_key": "supply_type", "display_name": "Supply Type", "data_type": "ENUM", "category": "TAX", "is_tax_field": True, "sort_order": 5, "aliases": ["type of supply"], "validation_rules_json": {"allowed_values": ["B2B", "B2C", "EXPORT", "SEZ"]}},
]

UAE_FIELDS = [
    {"field_key": "supplier_trn", "display_name": "Supplier TRN", "data_type": "TAX_ID", "category": "PARTY", "is_mandatory": True, "is_tax_field": True, "sort_order": 3, "validation_regex": r"\d{15}", "aliases": ["trn", "tax registration number"]},
    {"field_key": "buyer_trn", "display_name": "Buyer TRN", "data_type": "TAX_ID", "category": "PARTY", "is_mandatory": False, "is_tax_field": True, "sort_order": 7, "aliases": ["buyer trn", "customer trn"]},
    {"field_key": "vat_rate", "display_name": "VAT Rate (%)", "data_type": "PERCENTAGE", "category": "LINE_ITEM", "is_tax_field": True, "sort_order": 6},
    {"field_key": "vat_amount", "display_name": "VAT Amount", "data_type": "CURRENCY", "category": "LINE_ITEM", "is_tax_field": True, "sort_order": 7},
    {"field_key": "total_vat", "display_name": "Total VAT", "data_type": "CURRENCY", "category": "TAX", "is_tax_field": True, "sort_order": 1, "aliases": ["vat total"]},
    {"field_key": "total_vat_amount", "display_name": "Total VAT Amount (Header)", "data_type": "CURRENCY", "category": "HEADER", "is_tax_field": True, "sort_order": 12, "aliases": ["total vat"]},
]

SA_FIELDS = [
    {"field_key": "supplier_vat_id", "display_name": "Supplier VAT ID", "data_type": "TAX_ID", "category": "PARTY", "is_mandatory": True, "is_tax_field": True, "sort_order": 3, "validation_regex": r"3\d{14}", "aliases": ["vat id", "vat number", "vat registration"]},
    {"field_key": "buyer_vat_id", "display_name": "Buyer VAT ID", "data_type": "TAX_ID", "category": "PARTY", "is_mandatory": False, "is_tax_field": True, "sort_order": 7, "aliases": ["buyer vat", "customer vat"]},
    {"field_key": "vat_rate", "display_name": "VAT Rate (%)", "data_type": "PERCENTAGE", "category": "LINE_ITEM", "is_tax_field": True, "sort_order": 6},
    {"field_key": "vat_amount", "display_name": "VAT Amount", "data_type": "CURRENCY", "category": "LINE_ITEM", "is_tax_field": True, "sort_order": 7},
    {"field_key": "total_vat", "display_name": "Total VAT", "data_type": "CURRENCY", "category": "TAX", "is_tax_field": True, "sort_order": 1, "aliases": ["vat total"]},
    {"field_key": "total_vat_amount", "display_name": "Total VAT Amount (Header)", "data_type": "CURRENCY", "category": "HEADER", "is_tax_field": True, "sort_order": 12, "aliases": ["total vat"]},
]


class Command(BaseCommand):
    help = "Seed jurisdiction profiles, extraction schemas, normalization profiles, and field definitions."

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write("Seeding extraction configuration...")

        # 1 — Jurisdiction profiles
        jurisdiction_map = {}
        for j_data in JURISDICTIONS:
            j, created = TaxJurisdictionProfile.objects.update_or_create(
                country_code=j_data["country_code"],
                tax_regime=j_data["tax_regime"],
                defaults=j_data,
            )
            jurisdiction_map[j.country_code] = j
            tag = "CREATED" if created else "UPDATED"
            self.stdout.write(f"  [{tag}] Jurisdiction: {j}")

        # 2 — Normalization profiles
        for cc, norm_data in NORMALIZATION_PROFILES.items():
            jurisdiction = jurisdiction_map.get(cc)
            if not jurisdiction:
                continue
            np, created = NormalizationProfile.objects.update_or_create(
                jurisdiction=jurisdiction,
                defaults=norm_data,
            )
            tag = "CREATED" if created else "UPDATED"
            self.stdout.write(f"  [{tag}] Normalization Profile: {np}")

        # 3 — Schemas
        schema_map = {}
        for cc, s_data in SCHEMAS.items():
            jurisdiction = jurisdiction_map.get(cc)
            if not jurisdiction:
                continue
            s, created = ExtractionSchemaDefinition.objects.update_or_create(
                jurisdiction=jurisdiction,
                document_type=s_data["document_type"],
                schema_version=s_data["schema_version"],
                defaults={
                    "name": s_data["name"],
                    "header_fields_json": s_data["header_fields_json"],
                    "line_item_fields_json": s_data["line_item_fields_json"],
                    "tax_fields_json": s_data["tax_fields_json"],
                },
            )
            schema_map[cc] = s
            tag = "CREATED" if created else "UPDATED"
            self.stdout.write(f"  [{tag}] Schema: {s}")

        # 4 — Field definitions
        all_schemas = list(schema_map.values())

        # Common fields → linked to ALL schemas
        common_count = self._seed_fields(COMMON_FIELDS, all_schemas)
        self.stdout.write(f"  Common fields: {common_count}")

        # India-specific → linked to India schema only
        if "IN" in schema_map:
            india_count = self._seed_fields(INDIA_FIELDS, [schema_map["IN"]])
            self.stdout.write(f"  India-specific fields: {india_count}")

        # UAE-specific → linked to UAE schema only
        if "AE" in schema_map:
            uae_count = self._seed_fields(UAE_FIELDS, [schema_map["AE"]])
            self.stdout.write(f"  UAE-specific fields: {uae_count}")

        # Saudi-specific → linked to Saudi schema only
        if "SA" in schema_map:
            sa_count = self._seed_fields(SA_FIELDS, [schema_map["SA"]])
            self.stdout.write(f"  Saudi-specific fields: {sa_count}")

        self.stdout.write(self.style.SUCCESS("Extraction configuration seeded successfully!"))

    def _seed_fields(self, field_list: list[dict], schemas: list) -> int:
        count = 0
        for f_data in field_list:
            field_key = f_data["field_key"]
            defaults = {
                "display_name": f_data.get("display_name", field_key),
                "data_type": f_data.get("data_type", "STRING"),
                "category": f_data.get("category", "HEADER"),
                "is_mandatory": f_data.get("is_mandatory", False),
                "is_tax_field": f_data.get("is_tax_field", False),
                "sort_order": f_data.get("sort_order", 0),
            }
            if "validation_regex" in f_data:
                defaults["validation_regex"] = f_data["validation_regex"]
            if "validation_rules_json" in f_data:
                defaults["validation_rules_json"] = f_data["validation_rules_json"]
            if "aliases" in f_data:
                defaults["aliases"] = f_data["aliases"]

            fd, _ = TaxFieldDefinition.objects.update_or_create(
                field_key=field_key,
                defaults=defaults,
            )
            # Link to schemas (additive, won't remove existing links)
            fd.schemas.add(*schemas)
            count += 1
        return count
