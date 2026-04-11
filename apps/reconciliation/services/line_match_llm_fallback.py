"""Optional LLM fallback resolver for unresolved / ambiguous line matches.

This module provides a clean extension point for future LLM-assisted line
matching. The ``LineMatchLLMFallbackService`` is a lightweight interface
that can be subclassed to wire in an actual LLM call.

The default implementation returns ``None`` -- deterministic-only mode.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from apps.documents.models import InvoiceLineItem, PurchaseOrderLineItem
from apps.reconciliation.services.line_match_types import (
    LineCandidateScore,
    LLMFallbackResult,
)

logger = logging.getLogger(__name__)


class LineMatchLLMFallbackService:
    """Base LLM fallback resolver.

    Override ``resolve()`` in a subclass to provide actual LLM-assisted
    matching. The base implementation always returns ``None`` (no-op).
    """

    def resolve(
        self,
        invoice_line: InvoiceLineItem,
        candidate_scores: List[LineCandidateScore],
        context: Optional[dict] = None,
    ) -> Optional[LLMFallbackResult]:
        """Attempt LLM-assisted resolution for an ambiguous / unresolved line.

        Args:
            invoice_line: The invoice line that could not be deterministically matched.
            candidate_scores: Scored PO-line candidates from the deterministic scorer.
            context: Optional dict with invoice/PO-level context (e.g. vendor, currency).

        Returns:
            ``LLMFallbackResult`` if the LLM returned a structured recommendation,
            or ``None`` if the fallback is not configured / chose not to act.
        """
        logger.debug(
            "LLM fallback not configured -- skipping for invoice line %s",
            invoice_line.pk,
        )
        return None
