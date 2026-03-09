"""Abstract service contracts for future external integrations.

These are NOT implemented yet — they define the interface that real
integrations should conform to.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class POIngestPayload:
    po_number: str
    vendor_code: str
    vendor_name: str
    po_date: str
    currency: str = "USD"
    total_amount: float = 0.0
    lines: list[dict] = field(default_factory=list)


@dataclass
class GRNIngestPayload:
    grn_number: str
    po_number: str
    vendor_code: str
    receipt_date: str
    lines: list[dict] = field(default_factory=list)


@dataclass
class IngestResult:
    success: bool
    entity_id: Optional[int] = None
    message: str = ""


class BasePOIngestor(abc.ABC):
    """Contract for PO ingestion integrations (API or RPA)."""

    @abc.abstractmethod
    def ingest(self, payload: POIngestPayload) -> IngestResult:
        ...


class BaseGRNIngestor(abc.ABC):
    """Contract for GRN ingestion integrations (API or RPA)."""

    @abc.abstractmethod
    def ingest(self, payload: GRNIngestPayload) -> IngestResult:
        ...


class StubPOAPIIngestor(BasePOIngestor):
    """Placeholder — not yet connected to real endpoint."""

    def ingest(self, payload: POIngestPayload) -> IngestResult:
        return IngestResult(success=False, message="PO API ingestor not implemented")


class StubGRNAPIIngestor(BaseGRNIngestor):
    """Placeholder — not yet connected to real endpoint."""

    def ingest(self, payload: GRNIngestPayload) -> IngestResult:
        return IngestResult(success=False, message="GRN API ingestor not implemented")


class StubPORPAIngestor(BasePOIngestor):
    """Placeholder — not yet connected to RPA pipeline."""

    def ingest(self, payload: POIngestPayload) -> IngestResult:
        return IngestResult(success=False, message="PO RPA ingestor not implemented")


class StubGRNRPAIngestor(BaseGRNIngestor):
    """Placeholder — not yet connected to RPA pipeline."""

    def ingest(self, payload: GRNIngestPayload) -> IngestResult:
        return IngestResult(success=False, message="GRN RPA ingestor not implemented")
