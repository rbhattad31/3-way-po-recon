"""Azure Blob Storage service for document management.

Handles uploading, downloading, and generating SAS URLs for documents
stored in Azure Blob Storage. Files are organized into folders:
  - input/       — original uploaded invoices
  - processed/   — successfully extracted and processed documents
  - exception/   — documents that failed processing
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)


def _get_blob_settings():
    """Return Azure Blob Storage settings, raising if not configured."""
    conn_str = getattr(settings, "AZURE_BLOB_CONNECTION_STRING", "")
    container = getattr(settings, "AZURE_BLOB_CONTAINER_NAME", "")
    if not conn_str or not container:
        raise ValueError(
            "Azure Blob Storage not configured. "
            "Set AZURE_BLOB_CONNECTION_STRING and AZURE_BLOB_CONTAINER_NAME env vars."
        )
    return conn_str, container


def _get_container_client():
    """Create and return a ContainerClient."""
    from azure.storage.blob import ContainerClient

    conn_str, container = _get_blob_settings()
    return ContainerClient.from_connection_string(conn_str, container_name=container)


def is_blob_storage_enabled() -> bool:
    """Check if Azure Blob Storage is configured and enabled."""
    conn_str = getattr(settings, "AZURE_BLOB_CONNECTION_STRING", "")
    container = getattr(settings, "AZURE_BLOB_CONTAINER_NAME", "")
    return bool(conn_str and container)


def build_blob_path(folder: str, original_filename: str, upload_id: int) -> str:
    """Build a unique blob path: {folder}/{YYYY}/{MM}/{upload_id}_{filename}."""
    now = datetime.now(timezone.utc)
    safe_name = original_filename.replace(" ", "_")
    return f"{folder}/{now.year}/{now.month:02d}/{upload_id}_{safe_name}"


def upload_to_blob(file_obj, blob_path: str, content_type: str = "") -> str:
    """Upload a file-like object to Azure Blob Storage.

    Args:
        file_obj: File-like object (Django UploadedFile or open file handle).
        blob_path: Target path in the container (e.g. 'input/2026/03/42_invoice.pdf').
        content_type: MIME type for the blob.

    Returns:
        The blob_path on success.
    """
    from azure.storage.blob import ContentSettings

    container_client = _get_container_client()
    blob_client = container_client.get_blob_client(blob_path)

    content_settings = ContentSettings(content_type=content_type) if content_type else None

    # Ensure we read from the start
    if hasattr(file_obj, "seek"):
        file_obj.seek(0)

    blob_client.upload_blob(
        file_obj,
        overwrite=True,
        content_settings=content_settings,
    )
    logger.info("Uploaded blob: %s", blob_path)
    return blob_path


def download_blob_to_tempfile(blob_path: str) -> str:
    """Download a blob to a temporary file and return its local path.

    The caller is responsible for cleaning up the temp file.
    """
    container_client = _get_container_client()
    blob_client = container_client.get_blob_client(blob_path)

    # Derive extension from blob name
    _, ext = os.path.splitext(blob_path)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    try:
        download_stream = blob_client.download_blob()
        tmp.write(download_stream.readall())
        tmp.flush()
        tmp.close()
        logger.info("Downloaded blob %s to %s", blob_path, tmp.name)
        return tmp.name
    except Exception:
        tmp.close()
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)
        raise


def generate_blob_sas_url(
    blob_path: str,
    expiry_minutes: int = 30,
    content_disposition: str = None,
) -> str:
    """Generate a time-limited SAS URL for reading a blob.

    Pass content_disposition (e.g. 'attachment; filename="file.pdf"') to
    force the browser to download the file instead of opening it inline.
    Azure embeds this as the ``rscd`` SAS query parameter.
    """
    from azure.storage.blob import BlobSasPermissions, generate_blob_sas

    conn_str, container = _get_blob_settings()

    # Parse account name and key from connection string
    parts = dict(part.split("=", 1) for part in conn_str.split(";") if "=" in part)
    account_name = parts.get("AccountName", "")
    account_key = parts.get("AccountKey", "")

    if not account_name or not account_key:
        raise ValueError("Cannot parse AccountName/AccountKey from connection string")

    sas_kwargs = {}
    if content_disposition:
        sas_kwargs["content_disposition"] = content_disposition

    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=container,
        blob_name=blob_path,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes),
        **sas_kwargs,
    )

    return f"https://{account_name}.blob.core.windows.net/{container}/{blob_path}?{sas_token}"


def move_blob(source_path: str, dest_path: str) -> str:
    """Copy a blob from source_path to dest_path and delete the source.

    Used to move documents between folders (e.g. input/ -> processed/).
    """
    container_client = _get_container_client()
    source_blob = container_client.get_blob_client(source_path)
    dest_blob = container_client.get_blob_client(dest_path)

    dest_blob.start_copy_from_url(source_blob.url)
    source_blob.delete_blob()
    logger.info("Moved blob %s -> %s", source_path, dest_path)
    return dest_path


def delete_blob(blob_path: str) -> None:
    """Delete a blob if it exists."""
    container_client = _get_container_client()
    blob_client = container_client.get_blob_client(blob_path)
    blob_client.delete_blob(delete_snapshots="include")
    logger.info("Deleted blob: %s", blob_path)
