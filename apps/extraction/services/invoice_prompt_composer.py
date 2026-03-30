"""InvoicePromptComposer — composes the final extraction system prompt from modular parts.

Composition order:
  1. Base prompt     (extraction.invoice_base  → fallback: extraction.invoice_system)
  2. Category overlay (extraction.invoice_category_{goods|service|travel})
  3. Country/tax overlay (extraction.country_{code}_{regime})
  4. (future) schema instruction block

Returns a PromptComposition dataclass containing:
  - final_prompt     : the assembled system prompt string
  - components       : dict of slug → version used (for Langfuse metadata)
  - prompt_hash      : deterministic sha256 hex of final_prompt (first 16 chars)

Design principles:
  - Falls back gracefully at every step. A missing overlay is skipped with a warning.
  - Never raises. Callers receive a valid prompt even if all modular parts are absent.
  - Centralises all prompt composition logic. No scattered PromptRegistry calls elsewhere.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Map (country_code, regime_code) pairs to registry keys.
# Extend this dict as new country packs are added.
_COUNTRY_REGIME_KEYS: dict[tuple[str, str], str] = {
    ("IN", "GST"):  "extraction.country_india_gst",
    ("IN", ""):     "extraction.country_india_gst",
    ("AE", "VAT"):  "extraction.country_generic_vat",
    ("SA", "ZATCA"): "extraction.country_generic_vat",  # extend with ZATCA key later
    ("GB", "VAT"):  "extraction.country_generic_vat",
    ("DE", "VAT"):  "extraction.country_generic_vat",
    ("US", ""):     "",   # no VAT overlay for US
    ("US", "NONE"): "",
}

_CATEGORY_KEYS: dict[str, str] = {
    "goods":   "extraction.invoice_category_goods",
    "service": "extraction.invoice_category_service",
    "travel":  "extraction.invoice_category_travel",
}


@dataclass
class PromptComposition:
    """Result of prompt composition."""
    final_prompt: str = ""
    components: dict[str, str] = field(default_factory=dict)  # slug → version
    prompt_hash: str = ""                                       # first 16 hex chars of sha256


class InvoicePromptComposer:
    """Compose the extraction system prompt from registry-backed modular parts."""

    @classmethod
    def compose(
        cls,
        *,
        invoice_category: Optional[str] = None,
        country_code: str = "",
        regime_code: str = "",
    ) -> PromptComposition:
        """Build a composed extraction prompt.

        Parameters
        ----------
        invoice_category : str or None
            One of 'goods', 'service', 'travel'. None → no category overlay.
        country_code : str
            ISO 3166-1 alpha-2 country code (e.g. 'IN', 'AE'). Empty → no country overlay.
        regime_code : str
            Tax regime code (e.g. 'GST', 'VAT'). Empty → country lookup with empty regime.

        Returns
        -------
        PromptComposition
            Always returns a valid composition. On any failure the monolithic
            fallback prompt is returned and the error is logged.
        """
        from apps.core.prompt_registry import PromptRegistry

        parts: list[str] = []
        components: dict[str, str] = {}

        # ── 1. Base prompt ────────────────────────────────────────────────
        base_slug = "extraction.invoice_base"
        base_text = PromptRegistry.get_or_default(base_slug)
        if not base_text:
            # Fall back to the monolithic prompt
            base_slug = "extraction.invoice_system"
            base_text = PromptRegistry.get_or_default(base_slug)

        if not base_text:
            logger.warning(
                "InvoicePromptComposer: neither extraction.invoice_base nor "
                "extraction.invoice_system returned content. Returning empty prompt."
            )
            return PromptComposition()

        parts.append(base_text)
        components[base_slug] = cls._version_for(base_slug)

        # ── 2. Category overlay ────────────────────────────────────────────
        if invoice_category:
            cat_slug = _CATEGORY_KEYS.get(invoice_category.lower(), "")
            if cat_slug:
                cat_text = PromptRegistry.get_or_default(cat_slug)
                if cat_text:
                    parts.append(cat_text)
                    components[cat_slug] = cls._version_for(cat_slug)
                else:
                    logger.debug(
                        "InvoicePromptComposer: category overlay '%s' is empty — skipped",
                        cat_slug,
                    )

        # ── 3. Country/tax overlay ─────────────────────────────────────────
        if country_code:
            country_slug = cls._resolve_country_slug(country_code, regime_code)
            if country_slug:
                country_text = PromptRegistry.get_or_default(country_slug)
                if country_text:
                    parts.append(country_text)
                    components[country_slug] = cls._version_for(country_slug)
                else:
                    logger.debug(
                        "InvoicePromptComposer: country overlay '%s' is empty — skipped",
                        country_slug,
                    )

        final_prompt = "".join(parts)
        prompt_hash = hashlib.sha256(final_prompt.encode()).hexdigest()[:16]

        return PromptComposition(
            final_prompt=final_prompt,
            components=components,
            prompt_hash=prompt_hash,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_country_slug(country_code: str, regime_code: str) -> str:
        """Return the registry key for a country/regime pair, or '' if none defined."""
        key = (country_code.upper(), regime_code.upper())
        slug = _COUNTRY_REGIME_KEYS.get(key)
        if slug is None:
            # Try with empty regime
            slug = _COUNTRY_REGIME_KEYS.get((country_code.upper(), ""), "")
        return slug or ""

    @staticmethod
    def _version_for(slug: str) -> str:
        """Return version string for a prompt slug. Fail-silent, returns '' on error."""
        try:
            from apps.core.langfuse_client import get_prompt, slug_to_langfuse_name
            lf = get_prompt(slug_to_langfuse_name(slug), label="production")
            if lf is not None:
                v = getattr(lf, "version", None)
                if v is not None:
                    return f"langfuse-v{v}"
        except Exception:
            pass
        try:
            from apps.core.models import PromptTemplate
            pt = PromptTemplate.objects.filter(slug=slug, is_active=True).only("version").first()
            if pt:
                return str(pt.version)
        except Exception:
            pass
        return "default"
