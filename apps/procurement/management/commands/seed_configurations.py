"""Management command: seed_configurations
==========================================
Seeds 10 records each for:
  - ExternalSourceRegistry  (external sources)
  - Product                 (HVAC products)
  - Vendor                  (HVAC suppliers)
  - Room                    (physical rooms/facilities)
  - VendorProduct           (vendor-product pricing links, one per vendor-product pair)

Usage
-----
    python manage.py seed_configurations
    python manage.py seed_configurations --clear    # wipe existing before seeding
"""
from __future__ import annotations

import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.core.enums import (
    ExternalSourceClass,
    HVACSystemType,
    RoomUsageType,
)
from apps.procurement.models import (
    ExternalSourceRegistry,
    Product,
    Room,
    Vendor,
    VendorProduct,
)


# ---------------------------------------------------------------------------
# Seed data definitions
# ---------------------------------------------------------------------------

EXTERNAL_SOURCES = [
    {
        "source_name": "Daikin MEA Official",
        "domain": "daikinmea.com",
        "source_type": ExternalSourceClass.OEM_OFFICIAL,
        "country_scope": ["UAE", "KSA", "QAT", "BHR", "KWT", "OMN"],
        "priority": 1,
        "trust_score": 0.98,
        "allowed_for_discovery": True,
        "allowed_for_compliance": True,
        "fetch_mode": "PAGE",
        "notes": "Daikin Middle East and Africa primary product catalogue and datasheets.",
    },
    {
        "source_name": "Mitsubishi Electric ME",
        "domain": "mitsubishi-electric.com/me",
        "source_type": ExternalSourceClass.OEM_OFFICIAL,
        "country_scope": ["UAE", "KSA", "OMN", "EGY"],
        "priority": 2,
        "trust_score": 0.97,
        "allowed_for_discovery": True,
        "allowed_for_compliance": True,
        "fetch_mode": "PAGE",
        "notes": "Mitsubishi Electric Middle East product range and technical specs.",
    },
    {
        "source_name": "Carrier Global Datasheets",
        "domain": "carrier.com/datasheets",
        "source_type": ExternalSourceClass.TECHNICAL_DATASHEET,
        "country_scope": ["USA", "UAE", "IND", "GBR"],
        "priority": 3,
        "trust_score": 0.96,
        "allowed_for_discovery": True,
        "allowed_for_compliance": False,
        "fetch_mode": "PDF",
        "notes": "Carrier HVAC technical datasheets and performance curves.",
    },
    {
        "source_name": "Trane HVAC Product Catalogue",
        "domain": "trane.com/commercial/north-america/us/en/products.html",
        "source_type": ExternalSourceClass.OEM_OFFICIAL,
        "country_scope": ["USA", "CAN", "GBR"],
        "priority": 4,
        "trust_score": 0.95,
        "allowed_for_discovery": True,
        "allowed_for_compliance": False,
        "fetch_mode": "PAGE",
        "notes": "Trane commercial HVAC product lines including chillers and VRF.",
    },
    {
        "source_name": "ASHRAE Standards Portal",
        "domain": "ashrae.org/technical-resources/standards-and-guidelines",
        "source_type": ExternalSourceClass.STANDARD_REGULATORY,
        "country_scope": ["USA", "UAE", "IND", "GBR", "SGP"],
        "priority": 5,
        "trust_score": 1.0,
        "allowed_for_discovery": False,
        "allowed_for_compliance": True,
        "fetch_mode": "PDF",
        "notes": "ASHRAE 90.1, 62.1, 55 -- authoritative efficiency and comfort standards.",
    },
    {
        "source_name": "Dubai Green Building Regulations",
        "domain": "dm.gov.ae/en/Services/GreenBuildingRegulations",
        "source_type": ExternalSourceClass.STANDARD_REGULATORY,
        "country_scope": ["UAE"],
        "priority": 6,
        "trust_score": 1.0,
        "allowed_for_discovery": False,
        "allowed_for_compliance": True,
        "fetch_mode": "PAGE",
        "notes": "Dubai Municipality GBRS (formerly Estidama) for commercial buildings.",
    },
    {
        "source_name": "Voltas Commercial India",
        "domain": "voltasglobal.com/commercial",
        "source_type": ExternalSourceClass.OEM_REGIONAL,
        "country_scope": ["IND"],
        "priority": 7,
        "trust_score": 0.90,
        "allowed_for_discovery": True,
        "allowed_for_compliance": False,
        "fetch_mode": "PAGE",
        "notes": "Tata Group Voltas -- leading commercial HVAC brand in India.",
    },
    {
        "source_name": "Johnson Controls VRF Datasheets",
        "domain": "johnsoncontrols.com/hvac-equipment/variable-refrigerant-flow",
        "source_type": ExternalSourceClass.TECHNICAL_DATASHEET,
        "country_scope": ["USA", "UAE", "SGP", "GBR"],
        "priority": 8,
        "trust_score": 0.93,
        "allowed_for_discovery": True,
        "allowed_for_compliance": False,
        "fetch_mode": "PDF",
        "notes": "York-branded VRF datasheets from Johnson Controls.",
    },
    {
        "source_name": "National HVAC Distributor Network",
        "domain": "nhvacnet.ae",
        "source_type": ExternalSourceClass.AUTHORIZED_DISTRIBUTOR,
        "country_scope": ["UAE", "KSA"],
        "priority": 9,
        "trust_score": 0.82,
        "allowed_for_discovery": True,
        "allowed_for_compliance": False,
        "fetch_mode": "PAGE",
        "notes": "UAE-based multi-brand authorized distributor -- pricing and availability.",
    },
    {
        "source_name": "Landmark Group Internal Procurement History",
        "domain": "internal.lmg.procurement",
        "source_type": ExternalSourceClass.INTERNAL_HISTORICAL,
        "country_scope": ["UAE", "KSA", "IND", "EGY", "KWT"],
        "priority": 10,
        "trust_score": 0.99,
        "allowed_for_discovery": False,
        "allowed_for_compliance": False,
        "fetch_mode": "API",
        "notes": "Internal ERP-sourced HVAC purchase history for benchmarking.",
    },
]

PRODUCTS = [
    {
        "sku": "DAI-VRF-ODU-14TR-R32",
        "manufacturer": "Daikin",
        "product_name": "VRV IV Heat Pump Outdoor Unit 14TR",
        "system_type": HVACSystemType.VRF,
        "capacity_kw": Decimal("49.00"),
        "sound_level_db_full_load": 62,
        "sound_level_db_part_load": 54,
        "power_input_kw": Decimal("14.20"),
        "refrigerant_type": "R32",
        "cop_rating": Decimal("3.45"),
        "seer_rating": Decimal("6.10"),
        "length_mm": 1350,
        "width_mm": 760,
        "height_mm": 1680,
        "weight_kg": 285,
        "warranty_months": 24,
        "installation_support_required": True,
        "approved_use_cases": ["RETAIL", "OFFICE", "WAREHOUSE"],
        "efficiency_compliance": {"ASHRAE_90_1": True, "DEWA_GRADE": "A", "BEE_STAR": 5},
        "datasheet_url": "https://daikinmea.com/datasheets/vrv4-14tr.pdf",
    },
    {
        "sku": "MIT-MSZ-GE09NA-SPLIT",
        "manufacturer": "Mitsubishi Electric",
        "product_name": "MSZ-GE09NA Wall-Mount Split 2.5kW",
        "system_type": HVACSystemType.SPLIT_AC,
        "capacity_kw": Decimal("2.50"),
        "sound_level_db_full_load": 46,
        "sound_level_db_part_load": 38,
        "power_input_kw": Decimal("0.68"),
        "refrigerant_type": "R32",
        "cop_rating": Decimal("3.68"),
        "seer_rating": Decimal("7.20"),
        "length_mm": 870,
        "width_mm": 220,
        "height_mm": 295,
        "weight_kg": 10,
        "warranty_months": 36,
        "installation_support_required": False,
        "approved_use_cases": ["RETAIL", "OFFICE", "OTHER"],
        "efficiency_compliance": {"SEER_CLASS": "A++", "BEE_STAR": 5},
        "datasheet_url": "https://mitsubishi-electric.com/me/msz-ge09.pdf",
    },
    {
        "sku": "CAR-30XA-CHILLER-100T",
        "manufacturer": "Carrier",
        "product_name": "AquaForce 30XA Air-Cooled Chiller 100TR",
        "system_type": HVACSystemType.CHILLER,
        "capacity_kw": Decimal("352.00"),
        "sound_level_db_full_load": 75,
        "sound_level_db_part_load": 68,
        "power_input_kw": Decimal("98.50"),
        "refrigerant_type": "R134a",
        "cop_rating": Decimal("3.58"),
        "seer_rating": None,
        "length_mm": 5800,
        "width_mm": 2200,
        "height_mm": 2100,
        "weight_kg": 4800,
        "warranty_months": 24,
        "installation_support_required": True,
        "approved_use_cases": ["DATA_CENTER", "MEDICAL", "WAREHOUSE"],
        "efficiency_compliance": {"ASHRAE_90_1": True, "EUROVENT": True},
        "datasheet_url": "https://carrier.com/datasheets/30xa-100tr.pdf",
    },
    {
        "sku": "TRA-PKG-DX-20TR-R410",
        "manufacturer": "Trane",
        "product_name": "Precedent Packaged Rooftop Unit 20TR",
        "system_type": HVACSystemType.PACKAGED_DX,
        "capacity_kw": Decimal("70.30"),
        "sound_level_db_full_load": 72,
        "sound_level_db_part_load": 65,
        "power_input_kw": Decimal("20.80"),
        "refrigerant_type": "R410A",
        "cop_rating": Decimal("3.38"),
        "seer_rating": Decimal("14.00"),
        "length_mm": 3500,
        "width_mm": 1800,
        "height_mm": 1500,
        "weight_kg": 1250,
        "warranty_months": 24,
        "installation_support_required": True,
        "approved_use_cases": ["WAREHOUSE", "RETAIL", "OFFICE"],
        "efficiency_compliance": {"ASHRAE_90_1": True, "ENERGY_STAR": True},
        "datasheet_url": "https://trane.com/datasheets/precedent-20tr.pdf",
    },
    {
        "sku": "DAI-FCU-CEILING-4KW",
        "manufacturer": "Daikin",
        "product_name": "Ceiling-Concealed FCU 4kW",
        "system_type": HVACSystemType.FCU,
        "capacity_kw": Decimal("4.00"),
        "sound_level_db_full_load": 38,
        "sound_level_db_part_load": 30,
        "power_input_kw": Decimal("0.08"),
        "refrigerant_type": "",
        "cop_rating": None,
        "seer_rating": None,
        "length_mm": 1200,
        "width_mm": 250,
        "height_mm": 200,
        "weight_kg": 14,
        "warranty_months": 24,
        "installation_support_required": True,
        "approved_use_cases": ["OFFICE", "MEDICAL", "RETAIL"],
        "efficiency_compliance": {"ASHRAE_55": True},
        "datasheet_url": "https://daikinmea.com/datasheets/fcu-4kw.pdf",
    },
    {
        "sku": "MIT-PLA-CASSETTE-5KW",
        "manufacturer": "Mitsubishi Electric",
        "product_name": "PLA-SM S-Series 4-Way Cassette 5kW",
        "system_type": HVACSystemType.CASSETTE,
        "capacity_kw": Decimal("5.00"),
        "sound_level_db_full_load": 39,
        "sound_level_db_part_load": 32,
        "power_input_kw": Decimal("1.35"),
        "refrigerant_type": "R32",
        "cop_rating": Decimal("3.70"),
        "seer_rating": Decimal("6.80"),
        "length_mm": 570,
        "width_mm": 570,
        "height_mm": 270,
        "weight_kg": 17,
        "warranty_months": 36,
        "installation_support_required": True,
        "approved_use_cases": ["RETAIL", "OFFICE", "OTHER"],
        "efficiency_compliance": {"BEE_STAR": 5, "SEER_CLASS": "A+"},
        "datasheet_url": "https://mitsubishi-electric.com/datasheets/pla-sm5.pdf",
    },
    {
        "sku": "JCN-YKF-VRF-8TR",
        "manufacturer": "York",
        "product_name": "YVAA Variable Speed Inverter VRF ODU 8TR",
        "system_type": HVACSystemType.VRF,
        "capacity_kw": Decimal("28.10"),
        "sound_level_db_full_load": 60,
        "sound_level_db_part_load": 52,
        "power_input_kw": Decimal("7.80"),
        "refrigerant_type": "R410A",
        "cop_rating": Decimal("3.60"),
        "seer_rating": Decimal("6.40"),
        "length_mm": 940,
        "width_mm": 340,
        "height_mm": 1340,
        "weight_kg": 120,
        "warranty_months": 24,
        "installation_support_required": True,
        "approved_use_cases": ["RETAIL", "OFFICE", "WAREHOUSE"],
        "efficiency_compliance": {"ASHRAE_90_1": True, "ENERGY_STAR": True},
        "datasheet_url": "https://johnsoncontrols.com/datasheets/yvaa-8tr.pdf",
    },
    {
        "sku": "VOL-SMA-SPLIT-1.5TR",
        "manufacturer": "Voltas",
        "product_name": "SAC 183V SZS 1.5TR Inverter Split",
        "system_type": HVACSystemType.SPLIT_AC,
        "capacity_kw": Decimal("5.28"),
        "sound_level_db_full_load": 48,
        "sound_level_db_part_load": 40,
        "power_input_kw": Decimal("1.55"),
        "refrigerant_type": "R32",
        "cop_rating": Decimal("3.40"),
        "seer_rating": Decimal("5.00"),
        "length_mm": 880,
        "width_mm": 225,
        "height_mm": 290,
        "weight_kg": 11,
        "warranty_months": 12,
        "installation_support_required": False,
        "approved_use_cases": ["RETAIL", "OFFICE"],
        "efficiency_compliance": {"BEE_STAR": 5},
        "datasheet_url": "https://voltasglobal.com/datasheets/183v-szs.pdf",
    },
    {
        "sku": "CAR-VRF-HEATPUMP-12TR",
        "manufacturer": "Carrier",
        "product_name": "VRF J-Series Heat Pump ODU 12TR",
        "system_type": HVACSystemType.VRF,
        "capacity_kw": Decimal("42.00"),
        "sound_level_db_full_load": 64,
        "sound_level_db_part_load": 56,
        "power_input_kw": Decimal("11.90"),
        "refrigerant_type": "R410A",
        "cop_rating": Decimal("3.53"),
        "seer_rating": Decimal("5.90"),
        "length_mm": 1100,
        "width_mm": 400,
        "height_mm": 1600,
        "weight_kg": 220,
        "warranty_months": 24,
        "installation_support_required": True,
        "approved_use_cases": ["RETAIL", "WAREHOUSE", "OFFICE"],
        "efficiency_compliance": {"ASHRAE_90_1": True, "DEWA_GRADE": "B"},
        "datasheet_url": "https://carrier.com/datasheets/vrf-j-12tr.pdf",
    },
    {
        "sku": "TRA-PKG-DX-8TR-R32",
        "manufacturer": "Trane",
        "product_name": "Voyager Lite Packaged Rooftop 8TR",
        "system_type": HVACSystemType.PACKAGED_DX,
        "capacity_kw": Decimal("28.10"),
        "sound_level_db_full_load": 68,
        "sound_level_db_part_load": 60,
        "power_input_kw": Decimal("8.40"),
        "refrigerant_type": "R32",
        "cop_rating": Decimal("3.35"),
        "seer_rating": Decimal("13.50"),
        "length_mm": 1900,
        "width_mm": 1000,
        "height_mm": 1200,
        "weight_kg": 480,
        "warranty_months": 24,
        "installation_support_required": True,
        "approved_use_cases": ["WAREHOUSE", "RETAIL"],
        "efficiency_compliance": {"ENERGY_STAR": True},
        "datasheet_url": "https://trane.com/datasheets/voyager-lite-8tr.pdf",
    },
]

VENDORS = [
    {
        "vendor_name": "Al Futtaim HVAC Solutions",
        "country": "UAE",
        "city": "Dubai",
        "address": "Jebel Ali Free Zone, Building 4, Dubai, UAE",
        "contact_email": "hvac@alfuttaim.ae",
        "contact_phone": "+971-4-881-5000",
        "average_lead_time_days": 14,
        "payment_terms": "Net 30",
        "min_order_qty": 1,
        "bulk_discount_available": True,
        "rush_order_capable": True,
        "preferred_vendor": True,
        "reliability_score": Decimal("4.80"),
        "total_purchases": 128,
        "on_time_delivery_pct": Decimal("95.30"),
        "quality_issues_count": 3,
        "notes": "Daikin authorized distributor for UAE. Preferred vendor for Landmark Group.",
    },
    {
        "vendor_name": "Emirates Climate Control",
        "country": "UAE",
        "city": "Abu Dhabi",
        "address": "Musaffah Industrial Area, M-26, Abu Dhabi, UAE",
        "contact_email": "sales@emiratesclimate.ae",
        "contact_phone": "+971-2-552-4400",
        "average_lead_time_days": 21,
        "payment_terms": "50% upfront, 50% on delivery",
        "min_order_qty": 1,
        "bulk_discount_available": True,
        "rush_order_capable": False,
        "preferred_vendor": False,
        "reliability_score": Decimal("4.20"),
        "total_purchases": 45,
        "on_time_delivery_pct": Decimal("88.50"),
        "quality_issues_count": 5,
        "notes": "Mitsubishi Electric and Carrier authorized dealer for Abu Dhabi.",
    },
    {
        "vendor_name": "Saudi HVAC Engineering Co.",
        "country": "SAU",
        "city": "Riyadh",
        "address": "Industrial City 2nd, Block 12, Riyadh, KSA",
        "contact_email": "procurement@shvac.com.sa",
        "contact_phone": "+966-11-265-8800",
        "average_lead_time_days": 28,
        "payment_terms": "Net 45",
        "min_order_qty": 2,
        "bulk_discount_available": True,
        "rush_order_capable": False,
        "preferred_vendor": True,
        "reliability_score": Decimal("4.50"),
        "total_purchases": 72,
        "on_time_delivery_pct": Decimal("91.00"),
        "quality_issues_count": 4,
        "notes": "Primary supplier for Landmark KSA stores. Trane and York authorized.",
    },
    {
        "vendor_name": "Voltas Commercial Ltd",
        "country": "IND",
        "city": "Mumbai",
        "address": "Voltas House, Dr. Babasaheb Ambedkar Road, Chinchpokli, Mumbai 400033",
        "contact_email": "commercial@voltas.com",
        "contact_phone": "+91-22-6665-6666",
        "average_lead_time_days": 18,
        "payment_terms": "Net 30",
        "min_order_qty": 1,
        "bulk_discount_available": True,
        "rush_order_capable": True,
        "preferred_vendor": True,
        "reliability_score": Decimal("4.60"),
        "total_purchases": 193,
        "on_time_delivery_pct": Decimal("93.20"),
        "quality_issues_count": 7,
        "notes": "Tata Group subsidiary. Primary HVAC vendor for India operations.",
    },
    {
        "vendor_name": "Gulf Mechanical & HVAC W.L.L.",
        "country": "QAT",
        "city": "Doha",
        "address": "Industrial Area Street 3, P.O. Box 22611, Doha, Qatar",
        "contact_email": "info@gulfmech.qa",
        "contact_phone": "+974-4460-8800",
        "average_lead_time_days": 30,
        "payment_terms": "30% advance, 70% on completion",
        "min_order_qty": 1,
        "bulk_discount_available": False,
        "rush_order_capable": True,
        "preferred_vendor": False,
        "reliability_score": Decimal("4.10"),
        "total_purchases": 23,
        "on_time_delivery_pct": Decimal("86.50"),
        "quality_issues_count": 2,
        "notes": "Qatar operations for Landmark mega-mall projects.",
    },
    {
        "vendor_name": "Carrier Middle East FZE",
        "country": "UAE",
        "city": "Dubai",
        "address": "DAFZ Building 1, P.O. Box 54048, Dubai Airport Free Zone",
        "contact_email": "me.sales@carrier.com",
        "contact_phone": "+971-4-299-6000",
        "average_lead_time_days": 21,
        "payment_terms": "Net 30",
        "min_order_qty": 1,
        "bulk_discount_available": True,
        "rush_order_capable": True,
        "preferred_vendor": True,
        "reliability_score": Decimal("4.75"),
        "total_purchases": 86,
        "on_time_delivery_pct": Decimal("94.10"),
        "quality_issues_count": 2,
        "notes": "Direct OEM supply from Carrier. Preferred for chiller projects in GCC.",
    },
    {
        "vendor_name": "Star Engineering Pvt Ltd",
        "country": "IND",
        "city": "Bengaluru",
        "address": "Peenya Industrial Area Phase 2, Bengaluru 560058, Karnataka",
        "contact_email": "sales@starengg.in",
        "contact_phone": "+91-80-2839-4400",
        "average_lead_time_days": 12,
        "payment_terms": "Net 15",
        "min_order_qty": 1,
        "bulk_discount_available": True,
        "rush_order_capable": True,
        "preferred_vendor": False,
        "reliability_score": Decimal("4.30"),
        "total_purchases": 67,
        "on_time_delivery_pct": Decimal("90.00"),
        "quality_issues_count": 5,
        "notes": "VRF and split AC installer for South India. Daikin and Mitsubishi Electric.",
    },
    {
        "vendor_name": "York Johnson Controls GCC",
        "country": "UAE",
        "city": "Dubai",
        "address": "Business Bay Tower B, 12th Floor, Dubai, UAE",
        "contact_email": "gcc.hvac@jci.com",
        "contact_phone": "+971-4-445-4600",
        "average_lead_time_days": 25,
        "payment_terms": "Net 30",
        "min_order_qty": 1,
        "bulk_discount_available": True,
        "rush_order_capable": False,
        "preferred_vendor": True,
        "reliability_score": Decimal("4.65"),
        "total_purchases": 54,
        "on_time_delivery_pct": Decimal("92.60"),
        "quality_issues_count": 3,
        "notes": "York brand VRF systems directly from Johnson Controls GCC office.",
    },
    {
        "vendor_name": "Al-Sadiq HVAC Co. W.L.L.",
        "country": "KWT",
        "city": "Kuwait City",
        "address": "Shuwaikh Industrial Area, Area 2, Block 3, Kuwait City",
        "contact_email": "info@alsadiqhvac.com.kw",
        "contact_phone": "+965-2481-2200",
        "average_lead_time_days": 35,
        "payment_terms": "50% advance",
        "min_order_qty": 1,
        "bulk_discount_available": False,
        "rush_order_capable": False,
        "preferred_vendor": False,
        "reliability_score": Decimal("3.90"),
        "total_purchases": 15,
        "on_time_delivery_pct": Decimal("80.00"),
        "quality_issues_count": 3,
        "notes": "Kuwait operations. Secondary vendor for Landmark Kuwait City.",
    },
    {
        "vendor_name": "Integrated MEP Solutions India",
        "country": "IND",
        "city": "Delhi",
        "address": "Sector 63, NOIDA, Uttar Pradesh 201301",
        "contact_email": "procurement@intmep.in",
        "contact_phone": "+91-120-455-8800",
        "average_lead_time_days": 20,
        "payment_terms": "Net 30",
        "min_order_qty": 2,
        "bulk_discount_available": True,
        "rush_order_capable": True,
        "preferred_vendor": False,
        "reliability_score": Decimal("4.15"),
        "total_purchases": 38,
        "on_time_delivery_pct": Decimal("87.00"),
        "quality_issues_count": 6,
        "notes": "North India MEP contractor. Handles VRF, chiller, and packaged DX supply.",
    },
]

ROOMS = [
    {
        "room_code": "SRV-A",
        "building_name": "Dubai Mall Head Office - Tower A",
        "floor_number": 2,
        "location_description": "Server room adjacent to IT helpdesk, south wing.",
        "area_sqm": Decimal("45.00"),
        "ceiling_height_m": Decimal("3.00"),
        "usage_type": RoomUsageType.DATA_CENTER,
        "design_temp_c": Decimal("20.0"),
        "temp_tolerance_c": Decimal("2.0"),
        "design_cooling_load_kw": Decimal("22.00"),
        "design_humidity_pct": 50,
        "noise_limit_db": 55,
        "current_hvac_type": "Precision AC (Liebert)",
        "current_hvac_age_years": 7,
        "access_constraints": "Raised floor, limited ceiling clearance of 3.0m.",
        "contact_name": "Aravind Menon",
        "contact_email": "aravind.menon@lmg.ae",
    },
    {
        "room_code": "OFF-101",
        "building_name": "Dubai Mall Head Office - Tower A",
        "floor_number": 1,
        "location_description": "Open-plan finance office, north wing first floor.",
        "area_sqm": Decimal("180.00"),
        "ceiling_height_m": Decimal("2.80"),
        "usage_type": RoomUsageType.OFFICE,
        "design_temp_c": Decimal("23.0"),
        "temp_tolerance_c": Decimal("1.0"),
        "design_cooling_load_kw": Decimal("18.00"),
        "design_humidity_pct": None,
        "noise_limit_db": 42,
        "current_hvac_type": "FCU (Carrier)",
        "current_hvac_age_years": 5,
        "access_constraints": "",
        "contact_name": "Salma Al Rashid",
        "contact_email": "salma.alrashid@lmg.ae",
    },
    {
        "room_code": "WHS-DBX-01",
        "building_name": "Jebel Ali Distribution Centre",
        "floor_number": 0,
        "location_description": "Main dispatch bay, warehousing zone A.",
        "area_sqm": Decimal("2500.00"),
        "ceiling_height_m": Decimal("9.00"),
        "usage_type": RoomUsageType.WAREHOUSE,
        "design_temp_c": Decimal("26.0"),
        "temp_tolerance_c": Decimal("3.0"),
        "design_cooling_load_kw": Decimal("95.00"),
        "design_humidity_pct": None,
        "noise_limit_db": None,
        "current_hvac_type": "Evaporative Cooler",
        "current_hvac_age_years": 10,
        "access_constraints": "No overhead crane clearance. Rooftop access only via hatch.",
        "contact_name": "Prakash Nair",
        "contact_email": "prakash.nair@lmg.ae",
    },
    {
        "room_code": "RTL-DXB-MAX-01",
        "building_name": "Max Fashion Dubai Mall Store",
        "floor_number": 0,
        "location_description": "Ground floor showroom -- full footprint.",
        "area_sqm": Decimal("1200.00"),
        "ceiling_height_m": Decimal("4.50"),
        "usage_type": RoomUsageType.RETAIL,
        "design_temp_c": Decimal("22.0"),
        "temp_tolerance_c": Decimal("2.0"),
        "design_cooling_load_kw": Decimal("155.00"),
        "design_humidity_pct": None,
        "noise_limit_db": 45,
        "current_hvac_type": "VRF (Mitsubishi R22)",
        "current_hvac_age_years": 12,
        "access_constraints": "Mall-mandated rooftop only for ODU. Night works only for install.",
        "contact_name": "Rajesh Kumar",
        "contact_email": "rajesh.kumar@lmg.ae",
    },
    {
        "room_code": "MED-AUH-CLINIC-01",
        "building_name": "LuLu Hypermarket Abu Dhabi - Medical Centre",
        "floor_number": 1,
        "location_description": "First-aid medical clinic, east corridor.",
        "area_sqm": Decimal("60.00"),
        "ceiling_height_m": Decimal("2.70"),
        "usage_type": RoomUsageType.MEDICAL,
        "design_temp_c": Decimal("21.0"),
        "temp_tolerance_c": Decimal("1.0"),
        "design_cooling_load_kw": Decimal("8.50"),
        "design_humidity_pct": 55,
        "noise_limit_db": 38,
        "current_hvac_type": "Split AC (Daikin)",
        "current_hvac_age_years": 3,
        "access_constraints": "Strict noise limit. No vibration above 0.2mm/s.",
        "contact_name": "Dr. Fatima Al Marzouqi",
        "contact_email": "clinic.auh@landmark.ae",
    },
    {
        "room_code": "LAB-BLR-QC-01",
        "building_name": "Bengaluru Processing Centre",
        "floor_number": 0,
        "location_description": "Quality control lab, west end of ground floor.",
        "area_sqm": Decimal("90.00"),
        "ceiling_height_m": Decimal("3.20"),
        "usage_type": RoomUsageType.LAB,
        "design_temp_c": Decimal("20.0"),
        "temp_tolerance_c": Decimal("0.5"),
        "design_cooling_load_kw": Decimal("12.00"),
        "design_humidity_pct": 45,
        "noise_limit_db": 40,
        "current_hvac_type": "Precision AC (Stulz)",
        "current_hvac_age_years": 4,
        "access_constraints": "No dust ingress. Positive pressure required.",
        "contact_name": "Lavanya Rajan",
        "contact_email": "lavanya.rajan@lmg.in",
    },
    {
        "room_code": "OFF-RUH-HQ-02",
        "building_name": "Riyadh HQ - Level 2",
        "floor_number": 2,
        "location_description": "Executive floor -- boardroom and private offices.",
        "area_sqm": Decimal("350.00"),
        "ceiling_height_m": Decimal("3.00"),
        "usage_type": RoomUsageType.OFFICE,
        "design_temp_c": Decimal("22.0"),
        "temp_tolerance_c": Decimal("1.0"),
        "design_cooling_load_kw": Decimal("38.00"),
        "design_humidity_pct": None,
        "noise_limit_db": 35,
        "current_hvac_type": "VRF (Daikin VRIV)",
        "current_hvac_age_years": 2,
        "access_constraints": "Concealed FCU only -- no exposed ductwork.",
        "contact_name": "Mohammed Al Otaibi",
        "contact_email": "m.otaibi@lmg.com.sa",
    },
    {
        "room_code": "DC-DXB-EDGE-01",
        "building_name": "Dubai Operations Edge Data Centre",
        "floor_number": -1,
        "location_description": "Basement edge data centre -- racks A1 to A12.",
        "area_sqm": Decimal("120.00"),
        "ceiling_height_m": Decimal("3.50"),
        "usage_type": RoomUsageType.DATA_CENTER,
        "design_temp_c": Decimal("18.0"),
        "temp_tolerance_c": Decimal("1.0"),
        "design_cooling_load_kw": Decimal("55.00"),
        "design_humidity_pct": 50,
        "noise_limit_db": 65,
        "current_hvac_type": "Precision CRAC (Schneider APC)",
        "current_hvac_age_years": 6,
        "access_constraints": "Raised floor (600mm). N+1 redundancy required.",
        "contact_name": "Ravi Shankar",
        "contact_email": "ravi.shankar@lmg.ae",
    },
    {
        "room_code": "RTL-KWT-HOME-01",
        "building_name": "Home Centre Avenues Mall Kuwait",
        "floor_number": 0,
        "location_description": "Ground-level furniture showroom, 480 sqm.",
        "area_sqm": Decimal("480.00"),
        "ceiling_height_m": Decimal("3.20"),
        "usage_type": RoomUsageType.RETAIL,
        "design_temp_c": Decimal("22.0"),
        "temp_tolerance_c": Decimal("2.0"),
        "design_cooling_load_kw": Decimal("62.00"),
        "design_humidity_pct": None,
        "noise_limit_db": 45,
        "current_hvac_type": "Cassette Split (LG)",
        "current_hvac_age_years": 8,
        "access_constraints": "Mall-provided chilled water available at boundary valve.",
        "contact_name": "Ahmad Al Salem",
        "contact_email": "a.salem@lmg.com.kw",
    },
    {
        "room_code": "WHS-MUM-STOR-02",
        "building_name": "Mumbai Central Warehouse",
        "floor_number": 0,
        "location_description": "Secondary storage zone, aisle C-D, 1500 sqm.",
        "area_sqm": Decimal("1500.00"),
        "ceiling_height_m": Decimal("7.50"),
        "usage_type": RoomUsageType.WAREHOUSE,
        "design_temp_c": Decimal("28.0"),
        "temp_tolerance_c": Decimal("4.0"),
        "design_cooling_load_kw": Decimal("60.00"),
        "design_humidity_pct": None,
        "noise_limit_db": None,
        "current_hvac_type": "Evaporative + Industrial Fans",
        "current_hvac_age_years": 15,
        "access_constraints": "Old structure. Load-bearing check required before rooftop units.",
        "contact_name": "Suresh Pillay",
        "contact_email": "suresh.pillay@lmg.in",
    },
]

# VendorProduct links: 10 representative pairings (vendor_idx, product_idx)
VENDOR_PRODUCT_LINKS = [
    # (vendor_name, product_sku, vendor_sku, unit_price, currency, lead_time_days)
    ("Al Futtaim HVAC Solutions",       "DAI-VRF-ODU-14TR-R32",  "AF-DAI-VRF14",   Decimal("95000"),  "AED", 14),
    ("Al Futtaim HVAC Solutions",       "DAI-FCU-CEILING-4KW",   "AF-DAI-FCU4",    Decimal("3200"),   "AED",  7),
    ("Emirates Climate Control",        "MIT-MSZ-GE09NA-SPLIT",  "ECC-MIT-09NA",   Decimal("2800"),   "AED",  7),
    ("Emirates Climate Control",        "MIT-PLA-CASSETTE-5KW",  "ECC-MIT-PLA5",   Decimal("4100"),   "AED", 10),
    ("Carrier Middle East FZE",         "CAR-30XA-CHILLER-100T", "CME-30XA100",    Decimal("520000"), "AED", 60),
    ("Carrier Middle East FZE",         "CAR-VRF-HEATPUMP-12TR", "CME-VRF12",      Decimal("72000"),  "AED", 21),
    ("Saudi HVAC Engineering Co.",      "TRA-PKG-DX-20TR-R410",  "SH-TRA-PDX20",   Decimal("88000"),  "SAR", 28),
    ("York Johnson Controls GCC",       "JCN-YKF-VRF-8TR",       "YJC-VRF8",       Decimal("45000"),  "AED", 25),
    ("Voltas Commercial Ltd",           "VOL-SMA-SPLIT-1.5TR",   "VOL-183V",       Decimal("42000"),  "INR", 10),
    ("Integrated MEP Solutions India",  "TRA-PKG-DX-8TR-R32",    "IMS-TRA-LITE8",  Decimal("185000"), "INR", 20),
]


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = "Seed 10 records for ExternalSources, Products, Vendors, and Rooms in the DB"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete existing ExternalSourceRegistry, Product, Vendor, and Room records first",
        )

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        if options["clear"]:
            self.stdout.write(self.style.WARNING("Clearing existing seed records ..."))
            VendorProduct.objects.all().delete()
            ExternalSourceRegistry.objects.all().delete()
            Product.objects.all().delete()
            Vendor.objects.all().delete()
            Room.objects.all().delete()
            self.stdout.write(self.style.WARNING("  Cleared."))

        # -- External Sources --
        self.stdout.write(self.style.MIGRATE_HEADING("Seeding External Sources ..."))
        src_created = src_skipped = 0
        for data in EXTERNAL_SOURCES:
            _, created = ExternalSourceRegistry.objects.get_or_create(
                domain=data["domain"],
                defaults=data,
            )
            if created:
                src_created += 1
            else:
                src_skipped += 1
        self.stdout.write(
            self.style.SUCCESS(f"  External Sources: {src_created} created, {src_skipped} already existed")
        )

        # -- Products --
        self.stdout.write(self.style.MIGRATE_HEADING("Seeding Products ..."))
        prod_created = prod_skipped = 0
        product_map: dict[str, Product] = {}
        for data in PRODUCTS:
            sku = data["sku"]
            obj, created = Product.objects.get_or_create(
                sku=sku,
                defaults=data,
            )
            product_map[sku] = obj
            if created:
                prod_created += 1
            else:
                prod_skipped += 1
        self.stdout.write(
            self.style.SUCCESS(f"  Products: {prod_created} created, {prod_skipped} already existed")
        )

        # -- Vendors --
        self.stdout.write(self.style.MIGRATE_HEADING("Seeding Vendors ..."))
        vend_created = vend_skipped = 0
        vendor_map: dict[str, Vendor] = {}
        for data in VENDORS:
            name = data["vendor_name"]
            obj, created = Vendor.objects.get_or_create(
                vendor_name=name,
                defaults=data,
            )
            vendor_map[name] = obj
            if created:
                vend_created += 1
            else:
                vend_skipped += 1
        self.stdout.write(
            self.style.SUCCESS(f"  Vendors: {vend_created} created, {vend_skipped} already existed")
        )

        # -- Rooms --
        self.stdout.write(self.style.MIGRATE_HEADING("Seeding Rooms ..."))
        room_created = room_skipped = 0
        for data in ROOMS:
            _, created = Room.objects.get_or_create(
                room_code=data["room_code"],
                defaults=data,
            )
            if created:
                room_created += 1
            else:
                room_skipped += 1
        self.stdout.write(
            self.style.SUCCESS(f"  Rooms: {room_created} created, {room_skipped} already existed")
        )

        # -- VendorProduct links --
        self.stdout.write(self.style.MIGRATE_HEADING("Seeding VendorProduct links ..."))
        vp_created = vp_skipped = 0
        for vname, sku, vendor_sku, unit_price, currency, lead_time in VENDOR_PRODUCT_LINKS:
            vendor_obj = vendor_map.get(vname)
            product_obj = product_map.get(sku)
            if not vendor_obj or not product_obj:
                self.stdout.write(
                    self.style.WARNING(f"  Skipping VendorProduct link ({vname} / {sku}) -- object not found")
                )
                continue
            _, created = VendorProduct.objects.get_or_create(
                vendor=vendor_obj,
                product=product_obj,
                defaults={
                    "vendor_sku": vendor_sku,
                    "unit_price": unit_price,
                    "currency": currency,
                    "lead_time_days": lead_time,
                    "stock_available": 10,
                    "bulk_discount_pct": Decimal("5.00"),
                    "quote_validity_days": 30,
                    "is_preferred": vendor_obj.preferred_vendor,
                    "is_active": True,
                },
            )
            if created:
                vp_created += 1
            else:
                vp_skipped += 1
        self.stdout.write(
            self.style.SUCCESS(f"  VendorProduct links: {vp_created} created, {vp_skipped} already existed")
        )

        # -- Summary --
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            "seed_configurations complete -- "
            f"{src_created} sources, {prod_created} products, "
            f"{vend_created} vendors, {room_created} rooms, {vp_created} vendor-product links seeded."
        ))
