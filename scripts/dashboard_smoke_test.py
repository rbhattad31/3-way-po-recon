from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.test import Client  # noqa: E402


URLS = [
    "/dashboard/",
    "/dashboard/analytics/",
    "/dashboard/agents/",
    "/dashboard/agents/performance/",
    "/dashboard/agents/governance/",
]


def ensure_test_user():
    User = get_user_model()
    user = User.objects.filter(is_active=True).first()
    if user:
        return user
    return User.objects.create(
        email="dashboard-smoke@example.com",
        first_name="Dashboard",
        last_name="Smoke",
        role="ADMIN",
        is_active=True,
    )


def run() -> int:
    user = ensure_test_user()

    anon = Client(HTTP_HOST="localhost", raise_request_exception=False)
    auth = Client(HTTP_HOST="localhost", raise_request_exception=False)
    auth.force_login(user)

    print("=== Dashboard Smoke Test ===")
    print(f"User: {user.email} (role={getattr(user, 'role', '')})")

    print("\n[Anonymous Requests]")
    for url in URLS:
        response = anon.get(url)
        print(f"{url} -> {response.status_code}")

    print("\n[Authenticated Requests]")
    for url in URLS:
        response = auth.get(url)
        print(f"{url} -> {response.status_code}")

    return 0


if __name__ == "__main__":
    raise SystemExit(run())
