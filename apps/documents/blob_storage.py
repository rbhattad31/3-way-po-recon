"""Azure Blob Storage helper for document uploads and retrieval."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Dict, Optional
from uuid import uuid4

from django.conf import settings
from django.utils import timezone

from azure.identity import ClientSecretCredential
from azure.storage.blob import BlobSasPermissions, BlobServiceClient, generate_blob_sas


class AzureBlobStorageService:
    """Handles upload and download operations for document files in Azure Blob Storage."""

    def __init__(self) -> None:
        self.container_name = getattr(settings, "AZURE_STORAGE_CONTAINER", "uploads")
        self.account_name = getattr(settings, "AZURE_STORAGE_ACCOUNT_NAME", "")
        self.account_key = getattr(settings, "AZURE_STORAGE_ACCOUNT_KEY", "")
        self.connection_string = getattr(settings, "AZURE_STORAGE_CONNECTION_STRING", "")
        self.service_client = self._build_service_client()
        if not self.account_name:
            self.account_name = self.service_client.account_name
        if not self.account_key and self.connection_string:
            self.account_key = self._extract_connection_string_value(self.connection_string, "AccountKey")
        self.container_client = self.service_client.get_container_client(self.container_name)
        if not self.container_client.exists():
            self.container_client.create_container()

    @staticmethod
    def _extract_connection_string_value(connection_string: str, key: str) -> str:
        for part in connection_string.split(";"):
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            if name.strip().lower() == key.lower():
                return value.strip()
        return ""

    @staticmethod
    def _build_service_client() -> BlobServiceClient:
        connection_string = getattr(settings, "AZURE_STORAGE_CONNECTION_STRING", "")
        if connection_string:
            return BlobServiceClient.from_connection_string(connection_string)

        account_name = getattr(settings, "AZURE_STORAGE_ACCOUNT_NAME", "")
        account_key = getattr(settings, "AZURE_STORAGE_ACCOUNT_KEY", "")
        if account_name and account_key:
            account_url = f"https://{account_name}.blob.core.windows.net"
            return BlobServiceClient(account_url=account_url, credential=account_key)

        tenant_id = getattr(settings, "AZURE_TENANT_ID", "")
        client_id = getattr(settings, "AZURE_CLIENT_ID", "")
        client_secret = getattr(settings, "AZURE_CLIENT_SECRET", "")
        if account_name and tenant_id and client_id and client_secret:
            credential = ClientSecretCredential(
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret=client_secret,
            )
            account_url = f"https://{account_name}.blob.core.windows.net"
            return BlobServiceClient(account_url=account_url, credential=credential)

        raise ValueError(
            "Azure Blob Storage credentials are not configured. "
            "Set AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_ACCOUNT_NAME + "
            "AZURE_STORAGE_ACCOUNT_KEY or service principal credentials."
        )

    def upload_file(
        self,
        stream: BinaryIO,
        *,
        original_filename: str,
        content_type: str,
        metadata: Optional[Dict[str, str]] = None,
        verify_exists: bool = True,
    ) -> Dict[str, str]:
        safe_filename = Path(original_filename).name.replace("\\", "_").replace("/", "_")
        date_folder = datetime.utcnow().strftime("%d-%m-%Y")
        unique_blob_name = f"{date_folder}/{safe_filename}"

        existing_blob = self.container_client.get_blob_client(unique_blob_name)
        if existing_blob.exists():
            stem = Path(safe_filename).stem
            suffix = Path(safe_filename).suffix
            unique_blob_name = f"{date_folder}/{stem}-{uuid4().hex[:8]}{suffix}"

        blob_client = self.container_client.get_blob_client(unique_blob_name)
        stream.seek(0)
        blob_client.upload_blob(
            stream,
            overwrite=True,
            content_type=content_type or "application/octet-stream",
            metadata=metadata or {},
        )

        if verify_exists and not blob_client.exists():
            raise ValueError("Blob upload verification failed; blob does not exist.")

        return {
            "blob_name": unique_blob_name,
            "container_name": self.container_name,
            "blob_url": blob_client.url,
            "uploaded_at": timezone.now().isoformat(),
        }

    def download_blob_to_temp_path(self, blob_name: str, *, original_filename: str = "") -> str:
        blob_client = self.container_client.get_blob_client(blob_name)
        if not blob_client.exists():
            raise FileNotFoundError(f"Blob not found: {blob_name}")

        suffix = Path(original_filename).suffix if original_filename else ""
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            data = blob_client.download_blob().readall()
            temp_file.write(data)
            return temp_file.name

    def download_blob_bytes(self, blob_name: str) -> bytes:
        blob_client = self.container_client.get_blob_client(blob_name)
        if not blob_client.exists():
            raise FileNotFoundError(f"Blob not found: {blob_name}")
        return blob_client.download_blob().readall()

    def generate_blob_sas_url(self, blob_name: str, *, expiry_minutes: int = 120) -> str:
        blob_client = self.container_client.get_blob_client(blob_name)

        if not self.account_name or not self.account_key:
            return blob_client.url

        sas_token = generate_blob_sas(
            account_name=self.account_name,
            account_key=self.account_key,
            container_name=self.container_name,
            blob_name=blob_name,
            permission=BlobSasPermissions(read=True),
            expiry=timezone.now() + timedelta(minutes=expiry_minutes),
        )
        return f"{blob_client.url}?{sas_token}"
