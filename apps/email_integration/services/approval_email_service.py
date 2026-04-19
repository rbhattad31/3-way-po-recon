"""Approval-response extraction for email actions."""
from __future__ import annotations


class ApprovalEmailService:
    """Parses simple approve/reject signals from inbound responses."""

    @staticmethod
    def parse_approval_action(body_text: str) -> str:
        content = (body_text or "").lower()
        if any(token in content for token in ["approved", "approve", "ok to proceed"]):
            return "APPROVED"
        if any(token in content for token in ["rejected", "reject", "do not proceed"]):
            return "REJECTED"
        return "UNKNOWN"
