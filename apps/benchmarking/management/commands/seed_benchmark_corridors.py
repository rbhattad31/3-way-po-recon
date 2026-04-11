"""
Management command to seed default BenchmarkCorridorRule records.

Rates are based on GCC HVAC market benchmarks (UAE/KSA/Qatar) for 2025-2026.
Rule codes are aligned with the rates used in seed_benchmark_data.py demo data.

Usage:
    python manage.py seed_benchmark_corridors
    python manage.py seed_benchmark_corridors --clear   # clear and re-seed
"""

from django.core.management.base import BaseCommand
from apps.benchmarking.models import BenchmarkCorridorRule


CORRIDORS = [
    # =========================================================================
    # EQUIPMENT -- UAE
    # =========================================================================
    {
        "rule_code": "BC-EQUIP-UAE-001",
        "name": "VRF / VRV Outdoor Unit -- UAE",
        "category": "EQUIPMENT",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/No",
        "min_rate": 15000,
        "mid_rate": 17500,
        "max_rate": 21000,
        "currency": "AED",
        "keywords": "vrf,vrv,outdoor unit,condensing unit,multi-split outdoor",
        "notes": "Per outdoor unit (typical range: 10-25HP). Brands: Daikin, Mitsubishi, LG.",
        "priority": 10,
    },
    {
        "rule_code": "BC-EQUIP-UAE-002",
        "name": "Water-Cooled Chiller (60-100TR) -- UAE",
        "category": "EQUIPMENT",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/No",
        "min_rate": 130000,
        "mid_rate": 150000,
        "max_rate": 175000,
        "currency": "AED",
        "keywords": "chiller,water cooled chiller,60tr,80tr,100tr,screw chiller,centrifugal chiller",
        "notes": "Per chiller unit. Brands: Carrier, Trane, York. Includes refrigerant charge.",
        "priority": 10,
    },
    {
        "rule_code": "BC-EQUIP-UAE-003",
        "name": "Fan Coil Unit (2TR Cassette) -- UAE",
        "category": "EQUIPMENT",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/No",
        "min_rate": 2800,
        "mid_rate": 3000,
        "max_rate": 3500,
        "currency": "AED",
        "keywords": "fan coil,fcu,cassette,2-pipe,4-pipe,ceiling cassette,2tr fcu",
        "notes": "Per FCU unit (2TR / 7kW range). Includes controls and condensate drain.",
        "priority": 10,
    },
    {
        "rule_code": "BC-EQUIP-UAE-004",
        "name": "Air Handling Unit / Packaged DX -- UAE",
        "category": "EQUIPMENT",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/No",
        "min_rate": 18000,
        "mid_rate": 24000,
        "max_rate": 32000,
        "currency": "AED",
        "keywords": "ahu,air handling unit,packaged unit,dx unit,rooftop unit,rtu",
        "notes": "Per AHU/RTU (10-20TR typical). Includes filters and EC fans.",
        "priority": 20,
    },
    {
        "rule_code": "BC-EQUIP-UAE-005",
        "name": "VRF / VRV Indoor Cassette Unit -- UAE",
        "category": "EQUIPMENT",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/No",
        "min_rate": 2200,
        "mid_rate": 2600,
        "max_rate": 3100,
        "currency": "AED",
        "keywords": "vrf indoor,vrv indoor,cassette indoor,wall mounted indoor,ceiling cassette,2.5hp indoor,indoor unit",
        "notes": "Per indoor unit (1.5-3HP). Includes remote controller.",
        "priority": 10,
    },
    # =========================================================================
    # EQUIPMENT -- KSA
    # =========================================================================
    {
        "rule_code": "BC-EQUIP-KSA-001",
        "name": "Packaged Rooftop Unit (25TR) -- KSA",
        "category": "EQUIPMENT",
        "geography": "KSA",
        "scope_type": "ALL",
        "uom": "AED/No",
        "min_rate": 22000,
        "mid_rate": 25000,
        "max_rate": 30000,
        "currency": "AED",
        "keywords": "packaged rooftop,rooftop unit,25tr,rtu,packaged dx,york,trane,carrier rooftop",
        "notes": "Per packaged rooftop unit (25TR / 88kW). Zamil, York, Trane brands common in KSA.",
        "priority": 10,
    },
    {
        "rule_code": "BC-EQUIP-KSA-002",
        "name": "Water-Cooled Chiller (60-100TR) -- KSA",
        "category": "EQUIPMENT",
        "geography": "KSA",
        "scope_type": "ALL",
        "uom": "AED/No",
        "min_rate": 125000,
        "mid_rate": 145000,
        "max_rate": 170000,
        "currency": "AED",
        "keywords": "chiller,water cooled,ksa chiller,screw chiller",
        "notes": "Per chiller unit. KSA rates approx. 3-5% below UAE due to lower freight.",
        "priority": 10,
    },
    {
        "rule_code": "BC-EQUIP-KSA-003",
        "name": "Split System (2TR) -- KSA",
        "category": "EQUIPMENT",
        "geography": "KSA",
        "scope_type": "ALL",
        "uom": "AED/No",
        "min_rate": 3200,
        "mid_rate": 3600,
        "max_rate": 4500,
        "currency": "AED",
        "keywords": "split ac,split system,wall mounted,2tr split,back office ac,split unit ksa",
        "notes": "Per split system set (outdoor + indoor). Inverter type. Brands: Samsung, Midea, Gree.",
        "priority": 20,
    },
    {
        "rule_code": "BC-EQUIP-KSA-004",
        "name": "VRF / VRV Outdoor Unit -- KSA",
        "category": "EQUIPMENT",
        "geography": "KSA",
        "scope_type": "ALL",
        "uom": "AED/No",
        "min_rate": 14000,
        "mid_rate": 16500,
        "max_rate": 20000,
        "currency": "AED",
        "keywords": "vrf,vrv,outdoor unit ksa,condensing unit ksa",
        "notes": "Per VRF outdoor unit (10-25HP). Approx. 5% lower than UAE.",
        "priority": 10,
    },
    # =========================================================================
    # EQUIPMENT -- QATAR
    # =========================================================================
    {
        "rule_code": "BC-EQUIP-QAT-001",
        "name": "VRF / VRV Outdoor Unit -- Qatar",
        "category": "EQUIPMENT",
        "geography": "QATAR",
        "scope_type": "ALL",
        "uom": "AED/No",
        "min_rate": 16000,
        "mid_rate": 19000,
        "max_rate": 23000,
        "currency": "AED",
        "keywords": "vrf,vrv,outdoor unit qatar,condensing unit qatar",
        "notes": "Per VRF outdoor unit. Qatar market typically 5-10% higher than UAE due to import duties.",
        "priority": 10,
    },
    {
        "rule_code": "BC-EQUIP-QAT-002",
        "name": "Chiller (60-100TR) -- Qatar",
        "category": "EQUIPMENT",
        "geography": "QATAR",
        "scope_type": "ALL",
        "uom": "AED/No",
        "min_rate": 135000,
        "mid_rate": 158000,
        "max_rate": 185000,
        "currency": "AED",
        "keywords": "chiller qatar,water cooled chiller qar",
        "notes": "Per chiller unit (80TR typical). Includes refrigerant charge.",
        "priority": 10,
    },
    # =========================================================================
    # EQUIPMENT -- ALL (generic fallback)
    # =========================================================================
    {
        "rule_code": "BC-EQUIP-ALL-001",
        "name": "Generic HVAC Equipment -- All Geographies",
        "category": "EQUIPMENT",
        "geography": "ALL",
        "scope_type": "ALL",
        "uom": "AED/No",
        "min_rate": 14000,
        "mid_rate": 18000,
        "max_rate": 24000,
        "currency": "AED",
        "keywords": "equipment,hvac unit,system,outdoor,indoor,unit",
        "notes": "Generic fallback rule. Use geo-specific rules where available.",
        "priority": 100,
    },
    # =========================================================================
    # CONTROLS -- UAE
    # =========================================================================
    {
        "rule_code": "BC-CTRL-UAE-001",
        "name": "BMS / DDC Controller + Panel -- UAE",
        "category": "CONTROLS",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/Lot",
        "min_rate": 28000,
        "mid_rate": 33000,
        "max_rate": 40000,
        "currency": "AED",
        "keywords": "bms,ddc,building management system,control panel,bacnet,modbus,lon,ddc panel,bms uae",
        "notes": "Per BMS/DDC lot (small to mid-size project 10-50 points). Includes software license.",
        "priority": 10,
    },
    {
        "rule_code": "BC-CTRL-UAE-002",
        "name": "Control Cabling (HVAC) -- UAE",
        "category": "CONTROLS",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/m",
        "min_rate": 12,
        "mid_rate": 18,
        "max_rate": 28,
        "currency": "AED",
        "keywords": "control cable,signal cable,screened cable,instrumentation cable,bms cable",
        "notes": "Per linear metre. 2-core or 4-core screened.",
        "priority": 20,
    },
    # =========================================================================
    # CONTROLS -- KSA
    # =========================================================================
    {
        "rule_code": "BC-CTRL-KSA-001",
        "name": "DDC Controls + BMS Integration -- KSA",
        "category": "CONTROLS",
        "geography": "KSA",
        "scope_type": "ALL",
        "uom": "AED/Lot",
        "min_rate": 18000,
        "mid_rate": 21000,
        "max_rate": 27000,
        "currency": "AED",
        "keywords": "ddc,bms,controls ksa,control integration,building management ksa",
        "notes": "Per BMS lot (small project 5-20 points). Lower cost vs UAE for standard scope.",
        "priority": 10,
    },
    # =========================================================================
    # CONTROLS -- QATAR
    # =========================================================================
    {
        "rule_code": "BC-CTRL-QAT-001",
        "name": "BMS / DDC Controls -- Qatar",
        "category": "CONTROLS",
        "geography": "QATAR",
        "scope_type": "ALL",
        "uom": "AED/Lot",
        "min_rate": 30000,
        "mid_rate": 36000,
        "max_rate": 45000,
        "currency": "AED",
        "keywords": "bms,ddc,controls qatar,building management qatar",
        "notes": "Per BMS lot. Qatar market typically higher due to project complexity and QCC compliance.",
        "priority": 10,
    },
    # =========================================================================
    # CONTROLS -- ALL (generic fallback)
    # =========================================================================
    {
        "rule_code": "BC-CTRL-ALL-001",
        "name": "BMS / DDC Controls -- All Geographies",
        "category": "CONTROLS",
        "geography": "ALL",
        "scope_type": "ALL",
        "uom": "AED/Lot",
        "min_rate": 20000,
        "mid_rate": 28000,
        "max_rate": 42000,
        "currency": "AED",
        "keywords": "bms,ddc,building management,controller,modbus,bacnet,lon,control panel,controls",
        "notes": "Generic fallback. Use geo-specific rules where available.",
        "priority": 100,
    },
    # =========================================================================
    # DUCTING -- UAE
    # =========================================================================
    {
        "rule_code": "BC-DUCT-UAE-001",
        "name": "GI Rectangular Ducting (1.2mm) -- UAE",
        "category": "DUCTING",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/m2",
        "min_rate": 80,
        "mid_rate": 90,
        "max_rate": 110,
        "currency": "AED",
        "keywords": "gi duct,galvanised duct,galvanized duct,rectangular duct,ductwork,1.2mm,gi sheet,hvac ductwork",
        "notes": "Per m2 of duct surface area. Includes sheet metal fabrication, joints, and supports.",
        "priority": 10,
    },
    {
        "rule_code": "BC-DUCT-UAE-002",
        "name": "Flexible Ductwork -- UAE",
        "category": "DUCTING",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/m",
        "min_rate": 32,
        "mid_rate": 40,
        "max_rate": 52,
        "currency": "AED",
        "keywords": "flexible duct,flex duct,insulated flexible duct",
        "notes": "Per linear metre. Insulated type with vapour barrier.",
        "priority": 20,
    },
    # =========================================================================
    # DUCTING -- KSA
    # =========================================================================
    {
        "rule_code": "BC-DUCT-KSA-001",
        "name": "GI Ducting (0.8mm) -- KSA",
        "category": "DUCTING",
        "geography": "KSA",
        "scope_type": "ALL",
        "uom": "AED/m2",
        "min_rate": 65,
        "mid_rate": 75,
        "max_rate": 88,
        "currency": "AED",
        "keywords": "gi duct ksa,ducting ksa,0.8mm duct,galvanised ksa,ductwork ksa",
        "notes": "Per m2. KSA rates slightly lower due to lower labour cost. Includes hangers.",
        "priority": 10,
    },
    # =========================================================================
    # DUCTING -- QATAR
    # =========================================================================
    {
        "rule_code": "BC-DUCT-QAT-001",
        "name": "GI Rectangular Ducting -- Qatar",
        "category": "DUCTING",
        "geography": "QATAR",
        "scope_type": "ALL",
        "uom": "AED/m2",
        "min_rate": 75,
        "mid_rate": 88,
        "max_rate": 105,
        "currency": "AED",
        "keywords": "gi duct qatar,ducting qatar,galvanised duct qar",
        "notes": "Per m2. Qatar market rates similar to UAE.",
        "priority": 10,
    },
    {
        "rule_code": "BC-DUCT-QAT-002",
        "name": "Flexible Ducting (250mm dia) -- Qatar",
        "category": "DUCTING",
        "geography": "QATAR",
        "scope_type": "ALL",
        "uom": "AED/m",
        "min_rate": 28,
        "mid_rate": 35,
        "max_rate": 45,
        "currency": "AED",
        "keywords": "flexible duct qatar,flex duct,250mm,insulated flex,flex duct qar",
        "notes": "Per linear metre. 250mm dia insulated flexible duct.",
        "priority": 20,
    },
    # =========================================================================
    # DUCTING -- ALL (generic fallback)
    # =========================================================================
    {
        "rule_code": "BC-DUCT-ALL-001",
        "name": "GI Ductwork -- All Geographies",
        "category": "DUCTING",
        "geography": "ALL",
        "scope_type": "ALL",
        "uom": "AED/m2",
        "min_rate": 70,
        "mid_rate": 85,
        "max_rate": 105,
        "currency": "AED",
        "keywords": "gi duct,galvanised duct,galvanized duct,ductwork,rectangular duct,circular duct,supply duct,extract duct",
        "notes": "Generic fallback per m2. Use geo-specific rule where available.",
        "priority": 100,
    },
    # =========================================================================
    # INSULATION -- UAE
    # =========================================================================
    {
        "rule_code": "BC-INSUL-UAE-001",
        "name": "Pipe Insulation Armaflex (25mm) -- UAE",
        "category": "INSULATION",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/m",
        "min_rate": 22,
        "mid_rate": 27,
        "max_rate": 35,
        "currency": "AED",
        "keywords": "armaflex,pipe insulation,nbr foam,rubber foam,25mm insulation,elastomeric,refrigerant pipe insulation",
        "notes": "Per linear metre. 25mm Armaflex or equivalent on refrigerant / chilled water pipes.",
        "priority": 10,
    },
    {
        "rule_code": "BC-INSUL-UAE-002",
        "name": "Duct Insulation Glass Wool -- UAE",
        "category": "INSULATION",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/m2",
        "min_rate": 32,
        "mid_rate": 42,
        "max_rate": 58,
        "currency": "AED",
        "keywords": "glass wool,duct insulation,duct wrap,aluminium foil,mineral wool",
        "notes": "Per m2. 50mm glass wool with aluminium foil facing on supply air ducts.",
        "priority": 20,
    },
    # =========================================================================
    # INSULATION -- KSA
    # =========================================================================
    {
        "rule_code": "BC-INSUL-KSA-001",
        "name": "Duct Insulation Elastomeric -- KSA",
        "category": "INSULATION",
        "geography": "KSA",
        "scope_type": "ALL",
        "uom": "AED/m2",
        "min_rate": 50,
        "mid_rate": 60,
        "max_rate": 75,
        "currency": "AED",
        "keywords": "elastomeric insulation,duct insulation ksa,armaflex duct,19mm,foam insulation ksa",
        "notes": "Per m2. 19mm elastomeric foam on ductwork. Common in KSA commercial projects.",
        "priority": 10,
    },
    {
        "rule_code": "BC-INSUL-KSA-002",
        "name": "Pipe Insulation -- KSA",
        "category": "INSULATION",
        "geography": "KSA",
        "scope_type": "ALL",
        "uom": "AED/m",
        "min_rate": 20,
        "mid_rate": 25,
        "max_rate": 32,
        "currency": "AED",
        "keywords": "pipe insulation ksa,armaflex ksa,nbr ksa,25mm ksa",
        "notes": "Per linear metre. Refrigerant pipe insulation in KSA market.",
        "priority": 20,
    },
    # =========================================================================
    # INSULATION -- ALL (generic fallback)
    # =========================================================================
    {
        "rule_code": "BC-INSUL-ALL-001",
        "name": "Pipe and Duct Insulation -- All Geographies",
        "category": "INSULATION",
        "geography": "ALL",
        "scope_type": "ALL",
        "uom": "AED/m",
        "min_rate": 18,
        "mid_rate": 26,
        "max_rate": 40,
        "currency": "AED",
        "keywords": "insulation,armaflex,pipe insulation,duct insulation,nbr foam,glass wool,elastomeric",
        "notes": "Generic fallback per linear metre. Use geo-specific and type-specific rules where available.",
        "priority": 100,
    },
    # =========================================================================
    # ACCESSORIES -- UAE
    # =========================================================================
    {
        "rule_code": "BC-ACC-UAE-001",
        "name": "VRF Refrigerant Copper Piping -- UAE",
        "category": "ACCESSORIES",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/m",
        "min_rate": 55,
        "mid_rate": 70,
        "max_rate": 90,
        "currency": "AED",
        "keywords": "copper pipe,refrigerant pipe,vrf piping,liquid line,suction line,copper tubing",
        "notes": "Per linear metre. Includes insulation, fittings, and pressure test.",
        "priority": 10,
    },
    {
        "rule_code": "BC-ACC-UAE-002",
        "name": "Valves and Dampers -- UAE",
        "category": "ACCESSORIES",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/No",
        "min_rate": 120,
        "mid_rate": 220,
        "max_rate": 400,
        "currency": "AED",
        "keywords": "valve,ball valve,motorised valve,vav damper,fire damper,balancing valve,isolation valve",
        "notes": "Per unit. Range depends on type: ball valve (120-180), motorised (220-400).",
        "priority": 20,
    },
    {
        "rule_code": "BC-ACC-UAE-003",
        "name": "Pipe Hangers and Support Steelwork -- UAE",
        "category": "ACCESSORIES",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/Lot",
        "min_rate": 10000,
        "mid_rate": 15000,
        "max_rate": 20000,
        "currency": "AED",
        "keywords": "hanger,support,bracket,unistrut,rod,clamp,pipe support,steelwork support",
        "notes": "Per lot for a typical mid-size project. Includes unistrut, threaded rods, and brackets.",
        "priority": 20,
    },
    # =========================================================================
    # ACCESSORIES -- QATAR
    # =========================================================================
    {
        "rule_code": "BC-ACC-QATAR-001",
        "name": "VRF Refrigerant Copper Pipe Installation -- Qatar",
        "category": "ACCESSORIES",
        "geography": "QATAR",
        "scope_type": "ALL",
        "uom": "AED/m",
        "min_rate": 65,
        "mid_rate": 78,
        "max_rate": 95,
        "currency": "AED",
        "keywords": "copper pipe qatar,refrigerant pipe qatar,vrf piping qar,copper tubing qar",
        "notes": "Per linear metre installed. Includes insulation and pressure test. Qatar premium applies.",
        "priority": 10,
    },
    {
        "rule_code": "BC-ACC-QATAR-002",
        "name": "Pipe Hangers and Support Steelwork -- Qatar",
        "category": "ACCESSORIES",
        "geography": "QATAR",
        "scope_type": "ALL",
        "uom": "AED/Lot",
        "min_rate": 12000,
        "mid_rate": 16000,
        "max_rate": 22000,
        "currency": "AED",
        "keywords": "hanger qatar,pipe support qatar,steelwork support qar,bracket qatar,unistrut qar",
        "notes": "Per lot. Qatar rates higher than UAE due to QCC compliance and material import cost.",
        "priority": 20,
    },
    # =========================================================================
    # ACCESSORIES -- ALL (generic fallback)
    # =========================================================================
    {
        "rule_code": "BC-ACC-ALL-001",
        "name": "Refrigerant Piping and Accessories -- All",
        "category": "ACCESSORIES",
        "geography": "ALL",
        "scope_type": "ALL",
        "uom": "AED/m",
        "min_rate": 50,
        "mid_rate": 65,
        "max_rate": 90,
        "currency": "AED",
        "keywords": "copper pipe,refrigerant pipe,drain pipe,fittings,accessories,piping works",
        "notes": "Generic fallback per metre. Use geo-specific rule where available.",
        "priority": 100,
    },
    # =========================================================================
    # INSTALLATION -- UAE
    # =========================================================================
    {
        "rule_code": "BC-INST-UAE-001",
        "name": "HVAC Complete Installation -- UAE",
        "category": "INSTALLATION",
        "geography": "UAE",
        "scope_type": "SITC",
        "uom": "AED/Lot",
        "min_rate": 35000,
        "mid_rate": 42000,
        "max_rate": 55000,
        "currency": "AED",
        "keywords": "installation,labour,fix,fixing,erection,mechanical work,pipework,hvac installation,complete installation",
        "notes": "Per lot for a typical HVAC system (100-200TR). Includes mechanical, pipe, and duct install.",
        "priority": 10,
    },
    {
        "rule_code": "BC-INST-UAE-002",
        "name": "Electrical Works for HVAC -- UAE",
        "category": "INSTALLATION",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/Lot",
        "min_rate": 8000,
        "mid_rate": 12000,
        "max_rate": 18000,
        "currency": "AED",
        "keywords": "electrical,wiring,mcb,panel,switchboard,power supply,electrical works hvac",
        "notes": "Per lot. Electrical DB, cabling, and power connection to HVAC units.",
        "priority": 20,
    },
    # =========================================================================
    # INSTALLATION -- KSA
    # =========================================================================
    {
        "rule_code": "BC-INST-KSA-001",
        "name": "HVAC Installation and Commissioning -- KSA",
        "category": "INSTALLATION",
        "geography": "KSA",
        "scope_type": "SITC",
        "uom": "AED/Lot",
        "min_rate": 30000,
        "mid_rate": 35000,
        "max_rate": 45000,
        "currency": "AED",
        "keywords": "installation ksa,labour ksa,hvac installation ksa,fix ksa,erection ksa",
        "notes": "Per lot for KSA HVAC installation. Slightly lower than UAE owing to lower labour cost.",
        "priority": 10,
    },
    # =========================================================================
    # INSTALLATION -- QATAR
    # =========================================================================
    {
        "rule_code": "BC-INST-QATAR-001",
        "name": "HVAC Complete Installation Labour -- Qatar",
        "category": "INSTALLATION",
        "geography": "QATAR",
        "scope_type": "ITC",
        "uom": "AED/Lot",
        "min_rate": 22000,
        "mid_rate": 26000,
        "max_rate": 34000,
        "currency": "AED",
        "keywords": "installation labour qatar,fix qatar,itc qatar,erection qatar,hvac labour qar",
        "notes": "Per lot for ITC scope (equipment supplied by others). Qatar premium applies.",
        "priority": 10,
    },
    # =========================================================================
    # INSTALLATION -- ALL (generic fallback)
    # =========================================================================
    {
        "rule_code": "BC-INST-ALL-001",
        "name": "HVAC General Installation -- All Geographies",
        "category": "INSTALLATION",
        "geography": "ALL",
        "scope_type": "ALL",
        "uom": "AED/Lot",
        "min_rate": 25000,
        "mid_rate": 35000,
        "max_rate": 50000,
        "currency": "AED",
        "keywords": "installation,labour,install,erection,mechanical,fix,pipework,ductwork installation",
        "notes": "Generic fallback per lot. Use geo-specific rules where available.",
        "priority": 100,
    },
    # =========================================================================
    # TESTING AND COMMISSIONING (TC) -- UAE
    # =========================================================================
    {
        "rule_code": "BC-TC-UAE-001",
        "name": "Testing and Commissioning -- UAE",
        "category": "TC",
        "geography": "UAE",
        "scope_type": "ALL",
        "uom": "AED/Lot",
        "min_rate": 8000,
        "mid_rate": 11000,
        "max_rate": 15000,
        "currency": "AED",
        "keywords": "testing,commissioning,t&c,t and c,balancing,startup,hvac commissioning,duct balancing",
        "notes": "Per lot. Includes air and water balancing, TAB report, and handover documents.",
        "priority": 10,
    },
    # =========================================================================
    # TESTING AND COMMISSIONING (TC) -- KSA
    # =========================================================================
    {
        "rule_code": "BC-TC-KSA-001",
        "name": "T&C and Commissioning Report -- KSA",
        "category": "TC",
        "geography": "KSA",
        "scope_type": "ALL",
        "uom": "AED/Lot",
        "min_rate": 7000,
        "mid_rate": 9000,
        "max_rate": 12000,
        "currency": "AED",
        "keywords": "testing commissioning ksa,t and c ksa,tab ksa,startup ksa,commissioning report",
        "notes": "Per lot. Includes TAB and commissioning report for KSA SASO compliance.",
        "priority": 10,
    },
    # =========================================================================
    # TESTING AND COMMISSIONING (TC) -- QATAR
    # =========================================================================
    {
        "rule_code": "BC-TC-QATAR-001",
        "name": "Testing Commissioning and Handover -- Qatar",
        "category": "TC",
        "geography": "QATAR",
        "scope_type": "ALL",
        "uom": "AED/Lot",
        "min_rate": 6000,
        "mid_rate": 7000,
        "max_rate": 9500,
        "currency": "AED",
        "keywords": "testing commissioning qatar,t&c qatar,handover qatar,qcc testing,balancing qatar",
        "notes": "Per lot. ITC scope -- equipment by others. Includes QCC witness test and handover.",
        "priority": 10,
    },
    # =========================================================================
    # TESTING AND COMMISSIONING (TC) -- ALL (generic fallback)
    # =========================================================================
    {
        "rule_code": "BC-TC-ALL-001",
        "name": "Testing and Commissioning -- All Geographies",
        "category": "TC",
        "geography": "ALL",
        "scope_type": "ALL",
        "uom": "AED/Lot",
        "min_rate": 6000,
        "mid_rate": 9000,
        "max_rate": 14000,
        "currency": "AED",
        "keywords": "testing,commissioning,t&c,balancing,startup,tab,witness test",
        "notes": "Generic fallback per lot. Use geo-specific rule where available.",
        "priority": 100,
    },
]


class Command(BaseCommand):
    help = "Seed default BenchmarkCorridorRule records"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete all existing corridor rules before seeding",
        )

    def handle(self, *args, **options):
        if options["clear"]:
            deleted, _ = BenchmarkCorridorRule.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"Cleared {deleted} existing corridor rules."))

        created_count = 0
        updated_count = 0

        for data in CORRIDORS:
            notes = data.pop("notes", "")
            rule_code = data["rule_code"]
            kwargs = {k: v for k, v in data.items() if k != "rule_code"}
            if notes:
                kwargs["notes"] = notes

            obj, created = BenchmarkCorridorRule.objects.update_or_create(
                rule_code=rule_code,
                defaults=kwargs,
            )
            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {created_count} created, {updated_count} updated. "
                f"Total corridor rules: {BenchmarkCorridorRule.objects.count()}"
            )
        )
