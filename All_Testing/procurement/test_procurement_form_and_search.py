from __future__ import annotations

from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from apps.procurement.agents.Procurement_Form_Filling_Agent import ProcurementFormFillingAgent
from apps.procurement.services.web_search_service import WebSearchService


class ProcurementFormFillingAgentTests(SimpleTestCase):
    def test_fill_form_with_canonical_fields_positive(self):
        payload = {
            "confidence": 0.91,
            "fields": {
                "Project Name": {"value": "Mall HVAC Upgrade", "confidence": 0.88},
                "Country": "UAE",
            },
            "attributes": [{"key": "Store Type", "value": "MALL", "confidence": 0.8}],
            "requirements": ["VRF system with inverter compressors"],
        }

        result = ProcurementFormFillingAgent.fill_form(
            extraction_output=payload,
            source_doc_type="hvac_request_form",
        )

        self.assertEqual(result["agent"], "procurement_form_filling")
        self.assertEqual(result["fields"]["project_name"]["value"], "Mall HVAC Upgrade")
        self.assertEqual(result["fields"]["country"]["value"], "UAE")
        self.assertEqual(result["attributes"][0]["key"], "store_type")
        self.assertEqual(result["requirements"][0]["value"], "VRF system with inverter compressors")
        self.assertEqual(result["source_doc_type"], "hvac_request_form")

    def test_fill_form_with_di_shape_positive(self):
        payload = {
            "confidence": 0.77,
            "header": {
                "Project Name": "Warehouse Cooling",
                "Country": "KSA",
            },
            "key_value_pairs": [
                {"key": "Budget", "value": "250000", "confidence": 0.7},
            ],
            "line_items": [
                {"description": "Packaged DX rooftop units", "confidence": 0.65},
            ],
            "commercial_terms": {
                "Lead Time": "8 weeks",
            },
        }

        result = ProcurementFormFillingAgent.fill_form(extraction_output=payload)

        self.assertEqual(result["fields"]["project_name"]["value"], "Warehouse Cooling")
        self.assertEqual(result["fields"]["budget"]["value"], "250000")
        self.assertEqual(result["attributes"][0]["key"], "lead_time")
        self.assertEqual(result["requirements"][0]["key"], "requirement_1")

    def test_fill_form_negative_handles_empty_input(self):
        result = ProcurementFormFillingAgent.fill_form(extraction_output={})

        self.assertEqual(result["fields"], {})
        self.assertEqual(result["attributes"], [])
        self.assertEqual(result["requirements"], [])
        self.assertEqual(result["confidence"], 0.5)


class WebSearchServiceTests(SimpleTestCase):
    @patch("apps.procurement.services.web_search_service.requests.get")
    def test_search_product_info_positive_extracts_dynamic_prices(self, mock_get):
        html = """
        <html><body>
            <div class='result'>
              <a class='result__a' href='https://example.com/1'>VRF System Offer AED 12500</a>
              <a class='result__snippet'>Indicative installed cost AED 14500 in UAE market.</a>
            </div>
            <div class='result'>
              <a class='result__a' href='https://example.com/2'>Commercial HVAC AED 16000</a>
              <a class='result__snippet'>Supplier range AED 15000 with service package.</a>
            </div>
        </body></html>
        """
        mock_response = Mock()
        mock_response.text = html
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = WebSearchService.search_product_info(
            system_type="VRF_SYSTEM",
            geography="UAE",
            currency="AED",
        )

        self.assertEqual(result["source"], "WEB_DYNAMIC_SEARCH")
        self.assertTrue(len(result["snippets"]) >= 2)
        self.assertEqual(result["pricing"]["min"], 12500.0)
        self.assertEqual(result["pricing"]["max"], 16000.0)
        self.assertEqual(result["pricing"]["basis"], "dynamic_web_search")

    @patch("apps.procurement.services.web_search_service.requests.get")
    def test_search_market_rate_positive_returns_market_fields(self, mock_get):
        html = """
        <html><body>
            <div class='result'>
              <a class='result__a' href='https://example.com/rate'>Packaged DX AED 21000</a>
              <a class='result__snippet'>Installed package cost AED 23000 per unit.</a>
            </div>
        </body></html>
        """
        mock_response = Mock()
        mock_response.text = html
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        result = WebSearchService.search_market_rate(
            description="Packaged DX rooftop unit",
            geography="UAE",
            uom="unit",
            currency="AED",
        )

        self.assertEqual(result["market_min"], 21000.0)
        self.assertEqual(result["market_max"], 23000.0)
        self.assertEqual(result["currency"], "AED")

    @patch("apps.procurement.services.web_search_service.requests.get")
    def test_search_product_info_negative_handles_fetch_failure(self, mock_get):
        mock_get.side_effect = RuntimeError("network down")

        result = WebSearchService.search_product_info(
            system_type="VRF_SYSTEM",
            geography="UAE",
            currency="AED",
        )

        self.assertEqual(result["pricing"]["min"], None)
        self.assertEqual(result["pricing"]["avg"], None)
        self.assertEqual(result["pricing"]["max"], None)
        self.assertEqual(result["pricing"]["basis"], "dynamic_web_failed")
        self.assertIn("Fetch error", result["notes"])
