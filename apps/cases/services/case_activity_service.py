"""Service for creating APCaseActivity records.

Provides a single, fail-silent helper that all case services can call to
log lightweight UI-level activity events on a case.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class CaseActivityService:
    """Thin facade for APCaseActivity creation."""

    @staticmethod
    def log(
        case,
        activity_type: str,
        description: str = "",
        actor=None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Create an APCaseActivity record.  Fail-silent -- never raises."""
        try:
            from apps.cases.models import APCaseActivity

            APCaseActivity.objects.create(
                case=case,
                tenant=getattr(case, "tenant", None),
                activity_type=activity_type,
                description=description,
                actor=actor,
                metadata=metadata or {},
            )
        except Exception:
            logger.debug(
                "CaseActivityService.log failed for case=%s type=%s (non-fatal)",
                getattr(case, "pk", "?"),
                activity_type,
                exc_info=True,
            )
