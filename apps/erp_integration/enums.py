"""ERP Integration enums — extends core enums for ERP-specific concepts."""
from django.db import models


class ERPSourceType(models.TextChoices):
    """Source of a resolved ERP value."""
    API = "API", "ERP API"
    DB_FALLBACK = "DB_FALLBACK", "Database Fallback"
    CACHE = "CACHE", "Cache"
    NONE = "NONE", "Not Resolved"


class ERPConnectorType(models.TextChoices):
    """Supported ERP connector types."""
    CUSTOM = "CUSTOM", "Custom ERP"
    DYNAMICS = "DYNAMICS", "Microsoft Dynamics"
    ZOHO = "ZOHO", "Zoho"
    SALESFORCE = "SALESFORCE", "Salesforce"


class ERPConnectionStatus(models.TextChoices):
    """ERP connection health status."""
    ACTIVE = "ACTIVE", "Active"
    INACTIVE = "INACTIVE", "Inactive"
    ERROR = "ERROR", "Error"


class ERPResolutionType(models.TextChoices):
    """Type of ERP resolution performed."""
    VENDOR = "VENDOR", "Vendor Lookup"
    PO = "PO", "Purchase Order Lookup"
    GRN = "GRN", "GRN Lookup"
    ITEM = "ITEM", "Item Lookup"
    TAX = "TAX", "Tax Code Lookup"
    COST_CENTER = "COST_CENTER", "Cost Center Lookup"
    DUPLICATE_INVOICE = "DUPLICATE_INVOICE", "Duplicate Invoice Check"


class ERPSubmissionType(models.TextChoices):
    """Type of ERP submission."""
    CREATE_INVOICE = "CREATE_INVOICE", "Create Invoice"
    PARK_INVOICE = "PARK_INVOICE", "Park Invoice"
    GET_STATUS = "GET_STATUS", "Get Posting Status"


class ERPSubmissionStatus(models.TextChoices):
    """Status of an ERP submission attempt."""
    SUCCESS = "SUCCESS", "Success"
    FAILED = "FAILED", "Failed"
    TIMEOUT = "TIMEOUT", "Timeout"
    UNSUPPORTED = "UNSUPPORTED", "Unsupported"
