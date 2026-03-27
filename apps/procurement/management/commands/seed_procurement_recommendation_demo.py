"""Seed a procurement recommendation demo request with pass validation and quotation context."""
from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.enums import (
    AnalysisRunType,
    ProcurementRequestStatus,
    ValidationItemStatus,
    ValidationNextAction,
    ValidationOverallStatus,
    ValidationSeverity,
    ValidationSourceType,
    ValidationType,
    PrefillStatus,
    ExtractionStatus,
)
from apps.procurement.models import ValidationResult, ValidationResultItem
from apps.procurement.services.analysis_run_service import AnalysisRunService
from apps.procurement.services.quotation_service import LineItemNormalizationService, QuotationService
from apps.procurement.services.recommendation_service import RecommendationService
from apps.procurement.services.request_service import ProcurementRequestService


class Command(BaseCommand):
    help = "Create a ready-to-test procurement recommendation demo request with quotation evidence."

    def add_arguments(self, parser):
        parser.add_argument(
            "--user-email",
            default="",
            help="Existing user email to own the seeded request. Defaults to the first active user.",
        )
        parser.add_argument(
            "--run-recommendation",
            action="store_true",
            help="Immediately run the recommendation workflow after seeding the request.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        user = self._resolve_user(options.get("user_email") or "")

        attrs = [
            {"attribute_code": "store_type", "attribute_label": "Store Type", "data_type": "TEXT", "value_text": "DATA_CENTER", "is_required": True},
            {"attribute_code": "area_sqm", "attribute_label": "Area Sqm", "data_type": "NUMBER", "value_number": Decimal("185")},
            {"attribute_code": "zone_count", "attribute_label": "Zone Count", "data_type": "NUMBER", "value_number": Decimal("3")},
            {"attribute_code": "ambient_temp_max", "attribute_label": "Ambient Temp Max", "data_type": "NUMBER", "value_number": Decimal("46")},
            {"attribute_code": "chilled_water_available", "attribute_label": "Chilled Water Available", "data_type": "TEXT", "value_text": "NO"},
            {"attribute_code": "cooling_capacity_tr", "attribute_label": "Cooling Capacity TR", "data_type": "NUMBER", "value_number": Decimal("32")},
            {"attribute_code": "redundancy", "attribute_label": "Redundancy", "data_type": "TEXT", "value_text": "N+1"},
            {"attribute_code": "efficiency_priority", "attribute_label": "Efficiency Priority", "data_type": "TEXT", "value_text": "YES"},
            {"attribute_code": "monitoring", "attribute_label": "Monitoring", "data_type": "TEXT", "value_text": "BMS integration and remote monitoring required"},
            {"attribute_code": "budget", "attribute_label": "Budget", "data_type": "NUMBER", "value_number": Decimal("120000")},
            {"attribute_code": "warranty", "attribute_label": "Warranty", "data_type": "TEXT", "value_text": "Minimum 24 months comprehensive warranty"},
            {"attribute_code": "lead_time", "attribute_label": "Lead Time", "data_type": "TEXT", "value_text": "Maximum 8 weeks"},
            {"attribute_code": "requirements", "attribute_label": "Requirements", "data_type": "TEXT", "value_text": "Precision cooling for server room with N+1 redundancy, low noise, and energy efficient inverter controls."},
        ]

        proc_request = ProcurementRequestService.create_request(
            title="Demo - Data Center Precision Cooling Recommendation",
            description=(
                "Recommend the best precision cooling solution for the head office server room. "
                "The site requires 24x7 operation, N+1 redundancy, BMS integration, and high energy efficiency."
            ),
            domain_code="HVAC",
            schema_code="DATA_CENTER_COOLING",
            request_type=AnalysisRunType.RECOMMENDATION,
            priority="HIGH",
            geography_country="UAE",
            geography_city="Dubai",
            currency="USD",
            created_by=user,
            attributes=attrs,
        )
        ProcurementRequestService.mark_ready(proc_request, user=user)

        confirmed_quotation = QuotationService.create_quotation(
            request=proc_request,
            vendor_name="CoolFlow Solutions LLC",
            quotation_number="CFS-DC-2026-014",
            total_amount=Decimal("96400.00"),
            currency="USD",
            created_by=user,
        )
        QuotationService.add_line_items(
            confirmed_quotation,
            [
                {
                    "line_number": 1,
                    "description": "Daikin VRV IV S-Series precision cooling outdoor unit",
                    "normalized_description": "daikin vrv iv s-series precision cooling outdoor unit",
                    "category_code": "PRECISION_COOLING",
                    "quantity": Decimal("2"),
                    "unit": "EA",
                    "unit_rate": Decimal("28500.00"),
                    "total_amount": Decimal("57000.00"),
                    "brand": "Daikin",
                    "model": "RXYQ14UAY1",
                    "extraction_confidence": 0.98,
                },
                {
                    "line_number": 2,
                    "description": "BMS gateway, controllers, sensors and commissioning",
                    "normalized_description": "bms gateway controllers sensors and commissioning",
                    "category_code": "BMS_INTEGRATION",
                    "quantity": Decimal("1"),
                    "unit": "LOT",
                    "unit_rate": Decimal("12400.00"),
                    "total_amount": Decimal("12400.00"),
                    "brand": "Daikin",
                    "model": "iTouch Manager",
                    "extraction_confidence": 0.97,
                },
                {
                    "line_number": 3,
                    "description": "Installation, testing and commissioning",
                    "normalized_description": "installation testing and commissioning",
                    "category_code": "TESTING_COMMISSIONING",
                    "quantity": Decimal("1"),
                    "unit": "LOT",
                    "unit_rate": Decimal("27000.00"),
                    "total_amount": Decimal("27000.00"),
                    "brand": "",
                    "model": "",
                    "extraction_confidence": 0.96,
                },
            ],
        )
        LineItemNormalizationService.normalize_line_items(confirmed_quotation)

        extracted_quotation = QuotationService.create_quotation(
            request=proc_request,
            vendor_name="ThermaTech Projects",
            quotation_number="TTP-PRC-7781",
            total_amount=Decimal("102800.00"),
            currency="USD",
            created_by=user,
        )
        extracted_quotation.prefill_status = PrefillStatus.REVIEW_PENDING
        extracted_quotation.extraction_status = ExtractionStatus.COMPLETED
        extracted_quotation.extraction_confidence = 0.89
        extracted_quotation.prefill_payload_json = {
            "success": True,
            "header_fields": {
                "vendor_name": {"value": "ThermaTech Projects", "confidence": 0.95},
                "quotation_number": {"value": "TTP-PRC-7781", "confidence": 0.94},
                "currency": {"value": "USD", "confidence": 0.98},
                "total_amount": {"value": "102800.00", "confidence": 0.92},
            },
            "commercial_terms": [
                {"term": "warranty_terms", "value": "24 months compressor warranty", "confidence": 0.9},
                {"term": "lead_time", "value": "6-8 weeks", "confidence": 0.88},
                {"term": "payment_terms", "value": "40% advance, 50% on delivery, 10% after commissioning", "confidence": 0.9},
            ],
            "line_items": [
                {
                    "line_number": 1,
                    "description": "Mitsubishi City Multi precision DX indoor unit",
                    "category_code": "PRECISION_COOLING",
                    "quantity": 3,
                    "unit": "EA",
                    "unit_rate": 22100.0,
                    "total_amount": 66300.0,
                    "brand": "Mitsubishi",
                    "model": "PFFY-P32VKM-E",
                    "confidence": 0.91,
                },
                {
                    "line_number": 2,
                    "description": "Central controller, monitoring gateway and sensors",
                    "category_code": "BMS_INTEGRATION",
                    "quantity": 1,
                    "unit": "LOT",
                    "unit_rate": 13800.0,
                    "total_amount": 13800.0,
                    "brand": "Mitsubishi",
                    "model": "AE-200E",
                    "confidence": 0.88,
                },
                {
                    "line_number": 3,
                    "description": "Installation, refrigerant piping, testing and commissioning",
                    "category_code": "TESTING_COMMISSIONING",
                    "quantity": 1,
                    "unit": "LOT",
                    "unit_rate": 22700.0,
                    "total_amount": 22700.0,
                    "brand": "",
                    "model": "",
                    "confidence": 0.86,
                },
            ],
        }
        extracted_quotation.save(update_fields=[
            "prefill_status", "extraction_status", "extraction_confidence", "prefill_payload_json", "updated_at",
        ])

        validation_run = AnalysisRunService.create_run(
            request=proc_request,
            run_type=AnalysisRunType.VALIDATION,
            triggered_by=user,
        )
        AnalysisRunService.start_run(validation_run)
        validation_result = ValidationResult.objects.create(
            run=validation_run,
            validation_type=ValidationType.ATTRIBUTE_COMPLETENESS,
            overall_status=ValidationOverallStatus.PASS,
            completeness_score=98.0,
            summary_text="Demo request is complete and ready for recommendation. Supplier quotations are available for comparison.",
            readiness_for_recommendation=True,
            readiness_for_benchmarking=True,
            recommended_next_action=ValidationNextAction.READY_FOR_RECOMMENDATION,
            missing_items_json=[],
            warnings_json=[],
            ambiguous_items_json=[],
            output_payload_json={
                "status": "ready",
                "notes": [
                    "All critical HVAC sizing attributes are present.",
                    "Quotation evidence exists in both confirmed and extracted form.",
                ],
            },
        )
        ValidationResultItem.objects.bulk_create([
            ValidationResultItem(
                validation_result=validation_result,
                item_code="HVAC_ATTR_READY",
                item_label="HVAC attributes complete",
                category=ValidationType.ATTRIBUTE_COMPLETENESS,
                status=ValidationItemStatus.PRESENT,
                severity=ValidationSeverity.INFO,
                source_type=ValidationSourceType.ATTRIBUTE,
                source_reference="store_type",
                remarks="All required HVAC recommendation attributes are populated.",
                details_json={"checked_attributes": ["store_type", "area_sqm", "zone_count", "ambient_temp_max", "chilled_water_available"]},
            ),
            ValidationResultItem(
                validation_result=validation_result,
                item_code="HVAC_QUOTATION_READY",
                item_label="Quotation evidence available",
                category=ValidationType.DOCUMENT_COMPLETENESS,
                status=ValidationItemStatus.PRESENT,
                severity=ValidationSeverity.INFO,
                source_type=ValidationSourceType.DOCUMENT,
                source_reference="SUPPLIER_QUOTATION",
                remarks="Confirmed and extracted quotation data are available for recommendation and benchmark analysis.",
                details_json={"quotation_count": 2},
            ),
        ])
        AnalysisRunService.complete_run(
            validation_run,
            output_summary=validation_result.summary_text,
            confidence_score=0.98,
        )
        ProcurementRequestService.update_status(proc_request, ProcurementRequestStatus.READY, user=user)

        self.stdout.write(self.style.SUCCESS(
            f"Created demo procurement request '{proc_request.title}' (pk={proc_request.pk}, request_id={proc_request.request_id})."
        ))
        self.stdout.write(
            f"Workspace URL: /procurement/{proc_request.pk}/"
        )

        if options.get("run_recommendation"):
            recommendation_run = AnalysisRunService.create_run(
                request=proc_request,
                run_type=AnalysisRunType.RECOMMENDATION,
                triggered_by=user,
            )
            result = RecommendationService.run_recommendation(proc_request, recommendation_run)
            self.stdout.write(self.style.SUCCESS(
                f"Recommendation completed: {result.recommended_option} (confidence={result.confidence_score or 0:.2f})"
            ))
        else:
            self.stdout.write(
                "Validation PASS data seeded. Open the workspace and click Recommendation → Run Analysis to test the end-to-end flow."
            )

    def _resolve_user(self, email: str):
        User = get_user_model()
        qs = User.objects.filter(is_active=True).order_by("is_superuser", "id")
        if email:
            user = qs.filter(email__iexact=email).first()
            if not user:
                raise CommandError(f"No active user found for email '{email}'.")
            return user

        user = qs.last()
        if not user:
            raise CommandError("No active users found. Create a user before seeding the procurement demo.")
        return user