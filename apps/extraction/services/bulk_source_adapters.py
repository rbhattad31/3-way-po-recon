"""Bulk source adapters for listing and downloading files.

Each adapter implements a common interface for scanning a source folder
and downloading individual files. Phase 1 supports:
- Local filesystem folders
- Google Drive folders
- OneDrive folders
"""
from __future__ import annotations

import logging
import mimetypes
import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Dict, List, Optional

logger = logging.getLogger(__name__)

# File types accepted for invoice extraction
SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/tiff",
}


@dataclass
class DiscoveredFile:
    """Metadata for a file discovered during source scanning."""
    file_id: str          # Unique identifier (path for local, cloud ID for drive)
    name: str             # Original filename
    path: str             # Full path or cloud path
    mime_type: str = ""
    size: int = 0
    is_supported: bool = True


@dataclass
class DownloadedFile:
    """Result of downloading a file from a source."""
    local_path: str       # Path to temporary local file
    original_name: str
    mime_type: str = ""
    size: int = 0


@dataclass
class ValidationResult:
    """Result of validating a source connection config."""
    valid: bool
    message: str = ""


def _is_supported_file(filename: str) -> bool:
    """Check if filename has a supported invoice extension."""
    ext = os.path.splitext(filename)[1].lower()
    return ext in SUPPORTED_EXTENSIONS


def _guess_mime_type(filename: str) -> str:
    """Guess MIME type from filename."""
    mime, _ = mimetypes.guess_type(filename)
    return mime or ""


class BaseBulkSourceAdapter(ABC):
    """Base adapter for scanning and downloading files from a bulk source."""

    def __init__(self, config: Dict):
        self.config = config

    @abstractmethod
    def validate_config(self) -> ValidationResult:
        """Validate that the source configuration is correct and accessible."""

    @abstractmethod
    def list_files(self) -> List[DiscoveredFile]:
        """List all files in the configured source folder.

        Returns only invoice-relevant file types.
        Skips unsupported types (marks is_supported=False returned separately).
        """

    @abstractmethod
    def download_file(self, file_ref: DiscoveredFile) -> DownloadedFile:
        """Download a single file to a local temporary location.

        Caller is responsible for cleaning up the temp file.
        """


class LocalFolderBulkSourceAdapter(BaseBulkSourceAdapter):
    """Adapter for scanning a local filesystem folder."""

    def validate_config(self) -> ValidationResult:
        folder_path = self.config.get("folder_path", "")
        if not folder_path:
            return ValidationResult(valid=False, message="folder_path is required")
        p = Path(folder_path)
        if not p.exists():
            return ValidationResult(valid=False, message=f"Folder does not exist: {folder_path}")
        if not p.is_dir():
            return ValidationResult(valid=False, message=f"Path is not a directory: {folder_path}")
        return ValidationResult(valid=True)

    def list_files(self) -> List[DiscoveredFile]:
        folder_path = self.config["folder_path"]
        discovered: List[DiscoveredFile] = []
        try:
            for entry in os.scandir(folder_path):
                if not entry.is_file():
                    continue
                supported = _is_supported_file(entry.name)
                stat = entry.stat()
                discovered.append(DiscoveredFile(
                    file_id=entry.path,
                    name=entry.name,
                    path=entry.path,
                    mime_type=_guess_mime_type(entry.name),
                    size=stat.st_size,
                    is_supported=supported,
                ))
        except OSError as e:
            logger.error("Error scanning folder %s: %s", folder_path, e)
            raise
        return discovered

    def download_file(self, file_ref: DiscoveredFile) -> DownloadedFile:
        src_path = file_ref.path
        if not os.path.isfile(src_path):
            raise FileNotFoundError(f"Source file not found: {src_path}")
        suffix = os.path.splitext(file_ref.name)[1]
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        shutil.copy2(src_path, tmp_path)
        return DownloadedFile(
            local_path=tmp_path,
            original_name=file_ref.name,
            mime_type=file_ref.mime_type,
            size=file_ref.size,
        )


class GoogleDriveBulkSourceAdapter(BaseBulkSourceAdapter):
    """Adapter for scanning a Google Drive folder.

    Requires config keys:
        folder_id: Google Drive folder ID
        credentials_json: Path to service account JSON key file

    Uses the Google Drive API v3 via google-api-python-client.
    """

    def validate_config(self) -> ValidationResult:
        folder_id = self.config.get("folder_id", "")
        if not folder_id:
            return ValidationResult(valid=False, message="folder_id is required")
        creds = self.config.get("credentials_json", "")
        if not creds:
            return ValidationResult(valid=False, message="credentials_json path is required")
        if not os.path.isfile(creds):
            return ValidationResult(valid=False, message=f"Credentials file not found: {creds}")
        return ValidationResult(valid=True)

    def _get_service(self):
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            self.config["credentials_json"],
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        return build("drive", "v3", credentials=creds)

    def list_files(self) -> List[DiscoveredFile]:
        service = self._get_service()
        folder_id = self.config["folder_id"]
        discovered: List[DiscoveredFile] = []

        query = f"'{folder_id}' in parents and trashed = false"
        page_token = None
        while True:
            resp = service.files().list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType, size)",
                pageSize=100,
                pageToken=page_token,
            ).execute()
            for f in resp.get("files", []):
                name = f["name"]
                supported = _is_supported_file(name)
                discovered.append(DiscoveredFile(
                    file_id=f["id"],
                    name=name,
                    path=f"gdrive://{folder_id}/{name}",
                    mime_type=f.get("mimeType", ""),
                    size=int(f.get("size", 0)),
                    is_supported=supported,
                ))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return discovered

    def download_file(self, file_ref: DiscoveredFile) -> DownloadedFile:
        from googleapiclient.http import MediaIoBaseDownload
        import io

        service = self._get_service()
        request = service.files().get_media(fileId=file_ref.file_id)
        suffix = os.path.splitext(file_ref.name)[1]
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        file_size = os.path.getsize(tmp_path)
        return DownloadedFile(
            local_path=tmp_path,
            original_name=file_ref.name,
            mime_type=file_ref.mime_type,
            size=file_size,
        )


class OneDriveBulkSourceAdapter(BaseBulkSourceAdapter):
    """Adapter for scanning a OneDrive / SharePoint folder.

    Requires config keys:
        tenant_id: Azure AD tenant ID
        client_id: App registration client ID
        client_secret: App registration client secret
        folder_path: OneDrive folder path (e.g. "/Invoices/Incoming")
        -- OR --
        folder_id: OneDrive item ID for the folder

    Uses Microsoft Graph API with client-credentials flow.
    """

    GRAPH_BASE = "https://graph.microsoft.com/v1.0"

    def validate_config(self) -> ValidationResult:
        for key in ("tenant_id", "client_id", "client_secret"):
            if not self.config.get(key):
                return ValidationResult(valid=False, message=f"{key} is required")
        if not self.config.get("folder_path") and not self.config.get("folder_id"):
            return ValidationResult(
                valid=False,
                message="Either folder_path or folder_id is required",
            )
        return ValidationResult(valid=True)

    def _get_access_token(self) -> str:
        import requests

        tenant_id = self.config["tenant_id"]
        url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
        resp = requests.post(url, data={
            "grant_type": "client_credentials",
            "client_id": self.config["client_id"],
            "client_secret": self.config["client_secret"],
            "scope": "https://graph.microsoft.com/.default",
        }, timeout=30)
        resp.raise_for_status()
        return resp.json()["access_token"]

    def _headers(self) -> Dict[str, str]:
        token = self._get_access_token()
        return {"Authorization": f"Bearer {token}"}

    def list_files(self) -> List[DiscoveredFile]:
        import requests

        headers = self._headers()
        drive_user = self.config.get("drive_user", "")

        if self.config.get("folder_id"):
            url = f"{self.GRAPH_BASE}/drives/{drive_user}/items/{self.config['folder_id']}/children"
        else:
            folder_path = self.config["folder_path"].strip("/")
            url = f"{self.GRAPH_BASE}/me/drive/root:/{folder_path}:/children"

        discovered: List[DiscoveredFile] = []
        while url:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("value", []):
                if "file" not in item:
                    continue  # skip folders
                name = item["name"]
                supported = _is_supported_file(name)
                discovered.append(DiscoveredFile(
                    file_id=item["id"],
                    name=name,
                    path=item.get("parentReference", {}).get("path", "") + "/" + name,
                    mime_type=item.get("file", {}).get("mimeType", _guess_mime_type(name)),
                    size=item.get("size", 0),
                    is_supported=supported,
                ))
            url = data.get("@odata.nextLink")
        return discovered

    def download_file(self, file_ref: DiscoveredFile) -> DownloadedFile:
        import requests

        headers = self._headers()
        drive_user = self.config.get("drive_user", "")

        if drive_user:
            url = f"{self.GRAPH_BASE}/drives/{drive_user}/items/{file_ref.file_id}/content"
        else:
            url = f"{self.GRAPH_BASE}/me/drive/items/{file_ref.file_id}/content"

        resp = requests.get(url, headers=headers, stream=True, timeout=60)
        resp.raise_for_status()

        suffix = os.path.splitext(file_ref.name)[1]
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=8192):
                    fh.write(chunk)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        file_size = os.path.getsize(tmp_path)
        return DownloadedFile(
            local_path=tmp_path,
            original_name=file_ref.name,
            mime_type=file_ref.mime_type,
            size=file_size,
        )


# Adapter registry
ADAPTER_REGISTRY = {
    "LOCAL_FOLDER": LocalFolderBulkSourceAdapter,
    "GOOGLE_DRIVE": GoogleDriveBulkSourceAdapter,
    "ONEDRIVE": OneDriveBulkSourceAdapter,
}


def get_adapter(source_type: str, config: Dict) -> BaseBulkSourceAdapter:
    """Factory: return the appropriate adapter for the given source type."""
    cls = ADAPTER_REGISTRY.get(source_type)
    if not cls:
        raise ValueError(f"Unsupported source type: {source_type}")
    return cls(config)
