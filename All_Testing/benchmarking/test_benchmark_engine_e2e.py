from unittest.mock import patch

from django.test import TestCase

from apps.benchmarking.models import (
    BenchmarkCorridorRule,
    BenchmarkLineItem,
    BenchmarkQuotation,
    BenchmarkRequest,
    CategoryMaster,
)
from apps.benchmarking.services.benchmark_service import BenchmarkEngine


class BenchmarkEngineE2ETests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.req = BenchmarkRequest.objects.create(
            title="Benchmark E2E Duplicate Line Routing",
            geography="UAE",
            scope_type="SITC",
            status="PENDING",
        )
        cls.quotation = BenchmarkQuotation.objects.create(
            request=cls.req,
            supplier_name="Vendor A",
            quotation_ref="QT-E2E-001",
            extraction_status="DONE",
        )

        CategoryMaster.objects.bulk_create([
            CategoryMaster(code="EQUIPMENT", name="Equipment", keywords_csv="chiller,centrifugal chiller", sort_order=1, is_active=True),
            CategoryMaster(code="DUCTING", name="Ducting", keywords_csv="copper piping,insulated piping,piping", sort_order=2, is_active=True),
            CategoryMaster(code="CONTROLS", name="Controls", keywords_csv="bms,controls,sensors", sort_order=3, is_active=True),
            CategoryMaster(code="INSTALLATION", name="Installation", keywords_csv="installation,commissioning,testing", sort_order=4, is_active=True),
        ])

        BenchmarkCorridorRule.objects.bulk_create([
            BenchmarkCorridorRule(
                rule_code="BC-INSUL-ALL-001",
                name="Insulated piping",
                category="DUCTING",
                scope_type="ALL",
                geography="ALL",
                min_rate=18,
                mid_rate=26,
                max_rate=40,
                priority=100,
                is_active=True,
            ),
            BenchmarkCorridorRule(
                rule_code="BC-CTRL-ALL-001",
                name="Controls",
                category="CONTROLS",
                scope_type="ALL",
                geography="ALL",
                min_rate=20000,
                mid_rate=28000,
                max_rate=42000,
                priority=100,
                is_active=True,
            ),
            BenchmarkCorridorRule(
                rule_code="BC-INST-ALL-001",
                name="Installation",
                category="INSTALLATION",
                scope_type="ALL",
                geography="ALL",
                min_rate=25000,
                mid_rate=35000,
                max_rate=50000,
                priority=100,
                is_active=True,
            ),
        ])

    def setUp(self):
        BenchmarkLineItem.objects.all().delete()
        self.items = [
            BenchmarkLineItem.objects.create(
                quotation=self.quotation,
                description="Central Air Cooled Centrifugal Chiller 500 TR",
                line_number=0,
                quantity=1,
                quoted_unit_rate=25000,
                line_amount=25000,
                category="EQUIPMENT",
                benchmark_min=1,
                benchmark_mid=2,
                benchmark_max=3,
                corridor_rule_code="STALE-RULE",
                benchmark_source="CORRIDOR_DB",
                variance_pct=99.0,
                variance_status="HIGH",
            ),
            BenchmarkLineItem.objects.create(
                quotation=self.quotation,
                description="Copper Piping 1.5 inch insulated",
                line_number=0,
                quantity=500,
                quoted_unit_rate=150,
                line_amount=75000,
                category="DUCTING",
            ),
            BenchmarkLineItem.objects.create(
                quotation=self.quotation,
                description="BMS Controls System with sensors",
                line_number=0,
                quantity=1,
                quoted_unit_rate=8500,
                line_amount=8500,
                category="CONTROLS",
            ),
            BenchmarkLineItem.objects.create(
                quotation=self.quotation,
                description="Installation and commissioning services",
                line_number=0,
                quantity=200,
                quoted_unit_rate=350,
                line_amount=70000,
                category="INSTALLATION",
            ),
        ]

    def _run_engine(self):
        with patch.object(BenchmarkEngine, "_start_agent_run", return_value=None), \
             patch.object(BenchmarkEngine, "_complete_agent_run", return_value=None), \
             patch.object(BenchmarkEngine, "_fail_agent_run", return_value=None):
            return BenchmarkEngine.run(self.req.pk)

    def test_routes_duplicate_line_numbers_by_position(self):
        result = self._run_engine()

        self.assertTrue(result["success"])

        refreshed = list(self.quotation.line_items.order_by("id"))
        equipment, ducting, controls, installation = refreshed

        self.assertEqual(equipment.category, "EQUIPMENT")
        self.assertEqual(equipment.benchmark_source, "NONE")
        self.assertEqual(equipment.corridor_rule_code, "")
        self.assertIsNone(equipment.benchmark_mid)
        self.assertIsNone(equipment.variance_pct)
        self.assertEqual(equipment.variance_status, "NEEDS_REVIEW")

        self.assertEqual(ducting.corridor_rule_code, "BC-INSUL-ALL-001")
        self.assertEqual(float(ducting.benchmark_mid), 26.0)
        self.assertEqual(ducting.variance_status, "HIGH")

        self.assertEqual(controls.corridor_rule_code, "BC-CTRL-ALL-001")
        self.assertEqual(float(controls.benchmark_mid), 28000.0)
        self.assertEqual(controls.variance_status, "HIGH")

        self.assertEqual(installation.corridor_rule_code, "BC-INST-ALL-001")
        self.assertEqual(float(installation.benchmark_mid), 35000.0)
        self.assertEqual(installation.variance_status, "HIGH")

    def test_clears_stale_corridor_data_for_market_first_lines(self):
        equipment = self.items[0]
        self.assertEqual(equipment.corridor_rule_code, "STALE-RULE")
        self.assertEqual(equipment.benchmark_source, "CORRIDOR_DB")
        self.assertEqual(equipment.variance_status, "HIGH")

        result = self._run_engine()

        self.assertTrue(result["success"])

        equipment.refresh_from_db()
        self.assertEqual(equipment.category, "EQUIPMENT")
        self.assertEqual(equipment.benchmark_source, "NONE")
        self.assertEqual(equipment.corridor_rule_code, "")
        self.assertIsNone(equipment.benchmark_min)
        self.assertIsNone(equipment.benchmark_mid)
        self.assertIsNone(equipment.benchmark_max)
        self.assertIsNone(equipment.variance_pct)
        self.assertEqual(equipment.variance_status, "NEEDS_REVIEW")
