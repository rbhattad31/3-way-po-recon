"""
Duplicate detection for ProcurementRequest.

Matching criteria (all must match):
  - Same tenant
  - Same domain_code
  - Same request_type
  - Same geography_country (case-insensitive, stripped)
  - Same title text (case-insensitive exact match OR one title starts-with the other)
  - Original request created within the last LOOKBACK_DAYS days
  - Original request status not CANCELLED
  - Original request is NOT itself a duplicate (is_duplicate=False)
  - Original request is not the same object as the new request
"""
import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 30


class DuplicateDetectionService:
    """Deterministic duplicate-request detector -- no LLM required."""

    @staticmethod
    def find_original(new_request):
        """
        Given a freshly-created ProcurementRequest, return an existing request
        that looks like its original, or None if no duplicate is found.

        The new_request must already be saved (pk must exist) so we can
        exclude it from the search.

        Returns: ProcurementRequest instance or None
        """
        try:
            from apps.procurement.models import ProcurementRequest

            cutoff = timezone.now() - timedelta(days=LOOKBACK_DAYS)
            title_clean = (new_request.title or "").strip().lower()
            country_clean = (new_request.geography_country or "").strip().lower()

            if not title_clean or not new_request.domain_code or not new_request.request_type:
                # Not enough data to match -- treat as unique
                return None

            candidates = (
                ProcurementRequest.objects
                .filter(
                    tenant=new_request.tenant,
                    domain_code=new_request.domain_code,
                    request_type=new_request.request_type,
                    is_duplicate=False,
                    created_at__gte=cutoff,
                )
                .exclude(pk=new_request.pk)
                .exclude(status="CANCELLED")
                .order_by("-created_at")
            )

            # Apply country filter only when both sides have a value
            if country_clean:
                candidates = candidates.filter(
                    geography_country__iexact=new_request.geography_country.strip()
                )

            for candidate in candidates:
                cand_title = (candidate.title or "").strip().lower()
                if not cand_title:
                    continue
                # Exact icase match OR one is a prefix/suffix of the other
                if (
                    cand_title == title_clean
                    or title_clean.startswith(cand_title)
                    or cand_title.startswith(title_clean)
                ):
                    logger.info(
                        "DuplicateDetectionService: request pk=%s matched as duplicate of pk=%s",
                        new_request.pk,
                        candidate.pk,
                    )
                    return candidate

            return None

        except Exception as exc:
            logger.warning(
                "DuplicateDetectionService.find_original failed (treating as unique): %s", exc
            )
            return None

    @staticmethod
    def mark_as_duplicate(new_request, original_request, save=True):
        """
        Mark new_request as a duplicate of original_request.
        Does NOT trigger any pipeline tasks -- caller is responsible for skipping them.
        """
        new_request.is_duplicate = True
        new_request.duplicate_of = original_request
        if save:
            new_request.save(update_fields=["is_duplicate", "duplicate_of"])
        logger.info(
            "DuplicateDetectionService: pk=%s marked as duplicate of pk=%s",
            new_request.pk,
            original_request.pk,
        )
