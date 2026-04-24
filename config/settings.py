"""Django settings for PO Reconciliation project."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

from django.core.exceptions import ImproperlyConfigured

_secret_key = os.getenv("DJANGO_SECRET_KEY", "")
if not _secret_key:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY environment variable is not set. "
        "Generate one with: python -c \"from django.core.utils.crypto import get_random_string; "
        "print(get_random_string(50))\""
    )
SECRET_KEY = _secret_key

DEBUG = os.getenv("DJANGO_DEBUG", "False").lower() in ("true", "1", "yes")

ALLOWED_HOSTS = os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    "django_filters",
    "django_celery_results",
    # Local apps
    "apps.core",
    "apps.accounts",
    "apps.vendors",
    "apps.documents",
    "apps.extraction",
    "apps.reconciliation",
    "apps.agents",
    "apps.tools",
    "apps.reviews",  # migrations-only stub; models moved to apps.cases
    "apps.dashboard",
    "apps.reports",
    "apps.auditlog",
    "apps.integrations",
    "apps.cases",
    "apps.copilot",
    "apps.procurement",
    "apps.extraction_core",
    "apps.extraction_configs",
    "apps.posting",
    "apps.posting_core",
    "apps.erp_integration",
    "apps.email_integration",
    "apps.core_eval",
    "apps.benchmarking",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.DatabaseConnectionRecoveryMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.TenantMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.LoginRequiredMiddleware",
    "apps.core.middleware.RBACMiddleware",
    "apps.core.middleware.RequestTraceMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.pending_reviews",
                "apps.core.context_processors.rbac_context",
                "apps.core.context_processors.static_version",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------------------
# Database – MySQL
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.getenv("DB_NAME", "po_recon"),
        "USER": os.getenv("DB_USER", "root"),
        "PASSWORD": os.getenv("DB_PASSWORD"),  # Must be set via environment variable
        "HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": os.getenv("DB_PORT", "3306"),
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            "ssl_mode": "REQUIRED",
            "connect_timeout": int(os.getenv("DB_CONNECT_TIMEOUT", "15")),
        },
        "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "60")),
        "CONN_HEALTH_CHECKS": os.getenv("DB_CONN_HEALTH_CHECKS", "true").lower() == "true",
    }
}

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static & Media
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Cache-busting version counter — bump after static file changes
STATIC_VERSION = "1.1.4"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Invoice upload sub-directory
INVOICE_UPLOAD_DIR = "invoices/"

# ---------------------------------------------------------------------------
# Default primary key
# ---------------------------------------------------------------------------
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_RENDERER_CLASSES": (
        [
            "rest_framework.renderers.JSONRenderer",
            "rest_framework.renderers.BrowsableAPIRenderer",
        ]
        if DEBUG
        else [
            "rest_framework.renderers.JSONRenderer",
        ]
    ),
    "DATETIME_FORMAT": "%Y-%m-%dT%H:%M:%S%z",
}

# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------
# SECURITY: In production the broker URL MUST include authentication credentials.
# Use the rediss:// scheme for TLS, e.g.:
#   CELERY_BROKER_URL=rediss://:your-password@your-redis-host:6380/0
# The unauthenticated localhost default is intentional for local dev only and
# MUST be overridden via the CELERY_BROKER_URL environment variable in every
# non-development environment (staging, production, CI with an external Redis).
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = "django-db"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_DEFAULT_QUEUE = "default"
# Run tasks synchronously — enabled by default in dev/test; disabled in production.
# Override via CELERY_TASK_ALWAYS_EAGER env var if needed.
CELERY_TASK_ALWAYS_EAGER = os.getenv("CELERY_TASK_ALWAYS_EAGER", "True").lower() in ("true", "1", "yes")
CELERY_TASK_EAGER_PROPAGATES = CELERY_TASK_ALWAYS_EAGER

# ---------------------------------------------------------------------------
# ERP Integration
# ---------------------------------------------------------------------------
ERP_DUPLICATE_FALLBACK_CONFIDENCE_THRESHOLD = float(
    os.getenv("ERP_DUPLICATE_FALLBACK_CONFIDENCE_THRESHOLD", "0.8")
)
ERP_CACHE_TTL_SECONDS = int(os.getenv("ERP_CACHE_TTL_SECONDS", "3600"))

# ---------------------------------------------------------------------------
# ERP Shared Resolution Policy
# How stale can data be before we flag it or attempt a live refresh?
# TRANSACTIONAL = PO headers, GRN records  (short-lived, changes frequently)
# MASTER        = vendor/item/tax/cost-center reference data (more stable)
# ---------------------------------------------------------------------------
ERP_TRANSACTIONAL_FRESHNESS_HOURS = int(
    os.getenv("ERP_TRANSACTIONAL_FRESHNESS_HOURS", "24")
)
ERP_MASTER_FRESHNESS_HOURS = int(
    os.getenv("ERP_MASTER_FRESHNESS_HOURS", "168")  # 7 days
)
# When True, a live ERP API call is attempted if the mirror lookup misses.
ERP_ENABLE_LIVE_REFRESH_ON_MISS = (
    os.getenv("ERP_ENABLE_LIVE_REFRESH_ON_MISS", "false").lower() == "true"
)
# When True, a live ERP API call is attempted if the resolved data is stale.
ERP_ENABLE_LIVE_REFRESH_ON_STALE = (
    os.getenv("ERP_ENABLE_LIVE_REFRESH_ON_STALE", "false").lower() == "true"
)
# Use internal mirror tables (documents.PurchaseOrder / GoodsReceiptNote) as
# the primary source for reconciliation PO/GRN lookups.
ERP_RECON_USE_MIRROR_AS_PRIMARY = (
    os.getenv("ERP_RECON_USE_MIRROR_AS_PRIMARY", "true").lower() == "true"
)
# Use internal reference import tables as the primary source for posting
# vendor/item/tax/cost-center resolution.
ERP_POSTING_USE_MIRROR_AS_PRIMARY = (
    os.getenv("ERP_POSTING_USE_MIRROR_AS_PRIMARY", "true").lower() == "true"
)

# ---------------------------------------------------------------------------
# Email Integration
# ---------------------------------------------------------------------------
EMAIL_WEBHOOK_SHARED_SECRET = os.getenv("EMAIL_WEBHOOK_SHARED_SECRET", "")

# ---------------------------------------------------------------------------
# LLM / AI service configuration
# ---------------------------------------------------------------------------
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "azure_openai")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "")
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "gpt-4o")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))

# Perplexity AI -- used for live-web market intelligence research
# Model: sonar-pro (live web search + citations)
PERPLEXITY_API_KEY = os.getenv("PERPLEXITY_API_KEY")
PERPLEXITY_MODEL = os.getenv("PERPLEXITY_MODEL", "sonar")
BENCHMARKING_MARKET_AUTO_FETCH_ENABLED = (
    os.getenv("BENCHMARKING_MARKET_AUTO_FETCH_ENABLED", "true").lower() == "true"
)
# Set to True to activate the LLM-backed ReasoningPlanner for agent pipeline planning.
# When False (default), the deterministic PolicyEngine is always used.
AGENT_REASONING_ENGINE_ENABLED = os.getenv("AGENT_REASONING_ENGINE_ENABLED", "false").lower() == "true"

# Azure Document Intelligence (OCR)
AZURE_DI_ENDPOINT = os.getenv("AZURE_DI_ENDPOINT", "")
AZURE_DI_KEY = os.getenv("AZURE_DI_KEY", "")
# Set to false to skip Azure DI and use native PDF text extraction (PyPDF2).
# Useful for accuracy comparison. Runtime override via ExtractionRuntimeSettings.ocr_enabled.
EXTRACTION_OCR_ENABLED = os.getenv("EXTRACTION_OCR_ENABLED", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Azure Blob Storage (document storage)
# ---------------------------------------------------------------------------
AZURE_BLOB_CONNECTION_STRING = os.getenv("AZURE_BLOB_CONNECTION_STRING", "")
AZURE_BLOB_CONTAINER_NAME = os.getenv("AZURE_BLOB_CONTAINER_NAME", "finance-agents")

# ---------------------------------------------------------------------------
# Reconciliation defaults
# ---------------------------------------------------------------------------
DEFAULT_QTY_TOLERANCE_PCT = float(os.getenv("DEFAULT_QTY_TOLERANCE_PCT", "2.0"))
DEFAULT_PRICE_TOLERANCE_PCT = float(os.getenv("DEFAULT_PRICE_TOLERANCE_PCT", "1.0"))
DEFAULT_AMOUNT_TOLERANCE_PCT = float(os.getenv("DEFAULT_AMOUNT_TOLERANCE_PCT", "1.0"))
EXTRACTION_CONFIDENCE_THRESHOLD = float(os.getenv("EXTRACTION_CONFIDENCE_THRESHOLD", "0.75"))

# Extraction approval — human-in-the-loop gate
# Set to 1.1 (above max confidence) to require human approval for ALL extractions.
# Lower to e.g. 0.95 once confidence in the system grows, to auto-approve high-confidence results.
EXTRACTION_AUTO_APPROVE_THRESHOLD = float(os.getenv("EXTRACTION_AUTO_APPROVE_THRESHOLD", "0.80"))
EXTRACTION_AUTO_APPROVE_ENABLED = os.getenv("EXTRACTION_AUTO_APPROVE_ENABLED", "true").lower() == "true"

# Export field mapping
# Phase 1: deterministic mapping only (default false).
# Phase 2: set true to allow AI fallback only for unresolved fields.
EXPORT_MAPPING_AI_FALLBACK_ENABLED = (
    os.getenv("EXPORT_MAPPING_AI_FALLBACK_ENABLED", "false").lower() == "true"
)
EXPORT_MAPPING_AI_MIN_CONFIDENCE = float(
    os.getenv("EXPORT_MAPPING_AI_MIN_CONFIDENCE", "0.80")
)

LOKI_ENABLED = os.getenv("LOKI_ENABLED", "false").lower() == "true"
LOKI_URL = os.getenv("LOKI_URL", "http://localhost:3100/loki/api/v1/push")
LOKI_APP_LABEL = os.getenv("LOKI_APP_LABEL", "po-recon")
DJANGO_ENV = os.getenv("DJANGO_ENV", "dev")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_active_handlers = ["console", "file"]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
        "dev_traced": {
            "()": "apps.core.logging_utils.DevLogFormatter",
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
        "json": {
            "()": "apps.core.logging_utils.JSONLogFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "dev_traced" if DEBUG else "json",
        },
        "file": {
            "class": "apps.core.logging_utils.SafeRotatingFileHandler",
            "filename": BASE_DIR / "logs" / "po_recon.log",
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "json",
        },
    },
    "root": {
        "handlers": _active_handlers,
        "level": "INFO",
    },
    "filters": {
        "no_broken_pipe": {
            "()": "apps.core.logging_utils.BrokenPipeFilter",
        },
    },
    "loggers": {
        "django": {"handlers": _active_handlers, "level": "INFO", "propagate": False},
        "django.server": {
            "handlers": _active_handlers,
            "level": "INFO",
            "propagate": False,
            "filters": ["no_broken_pipe"],
        },
        "apps": {"handlers": _active_handlers, "level": "DEBUG", "propagate": False},
        "apps.observed": {"handlers": _active_handlers, "level": "INFO", "propagate": False},
        "apps.action": {"handlers": _active_handlers, "level": "INFO", "propagate": False},
        "apps.task": {"handlers": _active_handlers, "level": "INFO", "propagate": False},
    },
}

if LOKI_ENABLED:
    LOGGING["handlers"]["loki"] = {
        "class": "apps.core.logging_utils.SilentLokiHandler",
        "url": LOKI_URL,
        "tags": {
            "service": LOKI_APP_LABEL,
            "env": DJANGO_ENV,
        },
        "auth": (
            os.getenv("LOKI_USER", ""),
            os.getenv("LOKI_PASSWORD", ""),
        ),
        "version": "1",
        "formatter": "json",
    }
    _active_handlers.append("loki")
    LOGGING["root"]["handlers"] = _active_handlers
    for _logger_name in LOGGING.get("loggers", {}):
        LOGGING["loggers"][_logger_name]["handlers"] = _active_handlers
