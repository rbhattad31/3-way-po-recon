"""AP validation skill -- validate extraction, detect duplicates, verify vendor."""
from apps.agents.skills.base import Skill, register_skill

ap_validation_skill = register_skill(Skill(
    name="ap_validation",
    description="Validate extracted invoice data, detect duplicates, and verify vendor.",
    prompt_extension=(
        "## VALIDATE Phase\n"
        "After extraction, validate the data quality before matching.\n\n"
        "Steps:\n"
        "1. Call `validate_extraction` to check mandatory fields, format validity, "
        "and cross-field consistency.\n"
        "2. If validation finds repairable issues, call `repair_extraction` to "
        "auto-fix known patterns (e.g. date format, amount parsing).\n"
        "3. Call `check_duplicate` to verify this invoice has not been processed before.\n"
        "4. Call `verify_vendor` to confirm the vendor exists and matches by tax ID.\n"
        "5. Call `verify_tax_computation` to validate tax amounts against line totals.\n\n"
        "IMPORTANT: Verify vendor by tax ID, NOT by name alone. Name matching "
        "is unreliable due to abbreviations and aliases."
    ),
    tools=[
        "validate_extraction",
        "repair_extraction",
        "check_duplicate",
        "verify_vendor",
        "verify_tax_computation",
        "detect_self_company",
        "check_approval_status",
    ],
    decision_hints=[
        "If duplicate is detected, recommend SEND_TO_AP_REVIEW with evidence.",
        "If vendor verification fails by tax ID, do NOT auto-close.",
        "If validation finds critical missing fields, attempt re_extract_field "
        "before recommending REPROCESS_EXTRACTION.",
        "If vendor verification fails unexpectedly, call detect_self_company "
        "to check if vendor and buyer names are swapped.",
        "Call check_approval_status to verify extraction has been approved "
        "before proceeding to matching phase.",
    ],
))
