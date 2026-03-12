"""
Master data seeder — Vendors, Aliases, Users.

Creates deterministic, idempotent master data for McDonald's Saudi Arabia AP.
"""
from __future__ import annotations

import logging

from apps.accounts.models import User
from apps.core.enums import UserRole
from apps.vendors.models import Vendor, VendorAlias

from .constants import SEED_USERS, VENDORS_DATA

logger = logging.getLogger(__name__)


def seed_users() -> dict[str, User]:
    """Create or retrieve seed users. Returns {email_key: User}."""
    users: dict[str, User] = {}
    created = 0
    for data in SEED_USERS:
        user, was_created = User.objects.get_or_create(
            email=data["email"],
            defaults={
                "first_name": data["first_name"],
                "last_name": data["last_name"],
                "role": data["role"],
                "is_staff": data.get("is_staff", False),
                "is_superuser": data.get("is_superuser", False),
                "department": data.get("department", ""),
            },
        )
        if was_created:
            user.set_password("SeedPass123!")
            user.save(update_fields=["password"])
            created += 1
        # key like "ap_processor" from "ap.processor@mcd-ksa.com"
        key = data["email"].split("@")[0].replace(".", "_")
        users[key] = user
    logger.info("Users: %d created, %d total", created, len(users))
    return users


def seed_vendors(admin: User) -> dict[str, Vendor]:
    """Create or retrieve vendors. Returns {vendor_code: Vendor}."""
    vendors: dict[str, Vendor] = {}
    created = 0
    for v in VENDORS_DATA:
        vendor, was_created = Vendor.objects.get_or_create(
            code=v["code"],
            defaults={
                "name": v["name"],
                "normalized_name": v["name"].upper().strip(),
                "tax_id": v.get("tax_id", ""),
                "country": v.get("country", "Saudi Arabia"),
                "currency": v.get("currency", "SAR"),
                "payment_terms": v.get("payment_terms", ""),
                "contact_email": v.get("contact_email", ""),
                "address": f"{v['category']} supplier, Saudi Arabia",
                "created_by": admin,
            },
        )
        if was_created:
            created += 1
        vendors[v["code"]] = vendor
    logger.info("Vendors: %d created, %d total", created, len(vendors))
    return vendors


def seed_vendor_aliases(vendors: dict[str, Vendor], admin: User) -> int:
    """Create vendor aliases for extraction ambiguity testing."""
    total_created = 0
    for v_data in VENDORS_DATA:
        vendor = vendors.get(v_data["code"])
        if not vendor:
            continue
        for alias_name in v_data.get("aliases", []):
            normalized = alias_name.upper().strip()
            _, was_created = VendorAlias.objects.get_or_create(
                vendor=vendor,
                normalized_alias=normalized,
                defaults={
                    "alias_name": alias_name,
                    "source": "manual",
                    "created_by": admin,
                },
            )
            if was_created:
                total_created += 1
    logger.info("Vendor aliases: %d created", total_created)
    return total_created
