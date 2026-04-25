"""LLM-first email classification and intent extraction service."""
from __future__ import annotations

import json
import logging

from django.conf import settings

from apps.agents.services.llm_client import LLMClient, LLMMessage
from apps.core.decorators import observed_service
from apps.email_integration.enums import EmailIntentType, EmailMessageClassification, SenderTrustLevel

logger = logging.getLogger(__name__)


class ClassificationService:
    """LLM-first message classification with deterministic fallback."""

    VALID_CLASSIFICATIONS = {choice[0] for choice in EmailMessageClassification.choices}

    DETERMINISTIC_CONFIDENCE = {
        EmailMessageClassification.AP_INVOICE: 0.94,
        EmailMessageClassification.AP_SUPPORTING_DOCUMENT: 0.9,
        EmailMessageClassification.PROCUREMENT_QUOTATION: 0.93,
        EmailMessageClassification.PROCUREMENT_PROPOSAL: 0.86,
        EmailMessageClassification.PROCUREMENT_CLARIFICATION: 0.88,
        EmailMessageClassification.APPROVAL_RESPONSE: 0.91,
        EmailMessageClassification.INTERNAL_REVIEW_REPLY: 0.8,
        EmailMessageClassification.GENERAL_QUERY: 0.65,
        EmailMessageClassification.UNKNOWN: 0.2,
    }

    @classmethod
    def _ascii_text(cls, value: str) -> str:
        return (value or "").encode("ascii", "ignore").decode("ascii").strip()

    @classmethod
    def _llm_is_configured(cls) -> bool:
        provider = str(getattr(settings, "LLM_PROVIDER", "azure_openai") or "azure_openai").strip().lower()
        if provider == "azure_openai":
            return bool(
                str(getattr(settings, "AZURE_OPENAI_API_KEY", "") or "").strip()
                and str(getattr(settings, "AZURE_OPENAI_ENDPOINT", "") or "").strip()
                and str(getattr(settings, "AZURE_OPENAI_DEPLOYMENT", "") or "").strip()
            )
        return bool(str(getattr(settings, "OPENAI_API_KEY", "") or "").strip())

    @classmethod
    def _mailbox_llm_enabled(cls, mailbox=None) -> bool:
        if mailbox is None:
            return False
        config = getattr(mailbox, "config_json", {}) or {}
        return bool(config.get("llm_classification_enabled", False))

    @classmethod
    def _deterministic_classify(cls, subject: str, body_text: str) -> str:
        content = f"{subject or ''} {body_text or ''}".lower()
        if "invoice" in content:
            return EmailMessageClassification.AP_INVOICE
        if "quotation" in content or "quote" in content:
            return EmailMessageClassification.PROCUREMENT_QUOTATION
        if "approval" in content or "approved" in content or "reject" in content:
            return EmailMessageClassification.APPROVAL_RESPONSE
        if "clarification" in content:
            return EmailMessageClassification.PROCUREMENT_CLARIFICATION
        if "proposal" in content:
            return EmailMessageClassification.PROCUREMENT_PROPOSAL
        if "review" in content:
            return EmailMessageClassification.INTERNAL_REVIEW_REPLY
        if content.strip():
            return EmailMessageClassification.GENERAL_QUERY
        return EmailMessageClassification.UNKNOWN

    @classmethod
    def _deterministic_result(cls, subject: str, body_text: str) -> dict:
        classification = cls._deterministic_classify(subject, body_text)
        return {
            "classification": classification,
            "confidence": cls.DETERMINISTIC_CONFIDENCE.get(classification, 0.5),
            "llm_used": False,
            "source": "RULE",
            "reasoning_summary": "Rule-based keyword classification fallback.",
            "model_name": "",
        }

    @classmethod
    def _llm_prompt(cls, subject: str, body_text: str) -> list[LLMMessage]:
        allowed = sorted(cls.VALID_CLASSIFICATIONS)
        payload = {
            "subject": (subject or "")[:500],
            "body_text": (body_text or "")[:6000],
            "allowed_classifications": allowed,
        }
        return [
            LLMMessage(
                role="system",
                content=(
                    "Classify enterprise finance and procurement emails. "
                    "Return strict JSON only with keys: classification, confidence, reasoning_summary. "
                    "classification must be one of the allowed_classifications provided by the user. "
                    "confidence must be a number between 0 and 1. "
                    "reasoning_summary must be a short ASCII sentence."
                ),
            ),
            LLMMessage(role="user", content=json.dumps(payload)),
        ]

    @classmethod
    def _parse_llm_response(cls, content: str) -> dict | None:
        if not content:
            return None
        try:
            parsed = json.loads(content)
        except (TypeError, ValueError):
            return None

        classification = str(parsed.get("classification") or "").strip().upper()
        if classification not in cls.VALID_CLASSIFICATIONS:
            return None

        try:
            confidence = float(parsed.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        confidence = max(0.0, min(1.0, confidence))
        reasoning_summary = cls._ascii_text(str(parsed.get("reasoning_summary") or "LLM classification."))
        return {
            "classification": classification,
            "confidence": confidence or 0.75,
            "llm_used": True,
            "source": "LLM",
            "reasoning_summary": reasoning_summary or "LLM classification.",
        }

    @classmethod
    def _llm_result(cls, subject: str, body_text: str) -> dict | None:
        llm = LLMClient(temperature=0.0, max_tokens=220)
        response = llm.chat(
            messages=cls._llm_prompt(subject, body_text),
            response_format={"type": "json_object"},
        )
        parsed = cls._parse_llm_response(response.content)
        if not parsed:
            return None
        parsed["model_name"] = response.model or llm.model or ""
        return parsed

    @classmethod
    @observed_service("email.classification")
    def classify_with_metadata(cls, subject: str, body_text: str, *, mailbox=None) -> dict:
        fallback = cls._deterministic_result(subject, body_text)
        if not cls._mailbox_llm_enabled(mailbox) or not cls._llm_is_configured():
            return fallback

        try:
            llm_result = cls._llm_result(subject, body_text)
            if llm_result:
                llm_result["fallback_classification"] = fallback["classification"]
                return llm_result
        except Exception:
            logger.exception("Email LLM classification failed; using deterministic fallback")

        fallback["source"] = "RULE_FALLBACK"
        fallback["reasoning_summary"] = "LLM unavailable; used rule-based classification fallback."
        return fallback

    @classmethod
    def classify(cls, subject: str, body_text: str) -> str:
        return cls._deterministic_classify(subject, body_text)

    @staticmethod
    def infer_intent(classification: str) -> str:
        mapping = {
            EmailMessageClassification.AP_INVOICE: EmailIntentType.DOCUMENT_INGEST,
            EmailMessageClassification.AP_SUPPORTING_DOCUMENT: EmailIntentType.DOCUMENT_INGEST,
            EmailMessageClassification.PROCUREMENT_QUOTATION: EmailIntentType.DOCUMENT_INGEST,
            EmailMessageClassification.APPROVAL_RESPONSE: EmailIntentType.APPROVAL_ACTION,
            EmailMessageClassification.PROCUREMENT_CLARIFICATION: EmailIntentType.CLARIFICATION_RESPONSE,
        }
        return mapping.get(classification, EmailIntentType.MANUAL_TRIAGE)

    @staticmethod
    def infer_sender_trust(from_email: str, tenant_domains=None) -> str:
        email_lower = (from_email or "").strip().lower()
        if not email_lower or "@" not in email_lower:
            return SenderTrustLevel.UNKNOWN
        domain = email_lower.split("@", 1)[1]
        if tenant_domains and domain in {d.lower() for d in tenant_domains}:
            return SenderTrustLevel.TRUSTED_INTERNAL
        if any(k in domain for k in ["spam", "phish", "fraud"]):
            return SenderTrustLevel.SUSPICIOUS
        return SenderTrustLevel.KNOWN_EXTERNAL
