"""Health check views for load balancer and monitoring probes."""
import time

from django.conf import settings
from django.db import connection
from django.http import JsonResponse


def health_check(request):
    """Basic liveness check — returns 200 if Django is running."""
    return JsonResponse({
        "status": "ok",
        "env": getattr(settings, "APP_ENV", "unknown"),
    })


def health_live(request):
    """Kubernetes-style liveness probe — process is alive."""
    return JsonResponse({"status": "ok"})


def health_ready(request):
    """Readiness check — verifies database, Redis, and Celery broker connectivity."""
    checks = {}
    timings = {}

    # Database
    t0 = time.monotonic()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
    timings["database_ms"] = round((time.monotonic() - t0) * 1000, 1)

    # Redis
    t0 = time.monotonic()
    try:
        import redis as redis_lib

        r = redis_lib.from_url(settings.CELERY_BROKER_URL)
        r.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"
    timings["redis_ms"] = round((time.monotonic() - t0) * 1000, 1)

    # Celery broker queue visibility
    t0 = time.monotonic()
    try:
        import redis as redis_lib

        r = redis_lib.from_url(settings.CELERY_BROKER_URL)
        queue_lengths = {}
        for q in ["default", "extraction", "reconciliation", "agents", "scheduled"]:
            queue_lengths[q] = r.llen(q)
        checks["celery_queues"] = queue_lengths
    except Exception as e:
        checks["celery_queues"] = f"error: {e}"
    timings["celery_queues_ms"] = round((time.monotonic() - t0) * 1000, 1)

    all_ok = checks.get("database") == "ok" and checks.get("redis") == "ok"
    status_code = 200 if all_ok else 503

    return JsonResponse(
        {
            "status": "ok" if all_ok else "degraded",
            "checks": checks,
            "timings": timings,
            "env": getattr(settings, "APP_ENV", "unknown"),
        },
        status=status_code,
    )
