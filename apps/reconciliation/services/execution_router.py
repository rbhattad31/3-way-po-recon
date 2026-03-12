"""Execution router — dispatches reconciliation to 2-way or 3-way pipeline.

The router accepts a resolved mode (from ReconciliationModeResolver) and
delegates to the appropriate match service. Its output is a unified
dataclass that downstream consumers can handle identically regardless of
which mode was used.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Union

from apps.core.enums import ReconciliationMode
from apps.documents.models import Invoice
from apps.reconciliation.services.grn_match_service import GRNMatchResult
from apps.reconciliation.services.header_match_service import HeaderMatchResult
from apps.reconciliation.services.line_match_service import LineMatchResult
from apps.reconciliation.services.mode_resolver import ModeResolutionResult
from apps.reconciliation.services.po_lookup_service import POLookupResult
from apps.reconciliation.services.three_way_match_service import (
    ThreeWayMatchOutput,
    ThreeWayMatchService,
)
from apps.reconciliation.services.tolerance_engine import ToleranceEngine
from apps.reconciliation.services.two_way_match_service import (
    TwoWayMatchOutput,
    TwoWayMatchService,
)

logger = logging.getLogger(__name__)


@dataclass
class RoutedMatchOutput:
    """Unified output from the execution router.

    Provides a consistent interface regardless of whether the 2-way or
    3-way pipeline was executed. All downstream services (classification,
    exception builder, result service) consume these fields directly.
    """

    mode: str  # ReconciliationMode value
    po_result: POLookupResult
    header_result: Optional[HeaderMatchResult] = None
    line_result: Optional[LineMatchResult] = None
    grn_result: Optional[GRNMatchResult] = field(default=None)
    grn_required: bool = True
    grn_checked: bool = False
    mode_resolution: Optional[ModeResolutionResult] = None

    @classmethod
    def from_two_way(
        cls,
        output: TwoWayMatchOutput,
        mode_resolution: ModeResolutionResult,
    ) -> "RoutedMatchOutput":
        return cls(
            mode=ReconciliationMode.TWO_WAY,
            po_result=output.po_result,
            header_result=output.header_result,
            line_result=output.line_result,
            grn_result=output.grn_result,
            grn_required=output.grn_required,
            grn_checked=output.grn_checked,
            mode_resolution=mode_resolution,
        )

    @classmethod
    def from_three_way(
        cls,
        output: ThreeWayMatchOutput,
        mode_resolution: ModeResolutionResult,
    ) -> "RoutedMatchOutput":
        return cls(
            mode=ReconciliationMode.THREE_WAY,
            po_result=output.po_result,
            header_result=output.header_result,
            line_result=output.line_result,
            grn_result=output.grn_result,
            grn_required=output.grn_required,
            grn_checked=output.grn_checked,
            mode_resolution=mode_resolution,
        )


class ReconciliationExecutionRouter:
    """Route a single invoice reconciliation to the correct match pipeline.

    Usage::

        router = ReconciliationExecutionRouter(tolerance_engine)
        output = router.execute(invoice, po_result, mode_resolution)
        # output.mode -> "TWO_WAY" or "THREE_WAY"
        # output.header_result, output.line_result, output.grn_result
    """

    def __init__(self, tolerance_engine: ToleranceEngine):
        self._two_way = TwoWayMatchService(tolerance_engine)
        self._three_way = ThreeWayMatchService(tolerance_engine)

    def execute(
        self,
        invoice: Invoice,
        po_result: POLookupResult,
        mode_resolution: ModeResolutionResult,
    ) -> RoutedMatchOutput:
        """Dispatch to the appropriate match service.

        Args:
            invoice: The invoice being reconciled.
            po_result: PO lookup result (may have ``found=False``).
            mode_resolution: Resolved reconciliation mode and metadata.

        Returns:
            RoutedMatchOutput with all match results + mode metadata.
        """
        if not po_result.found:
            logger.info(
                "Router: PO not found for invoice %s — returning early (mode=%s)",
                invoice.pk, mode_resolution.mode,
            )
            return RoutedMatchOutput(
                mode=mode_resolution.mode,
                po_result=po_result,
                grn_required=mode_resolution.grn_required,
                mode_resolution=mode_resolution,
            )

        mode = mode_resolution.mode

        if mode == ReconciliationMode.TWO_WAY:
            logger.info(
                "Router: dispatching invoice %s to 2-way pipeline (reason=%s)",
                invoice.pk, mode_resolution.reason,
            )
            two_way_output = self._two_way.match(invoice, po_result)
            return RoutedMatchOutput.from_two_way(two_way_output, mode_resolution)

        # Default: THREE_WAY
        logger.info(
            "Router: dispatching invoice %s to 3-way pipeline (reason=%s)",
            invoice.pk, mode_resolution.reason,
        )
        three_way_output = self._three_way.match(invoice, po_result)
        return RoutedMatchOutput.from_three_way(three_way_output, mode_resolution)
