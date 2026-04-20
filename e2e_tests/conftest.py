"""
Shared fixtures for all end-to-end tests.
Uses config.test_settings (SQLite in-memory).
"""

import os
import django
import pytest
from django.core.management import call_command

os.environ["DJANGO_SETTINGS_MODULE"] = "config.test_settings"
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "True")

django.setup()

from django.test import Client  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from apps.accounts.models import CompanyProfile  # noqa: E402

User = get_user_model()


@pytest.fixture(scope="session", autouse=True)
def seed_e2e_baseline_data(django_db_setup, django_db_blocker):
    """Seed baseline RBAC/agent/email data used by e2e tests.

    This keeps seed-dependent tests runnable in isolated test DBs.
    """
    with django_db_blocker.unblock():
        for cmd in ("seed_rbac", "seed_agent_contracts", "seed_email_data"):
            try:
                call_command(cmd)
            except Exception:
                # Keep suite resilient when optional seed command dependencies are absent.
                pass

        tenant, _ = CompanyProfile.objects.get_or_create(
            name="E2E Test Tenant",
            defaults={"currency": "SAR", "country": "SA", "is_active": True},
        )

        from apps.accounts.rbac_models import Role, Permission
        role_codes = ["ADMIN", "AP_PROCESSOR", "REVIEWER", "FINANCE_MANAGER", "AUDITOR"]
        for idx, code in enumerate(role_codes, start=1):
            Role.objects.get_or_create(
                code=code,
                defaults={
                    "name": code.replace("_", " ").title(),
                    "description": f"E2E seed role {code}",
                    "is_system_role": True,
                    "is_active": True,
                    "rank": idx,
                },
            )

        for idx in range(1, 21):
            Permission.objects.get_or_create(
                code=f"e2e.permission_{idx}",
                defaults={
                    "name": f"E2E Permission {idx}",
                    "module": "e2e",
                    "action": f"action_{idx}",
                    "description": "E2E seeded permission",
                    "is_active": True,
                },
            )

        from apps.agents.models import AgentDefinition
        from apps.core.enums import AgentType
        for agent_type in list(AgentType)[:5]:
            AgentDefinition.objects.get_or_create(
                agent_type=agent_type,
                defaults={
                    "tenant": tenant,
                    "name": f"E2E {agent_type}",
                    "description": "E2E seeded agent definition",
                    "enabled": True,
                    "purpose": "E2E test purpose",
                    "lifecycle_status": "active",
                },
            )

        from apps.email_integration.models import (
            MailboxConfig,
            EmailThread,
            EmailMessage,
            EmailTemplate,
        )
        from apps.email_integration.enums import (
            EmailProvider,
            MailboxType,
            MailboxAuthMode,
            TargetDomain,
            EmailDirection,
            EmailTemplateDomainScope,
        )

        mailboxes = []
        for idx in range(1, 3):
            mailbox, _ = MailboxConfig.objects.get_or_create(
                tenant=tenant,
                mailbox_address=f"mailbox{idx}@test.local",
                defaults={
                    "name": f"E2E Mailbox {idx}",
                    "provider": EmailProvider.MICROSOFT_365,
                    "mailbox_type": MailboxType.SHARED,
                    "auth_mode": MailboxAuthMode.OAUTH,
                    "default_domain_route": TargetDomain.TRIAGE,
                    "is_inbound_enabled": True,
                    "is_outbound_enabled": True,
                    "is_active": True,
                },
            )
            mailboxes.append(mailbox)

        for idx in range(1, 4):
            mailbox = mailboxes[(idx - 1) % len(mailboxes)]
            thread, _ = EmailThread.objects.get_or_create(
                tenant=tenant,
                mailbox=mailbox,
                provider_thread_id=f"e2e-thread-{idx}",
                defaults={
                    "normalized_subject": f"E2E Thread {idx}",
                    "message_count": 0,
                },
            )
            for msg_idx in range(1, 4):
                EmailMessage.objects.get_or_create(
                    tenant=tenant,
                    mailbox=mailbox,
                    thread=thread,
                    provider_message_id=f"e2e-msg-{idx}-{msg_idx}",
                    defaults={
                        "direction": EmailDirection.INBOUND,
                        "subject": f"E2E Message {idx}-{msg_idx}",
                        "from_email": "sender@test.local",
                        "body_text": "E2E seeded message",
                    },
                )

        template_codes = [
            "AP_VENDOR_CLARIFICATION",
            "PROCUREMENT_SUPPLIER_CLARIFICATION",
            "E2E_TEMPLATE_3",
            "E2E_TEMPLATE_4",
            "E2E_TEMPLATE_5",
            "E2E_TEMPLATE_6",
        ]
        for code in template_codes:
            EmailTemplate.objects.get_or_create(
                tenant=tenant,
                template_code=code,
                defaults={
                    "template_name": code.replace("_", " ").title(),
                    "domain_scope": EmailTemplateDomainScope.GLOBAL,
                    "subject_template": f"Subject for {code}",
                    "body_text_template": "Hello {{name}}",
                    "is_active": True,
                },
            )


@pytest.fixture
def tenant(db):
    obj, _ = CompanyProfile.objects.get_or_create(
        name="E2E Test Tenant",
        defaults={"currency": "SAR", "country": "SA", "is_active": True},
    )
    return obj


@pytest.fixture
def admin_user(db, tenant):
    user, _ = User.objects.get_or_create(
        email="e2e_admin@test.local",
        defaults={
            "first_name": "E2E",
            "last_name": "Admin",
            "is_staff": True,
            "is_superuser": True,
            "company": tenant,
            "is_active": True,
            "is_platform_admin": True,
        },
    )
    user.set_password("e2eTestPass#2026")
    user.save()
    return user


@pytest.fixture
def ap_user(db, tenant):
    user, _ = User.objects.get_or_create(
        email="e2e_ap@test.local",
        defaults={
            "first_name": "AP",
            "last_name": "User",
            "is_staff": False,
            "is_superuser": False,
            "company": tenant,
            "is_active": True,
            "is_platform_admin": False,
        },
    )
    user.set_password("e2eTestPass#2026")
    user.save()
    return user


@pytest.fixture
def admin_client(admin_user):
    c = Client()
    c.force_login(admin_user)
    return c


@pytest.fixture
def ap_client(ap_user):
    c = Client()
    c.force_login(ap_user)
    return c


@pytest.fixture
def anon_client():
    return Client()


def assert_page_ok(response, url_hint="", allow_redirect=False):
    allowed = {200, 302} if allow_redirect else {200}
    assert response.status_code in allowed, (
        f"URL '{url_hint}' returned {response.status_code}. Expected {allowed}."
    )


def assert_api_ok(response, url_hint=""):
    assert response.status_code in (200, 201), (
        f"API '{url_hint}' returned {response.status_code}."
    )


def assert_no_500(response, url_hint=""):
    assert response.status_code != 500, (
        f"URL '{url_hint}' returned 500 Server Error."
    )
