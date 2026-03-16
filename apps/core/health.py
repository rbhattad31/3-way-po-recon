"""Health check views for load balancer and monitoring probes."""
from django.db import connection
from django.http import JsonResponse


def health_check(request):
    """Basic liveness check — returns 200 if Django is running."""
    return JsonResponse({"status": "ok"})


def health_ready(request):
    """Readiness check — verifies database and Redis connectivity."""
    checks = {}

    # Database
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    # Redis
    try:
        from django.core.cache import cache
        import redis as redis_lib
        from django.conf import settings

        r = redis_lib.from_url(settings.CELERY_BROKER_URL)
        r.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    all_ok = all(v == "ok" for v in checks.values())
    status_code = 200 if all_ok else 503

    return JsonResponse(
        {"status": "ok" if all_ok else "degraded", "checks": checks},
        status=status_code,
    )
