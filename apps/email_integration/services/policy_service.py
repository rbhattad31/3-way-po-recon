"""Policy checks for sender/domain-based mailbox governance."""
from __future__ import annotations


class EmailPolicyService:
    """Fail-closed policy checks used before processing or routing."""

    @staticmethod
    def is_sender_allowed(mailbox, from_email: str) -> bool:
        allowed_domains = mailbox.allowed_sender_domains_json or []
        if not allowed_domains:
            return True
        email = (from_email or "").strip().lower()
        if "@" not in email:
            return False
        sender_domain = email.split("@", 1)[1]
        normalized_allowed = {d.strip().lower() for d in allowed_domains if d}
        return sender_domain in normalized_allowed
