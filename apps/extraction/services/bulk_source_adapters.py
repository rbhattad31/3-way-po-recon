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
        self.lf_trace = None

    @abstractmethod
    def validate_config(self) -> ValidationResult:
        """Validate that the source configuration is correct and accessible."""

    def test_connection(self) -> ValidationResult:
        """Test live connectivity. Default delegates to validate_config."""
        return self.validate_config()

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

    def test_connection(self) -> ValidationResult:
        """Test Google Drive connectivity using raw service-account JSON (from form)."""
        _lf_span = None
        try:
            from apps.core.langfuse_client import start_span
            if self.lf_trace:
                _lf_span = start_span(
                    self.lf_trace,
                    name="gdrive_test_connection",
                    metadata={"folder_id": self.config.get("folder_id", "")},
                )
        except Exception:
            pass

        try:
            import json as _json

            folder_id = self.config.get("folder_id", "").strip()
            if not folder_id:
                return ValidationResult(valid=False, message="Drive Folder ID is required.")

            raw_creds = (
                self.config.get("service_account_json", "")
                or self.config.get("credentials_json", "")
            ).strip()
            if not raw_creds:
                return ValidationResult(valid=False, message="Service account credentials are required.")

            try:
                from google.oauth2 import service_account
                from googleapiclient.discovery import build
            except ImportError:
                return ValidationResult(
                    valid=False,
                    message=(
                        "google-api-python-client is not installed. "
                        "Run: pip install google-api-python-client google-auth"
                    ),
                )

            try:
                if raw_creds.startswith("{"):
                    info = _json.loads(raw_creds)
                else:
                    # Treat as file path fallback
                    if not os.path.isfile(raw_creds):
                        return ValidationResult(valid=False, message=f"Credentials file not found: {raw_creds}")
                    with open(raw_creds) as fh:
                        info = _json.load(fh)

                creds = service_account.Credentials.from_service_account_info(
                    info,
                    scopes=["https://www.googleapis.com/auth/drive.readonly"],
                )
                svc = build("drive", "v3", credentials=creds)
                svc.files().get(fileId=folder_id, fields="id,name").execute()
                return ValidationResult(valid=True, message="Connected to Google Drive successfully.")
            except Exception as exc:
                return ValidationResult(valid=False, message=f"Google Drive connection failed: {exc}")
        finally:
            try:
                from apps.core.langfuse_client import end_span
                if _lf_span:
                    end_span(_lf_span)
            except Exception:
                pass

    def _get_service(self):
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            self.config["credentials_json"],
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        return build("drive", "v3", credentials=creds)

    def list_files(self) -> List[DiscoveredFile]:
        _lf_span = None
        try:
            from apps.core.langfuse_client import start_span
            if self.lf_trace:
                _lf_span = start_span(
                    self.lf_trace,
                    name="gdrive_list_files",
                    metadata={"folder_id": self.config.get("folder_id", "")},
                )
        except Exception:
            pass

        discovered: List[DiscoveredFile] = []
        try:
            service = self._get_service()
            folder_id = self.config["folder_id"]

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
        finally:
            try:
                from apps.core.langfuse_client import end_span
                if _lf_span:
                    end_span(_lf_span, output={"file_count": len(discovered)})
            except Exception:
                pass

    def download_file(self, file_ref: DiscoveredFile) -> DownloadedFile:
        _lf_span = None
        try:
            from apps.core.langfuse_client import start_span
            if self.lf_trace:
                _lf_span = start_span(
                    self.lf_trace,
                    name="gdrive_download_file",
                    metadata={"file_id": file_ref.file_id, "file_name": file_ref.name},
                )
        except Exception:
            pass

        try:
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
        finally:
            try:
                from apps.core.langfuse_client import end_span
                if _lf_span:
                    end_span(_lf_span)
            except Exception:
                pass


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
        for key in ("tenant_id", "client_id", "client_secret", "drive_id"):
            if not self.config.get(key, "").strip():
                return ValidationResult(valid=False, message=f"{key} is required")
        if not self.config.get("folder_path") and not self.config.get("folder_id"):
            return ValidationResult(
                valid=False,
                message="Either folder_path or folder_id is required",
            )
        return ValidationResult(valid=True)

    def test_connection(self) -> ValidationResult:
        """Test OneDrive connectivity and verify the target folder is accessible."""
        _lf_span = None
        try:
            from apps.core.langfuse_client import start_span
            if self.lf_trace:
                _lf_span = start_span(
                    self.lf_trace,
                    name="onedrive_test_connection",
                    metadata={
                        "drive_id": self.config.get("drive_id", ""),
                        "folder_path": self.config.get("folder_path", ""),
                    },
                )
        except Exception:
            pass

        try:
            result = self.validate_config()
            if not result.valid:
                return result
            drive_id = self.config.get("drive_id", "").strip()
            if not drive_id:
                return ValidationResult(valid=False, message="Drive ID is required for OneDrive (client credentials cannot use /me/drive).")
            try:
                import requests as _req
                token = self._get_access_token()
                headers = {"Authorization": f"Bearer {token}"}

                # Resolve the specific target folder (supports nested paths e.g. Finance-Agents/Agents1)
                folder_id = self.config.get("folder_id", "").strip()
                folder_path = self.config.get("folder_path", "").strip("/")

                if folder_id:
                    folder_url = f"{self.GRAPH_BASE}/drives/{drive_id}/items/{folder_id}"
                    display_target = folder_id
                elif folder_path:
                    # Graph API path navigation: /drives/{id}/root:/path/to/folder
                    # Supports any depth e.g. Finance-Agents/Agents1
                    folder_url = f"{self.GRAPH_BASE}/drives/{drive_id}/root:/{folder_path}"
                    display_target = folder_path
                else:
                    return ValidationResult(valid=False, message="Either folder_path or folder_id is required.")

                folder_resp = _req.get(folder_url, headers=headers, timeout=15)
                if folder_resp.status_code == 404:
                    return ValidationResult(
                        valid=False,
                        message=f"Folder not found: '{display_target}'. Check the path is correct (e.g. Finance-Agents/Agents1).",
                    )
                folder_resp.raise_for_status()
                data = folder_resp.json()
                folder_name = data.get("name", display_target)
                parent = data.get("parentReference", {}).get("path", "")
                # Strip the /drives/{id}/root: prefix from the parent path for display
                if "root:" in parent:
                    parent = parent.split("root:", 1)[-1].strip("/")
                full_path = f"{parent}/{folder_name}".strip("/") if parent else folder_name

                return ValidationResult(
                    valid=True,
                    message=f"Folder found: '{full_path}'.",
                )
            except Exception as exc:
                return ValidationResult(valid=False, message=f"OneDrive connection failed: {exc}")
        finally:
            try:
                from apps.core.langfuse_client import end_span
                if _lf_span:
                    end_span(_lf_span)
            except Exception:
                pass

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
        _lf_span = None
        try:
            from apps.core.langfuse_client import start_span
            if self.lf_trace:
                _lf_span = start_span(
                    self.lf_trace,
                    name="onedrive_list_files",
                    metadata={
                        "drive_id": self.config.get("drive_id", ""),
                        "folder_path": self.config.get("folder_path", ""),
                    },
                )
        except Exception:
            pass

        discovered: List[DiscoveredFile] = []
        try:
            import requests

            headers = self._headers()
            drive_id = self.config.get("drive_id", "").strip()

            if not drive_id:
                raise ValueError(
                    "OneDrive source requires a Drive ID. "
                    "Find it via: Graph Explorer -> /me/drives or /sites/{site-id}/drives"
                )

            if self.config.get("folder_id"):
                url = f"{self.GRAPH_BASE}/drives/{drive_id}/items/{self.config['folder_id']}/children"
            else:
                folder_path = self.config["folder_path"].strip("/")
                url = f"{self.GRAPH_BASE}/drives/{drive_id}/root:/{folder_path}:/children"

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
        finally:
            try:
                from apps.core.langfuse_client import end_span
                if _lf_span:
                    end_span(_lf_span, output={"file_count": len(discovered)})
            except Exception:
                pass

    def download_file(self, file_ref: DiscoveredFile) -> DownloadedFile:
        _lf_span = None
        try:
            from apps.core.langfuse_client import start_span
            if self.lf_trace:
                _lf_span = start_span(
                    self.lf_trace,
                    name="onedrive_download_file",
                    metadata={"file_id": file_ref.file_id, "file_name": file_ref.name},
                )
        except Exception:
            pass

        try:
            import requests

            headers = self._headers()
            drive_id = self.config.get("drive_id", "").strip()

            if drive_id:
                url = f"{self.GRAPH_BASE}/drives/{drive_id}/items/{file_ref.file_id}/content"
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
        finally:
            try:
                from apps.core.langfuse_client import end_span
                if _lf_span:
                    end_span(_lf_span)
            except Exception:
                pass


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
