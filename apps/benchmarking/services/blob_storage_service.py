"""
Azure Blob Storage service for benchmarking quotation files.

Uploads PDF files to Azure Blob Storage and returns the blob URL.
Falls back gracefully (no-op) when the SDK is not installed or credentials
are not configured.

Container used: AZURE_BLOB_CONTAINER_NAME (default: finance-agents)
Blob prefix   : benchmarking/quotations/<year>/<month>/

Usage:
    from apps.benchmarking.services.blob_storage_service import BlobStorageService

    blob_name, blob_url = BlobStorageService.upload_quotation(
        file_path_or_bytes,
        filename="supplier_q_001.pdf",
        request_ref="BM-2026-001",
    )

    BlobStorageService.delete_blob(blob_name)
"""
from __future__ import annotations

import io
import logging
import os
import time
from datetime import datetime
from typing import Union

logger = logging.getLogger(__name__)


class BlobStorageService:
    """Upload / manage quotation PDFs in Azure Blob Storage."""

    BENCHMARKING_PREFIX = "benchmarking/quotations"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def upload_quotation(
        cls,
        source: Union[str, bytes, io.IOBase],
        filename: str,
        request_ref: str = "",
    ) -> tuple[str, str]:
        """Upload a quotation PDF to Azure Blob Storage.

        Args:
            source    : Absolute file path (str), raw bytes, or file-like object.
            filename  : Original filename used to build the blob name.
            request_ref: Optional request reference used as a sub-directory.

        Returns:
            (blob_name, blob_url)  -- both empty strings on failure.
        """
        try:
            return cls._upload(source, filename, request_ref)
        except ImportError:
            logger.warning(
                "BlobStorageService: azure-storage-blob not installed -- skipping upload for '%s'",
                filename,
            )
            return "", ""
        except ValueError as exc:
            logger.warning(
                "BlobStorageService: Azure Blob not configured -- skipping upload for '%s': %s",
                filename,
                exc,
            )
            return "", ""
        except Exception:
            logger.exception(
                "BlobStorageService: Upload failed for '%s' -- skipping",
                filename,
            )
            return "", ""

    @classmethod
    def delete_blob(cls, blob_name: str) -> bool:
        """Delete a blob by name. Returns True on success, False on failure."""
        if not blob_name:
            return False
        try:
            return cls._delete(blob_name)
        except Exception:
            logger.exception("BlobStorageService: Delete failed for '%s'", blob_name)
            return False

    @classmethod
    def download_blob_bytes(cls, blob_name: str) -> bytes:
        """Download a blob and return its bytes."""
        if not blob_name:
            raise ValueError("blob_name is required")
        try:
            container_client = cls._get_container_client()
            blob_client = container_client.get_blob_client(blob_name)
            return blob_client.download_blob().readall()
        except Exception:
            logger.exception("BlobStorageService: Download failed for '%s'", blob_name)
            raise

    @classmethod
    def get_sas_url(cls, blob_name: str, expiry_hours: int = 24) -> str:
        """Return a time-limited SAS URL for the given blob (read-only).

        Returns empty string on failure.
        """
        if not blob_name:
            return ""
        try:
            return cls._generate_sas_url(blob_name, expiry_hours)
        except Exception as exc:
            logger.warning("BlobStorageService: SAS URL generation failed for '%s': %s", blob_name, exc)
            return ""

    # ------------------------------------------------------------------
    # Internal implementation
    # ------------------------------------------------------------------

    @classmethod
    def _get_service_client(cls):
        """Return a BlobServiceClient.  Raises ValueError if not configured."""
        from azure.storage.blob import BlobServiceClient
        from django.conf import settings

        conn_str = getattr(settings, "AZURE_BLOB_CONNECTION_STRING", "")
        if not conn_str:
            raise ValueError(
                "AZURE_BLOB_CONNECTION_STRING is not set. "
                "Add it to .env to enable Azure Blob Storage for benchmarking quotations."
            )
        return BlobServiceClient.from_connection_string(conn_str)

    @classmethod
    def _get_container_client(cls):
        from django.conf import settings

        service = cls._get_service_client()
        container_name = getattr(settings, "AZURE_BLOB_CONTAINER_NAME", "finance-agents")
        return service.get_container_client(container_name)

    @classmethod
    def _build_blob_name(cls, filename: str, request_ref: str = "") -> str:
        """Build a unique blob name under the benchmarking prefix."""
        now = datetime.utcnow()
        year_month = now.strftime("%Y/%m")
        # Sanitise filename
        safe_name = os.path.basename(filename).replace(" ", "_")
        ts = str(int(time.time()))
        if request_ref:
            safe_ref = request_ref.replace("/", "-").replace(" ", "_")
            blob_name = f"{cls.BENCHMARKING_PREFIX}/{year_month}/{safe_ref}_{ts}_{safe_name}"
        else:
            blob_name = f"{cls.BENCHMARKING_PREFIX}/{year_month}/{ts}_{safe_name}"
        return blob_name

    @classmethod
    def _upload(cls, source, filename: str, request_ref: str) -> tuple[str, str]:
        container_client = cls._get_container_client()
        blob_name = cls._build_blob_name(filename, request_ref)

        # Normalise source to bytes
        if isinstance(source, str):
            with open(source, "rb") as f:
                data = f.read()
        elif isinstance(source, (bytes, bytearray)):
            data = source
        else:
            data = source.read()

        blob_client = container_client.get_blob_client(blob_name)
        blob_client.upload_blob(
            data,
            overwrite=True,
            content_settings=cls._pdf_content_settings(),
        )

        blob_url = blob_client.url
        logger.info(
            "BlobStorageService: Uploaded '%s' -> '%s' (%d bytes)",
            filename,
            blob_url,
            len(data),
        )
        return blob_name, blob_url

    @classmethod
    def _delete(cls, blob_name: str) -> bool:
        container_client = cls._get_container_client()
        blob_client = container_client.get_blob_client(blob_name)
        blob_client.delete_blob(delete_snapshots="include")
        logger.info("BlobStorageService: Deleted blob '%s'", blob_name)
        return True

    @classmethod
    def _generate_sas_url(cls, blob_name: str, expiry_hours: int) -> str:
        from datetime import timedelta
        from azure.storage.blob import (
            BlobSasPermissions,
            generate_blob_sas,
        )
        from django.conf import settings

        conn_str = getattr(settings, "AZURE_BLOB_CONNECTION_STRING", "")
        if not conn_str:
            raise ValueError("AZURE_BLOB_CONNECTION_STRING not configured")

        # Parse account name and key from connection string
        parts = dict(p.split("=", 1) for p in conn_str.split(";") if "=" in p)
        account_name = parts.get("AccountName", "")
        account_key = parts.get("AccountKey", "")
        container_name = getattr(settings, "AZURE_BLOB_CONTAINER_NAME", "finance-agents")

        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=container_name,
            blob_name=blob_name,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.utcnow() + timedelta(hours=expiry_hours),
        )
        blob_endpoint = parts.get("BlobEndpoint", f"https://{account_name}.blob.core.windows.net")
        return f"{blob_endpoint}/{container_name}/{blob_name}?{sas_token}"

    @staticmethod
    def _pdf_content_settings():
        """Return ContentSettings for PDF blobs."""
        try:
            from azure.storage.blob import ContentSettings
            return ContentSettings(content_type="application/pdf")
        except Exception:
            return None
