from unittest.mock import patch

from django.test import TestCase

from apps.benchmarking.agents.Line_Item_Understanding_Agent_BM import (
    BenchmarkLineItemUnderstandingAgentBM,
)
from apps.benchmarking.models import BenchmarkLineItem, BenchmarkQuotation, BenchmarkRequest


class LineItemUnderstandingAgentTests(TestCase):
    def setUp(self):
        self.request = BenchmarkRequest.objects.create(
            title="Understanding Agent Test",
            geography="UAE",
            scope_type="SITC",
            status="PENDING",
        )
        self.quotation = BenchmarkQuotation.objects.create(
            request=self.request,
            supplier_name="",
            quotation_ref="QT-UNDER-001",
            extraction_status="DONE",
            extracted_text=(
                "Supplier Quotation - Al Najah HVAC Trading LLC\n"
                "Quotation Ref: QT-UNDER-001\n"
                "AHU 4500 CFM with VFD\n"
                "VAT 5% AED 11,115.00\n"
            ),
        )
        self.good_item = BenchmarkLineItem.objects.create(
            quotation=self.quotation,
            description="AHU 4500 CFM with VFD",
            line_number=1,
            quantity=4,
            quoted_unit_rate=12850,
            line_amount=51400,
            extraction_confidence=0.7,
        )
        self.noise_item = BenchmarkLineItem.objects.create(
            quotation=self.quotation,
            description="AED 11,115.00",
            line_number=2,
            quantity=5,
            quoted_unit_rate=5,
            line_amount=11115,
            extraction_confidence=0.4,
        )

    @patch(
        "apps.benchmarking.agents.Line_Item_Understanding_Agent_BM.BenchmarkLineItemUnderstandingAgentBM._understand_with_llm"
    )
    def test_understanding_agent_filters_noise_and_sets_supplier(self, llm_mock):
        llm_mock.return_value = {
            "supplier_name": "Al Najah HVAC Trading LLC",
            "normalized_lines": [
                {
                    "line_pk": self.good_item.pk,
                    "keep": True,
                    "normalized_description": "AHU 4500 CFM with VFD",
                    "uom": "NOS",
                    "quantity": 4,
                    "quoted_unit_rate": 12850,
                    "line_amount": 51400,
                    "confidence": 0.95,
                    "drop_reason": "",
                },
                {
                    "line_pk": self.noise_item.pk,
                    "keep": False,
                    "normalized_description": "AED 11,115.00",
                    "uom": "",
                    "quantity": "",
                    "quoted_unit_rate": "",
                    "line_amount": "",
                    "confidence": 0.99,
                    "drop_reason": "vat_footer",
                },
            ],
        }

        result = BenchmarkLineItemUnderstandingAgentBM.understand_request(
            quotations=[self.quotation]
        )

        self.assertEqual(result["kept_lines"], 1)
        self.assertEqual(result["dropped_lines"], 1)

        self.quotation.refresh_from_db()
        self.good_item.refresh_from_db()
        self.noise_item.refresh_from_db()

        self.assertEqual(self.quotation.supplier_name, "Al Najah HVAC Trading LLC")
        self.assertTrue(self.good_item.is_active)
        self.assertEqual(self.good_item.uom, "NOS")
        self.assertFalse(self.noise_item.is_active)
