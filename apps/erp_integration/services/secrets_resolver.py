"""Secrets resolver — resolves secret references from environment variables.

Phase 1: Environment variables only.
Future: Integrate with Azure Key Vault, AWS Secrets Manager, etc.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def resolve_secret(reference: str) -> str:
    """Resolve a secret reference to its actual value.

    Phase 1 implementation reads from environment variables.
    Raises ``KeyError`` if the env var is not set.
    """
    value = os.environ.get(reference)
    if value is None:
        logger.error("Secret reference not found in environment: %s", reference)
        raise KeyError(f"Environment variable '{reference}' is not set")
    return value
