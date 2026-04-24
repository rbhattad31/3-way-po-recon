"""ERP Integration enums — extends core enums for ERP-specific concepts."""
from django.db import models


class ERPSourceType(models.TextChoices):
    """Source of a resolved ERP value.

    Source priority (highest to lowest freshness guarantee):
      API          -- Live call to external ERP system.
      CACHE        -- TTL-based in-DB cache of a recent API result.
      MIRROR_DB    -- Internal canonical mirror tables (documents.PurchaseOrder,
                      documents.GoodsReceiptNote). Updated when AP team loads
                      transactional documents from ERP.
      DB_FALLBACK  -- Imported ERP reference snapshots (ERPVendorReference,
                      ERPItemReference, ERPTaxCodeReference, ERPCostCenterReference,
                      ERPPOReference). Updated via Excel/CSV import batches.
      MANUAL_OVERRIDE -- Value was set or corrected by a human user.
      NONE         -- Resolution failed; no data returned.
    """
    API = "API", "ERP API"
    CACHE = "CACHE", "Cache"
    MIRROR_DB = "MIRROR_DB", "Internal ERP Mirror"
    DB_FALLBACK = "DB_FALLBACK", "Reference Import Snapshot"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE", "Manual Override"
    NONE = "NONE", "Not Resolved"


class ERPDataDomain(models.TextChoices):
    """Data domain used to select the appropriate freshness threshold."""
    TRANSACTIONAL = "TRANSACTIONAL", "Transactional (PO, GRN)"
    MASTER = "MASTER", "Master Reference (Vendor, Item, Tax, Cost Center)"


class ERPConnectorType(models.TextChoices):
    """Supported ERP connector types."""
    CUSTOM = "CUSTOM", "Custom ERP"
    SQLSERVER = "SQLSERVER", "SQL Server (Direct DB)"
    VOUCHER_SQLSERVER = "VOUCHER_SQLSERVER", "Voucher SQL Server (Legacy ERP)"
    MYSQL = "MYSQL", "MySQL / MariaDB (Direct DB)"
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


class ERPAuthType(models.TextChoices):
    """Authentication method for REST-based ERP connectors."""
    BEARER = "BEARER", "Bearer Token"
    BASIC = "BASIC", "Basic Auth"
    API_KEY = "API_KEY", "API Key Header"
    OAUTH2 = "OAUTH2", "OAuth 2.0"
