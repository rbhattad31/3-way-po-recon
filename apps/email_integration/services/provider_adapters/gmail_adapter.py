"""Gmail adapter implementation using Google API v2.0."""
from __future__ import annotations

import base64
import json
import logging
from typing import Any, Dict, List, Optional

import requests

# Optional Google API imports - graceful fallback if not installed
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google.oauth2.service_account import Credentials as ServiceAccountCredentials
    _GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    _GOOGLE_LIBS_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.info("Google Auth libraries not installed - Gmail adapter will use fallback mode")

from apps.email_integration.services.provider_adapters.base import BaseEmailProviderAdapter

logger = logging.getLogger(__name__)


class GmailEmailAdapter(BaseEmailProviderAdapter):
    """Production Gmail API adapter using Google API v2.0 with OAuth2/Service Account."""

    GMAIL_API_URL = "https://www.googleapis.com/gmail/v1"
    TOKEN_URI = "https://oauth2.googleapis.com/token"

    def subscribe_mailbox(self, mailbox_config) -> Dict[str, Any]:
        """Test mailbox connectivity and return metadata."""
        try:
            token = self._get_access_token(mailbox_config)
            if not token:
                return {"success": False, "error": "Failed to obtain access token"}

            profile_resp = self._request_gmail(token, "GET", "/users/me/profile")
            if not profile_resp.get("success"):
                return {"success": False, "error": profile_resp.get("error", "Unknown error")}

            profile_data = profile_resp.get("data", {})
            return {
                "success": True,
                "subscribed": True,
                "provider": "GMAIL",
                "mailbox_id": mailbox_config.pk,
                "mailbox_address": profile_data.get("emailAddress", ""),
                "message_count": profile_data.get("messagesTotal", -1),
                "thread_count": profile_data.get("threadsTotal", -1),
            }
        except Exception as e:
            logger.exception("Gmail subscribe_mailbox failed: %s", e)
            return {"success": False, "error": str(e)}

    def poll_messages(
        self, mailbox_config, since_cursor: Optional[str] = None, max_results: int = 100
    ) -> List[Dict[str, Any]]:
        """Poll messages from Gmail with pagination support."""
        try:
            token = self._get_access_token(mailbox_config)
            if not token:
                return []

            # Search for all messages, ordered by newest first
            query = "in:inbox"  # Can be extended with date filters
            search_resp = self._request_gmail(
                token,
                "GET",
                "/users/me/messages",
                params={"q": query, "maxResults": max_results, "pageToken": since_cursor},
            )

            if not search_resp.get("success"):
                logger.warning("Gmail poll search failed: %s", search_resp.get("error"))
                return []

            message_ids = search_resp.get("data", {}).get("messages", [])
            if not message_ids:
                return []

            # Fetch metadata for each message
            messages = []
            for msg_ref in message_ids[:max_results]:
                msg_data = self.get_message(mailbox_config, msg_ref.get("id"))
                if msg_data:
                    messages.append(msg_data)

            return messages
        except Exception as e:
            logger.exception("Gmail poll_messages failed: %s", e)
            return []

    def get_message(self, mailbox_config, provider_message_id: str) -> Dict[str, Any]:
        """Fetch full message details from Gmail."""
        try:
            token = self._get_access_token(mailbox_config)
            if not token:
                return {}

            msg_resp = self._request_gmail(
                token, "GET", f"/users/me/messages/{provider_message_id}", params={"format": "full"}
            )

            if not msg_resp.get("success"):
                return {}

            msg_payload = msg_resp.get("data", {})
            headers = {h["name"]: h["value"] for h in msg_payload.get("payload", {}).get("headers", [])}

            # Decode body
            body_text = ""
            body_html = ""
            if "parts" in msg_payload.get("payload", {}):
                for part in msg_payload["payload"]["parts"]:
                    if part.get("mimeType") == "text/plain":
                        data = part.get("body", {}).get("data", "")
                        body_text = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                    elif part.get("mimeType") == "text/html":
                        data = part.get("body", {}).get("data", "")
                        body_html = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
            else:
                data = msg_payload.get("payload", {}).get("body", {}).get("data", "")
                if data:
                    body_text = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

            return {
                "provider_message_id": provider_message_id,
                "internet_message_id": headers.get("Message-ID", ""),
                "subject": headers.get("Subject", "(no subject)"),
                "from_email": headers.get("From", ""),
                "to_emails": [e.strip() for e in headers.get("To", "").split(",")] if headers.get("To") else [],
                "cc_emails": [e.strip() for e in headers.get("Cc", "").split(",")] if headers.get("Cc") else [],
                "bcc_emails": [e.strip() for e in headers.get("Bcc", "").split(",")] if headers.get("Bcc") else [],
                "reply_to_emails": [e.strip() for e in headers.get("Reply-To", "").split(",")] if headers.get("Reply-To") else [],
                "sent_at": headers.get("Date", ""),
                "received_at": headers.get("Date", ""),
                "body_text": body_text,
                "body_html": body_html,
                "headers": headers,
                "thread_id": msg_payload.get("threadId", ""),
            }
        except Exception as e:
            logger.exception("Gmail get_message failed for %s: %s", provider_message_id, e)
            return {}

    def get_attachments(self, mailbox_config, provider_message_id: str) -> List[Dict[str, Any]]:
        """Fetch attachments for a message."""
        try:
            token = self._get_access_token(mailbox_config)
            if not token:
                return []

            msg_resp = self._request_gmail(
                token, "GET", f"/users/me/messages/{provider_message_id}", params={"format": "full"}
            )

            if not msg_resp.get("success"):
                return []

            attachments = []
            msg_payload = msg_resp.get("data", {})

            def extract_attachments(parts):
                for part in parts or []:
                    if part.get("filename"):
                        attachments.append({
                            "filename": part.get("filename", ""),
                            "mime_type": part.get("mimeType", ""),
                            "size_bytes": part.get("size", 0),
                            "attachment_id": part.get("body", {}).get("attachmentId", ""),
                            "provider_message_id": provider_message_id,
                        })
                    if "parts" in part:
                        extract_attachments(part["parts"])

            extract_attachments(msg_payload.get("payload", {}).get("parts", []))
            return attachments
        except Exception as e:
            logger.exception("Gmail get_attachments failed for %s: %s", provider_message_id, e)
            return []

    def send_message(self, mailbox_config, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send a message via Gmail."""
        try:
            token = self._get_access_token(mailbox_config)
            if not token:
                return {"success": False, "error": "Failed to obtain access token"}

            import email
            from email.mime.text import MIMEText

            msg = MIMEText(payload.get("body_text", ""))
            msg["to"] = ", ".join(payload.get("to_emails", []))
            msg["cc"] = ", ".join(payload.get("cc_emails", []))
            msg["subject"] = payload.get("subject", "")
            msg["from"] = payload.get("from_email", "")

            raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

            resp = self._request_gmail(
                token,
                "POST",
                "/users/me/messages/send",
                json={"raw": raw_message},
            )

            if resp.get("success"):
                return {
                    "success": True,
                    "provider_message_id": resp.get("data", {}).get("id", ""),
                    "thread_id": resp.get("data", {}).get("threadId", ""),
                }
            return {"success": False, "error": resp.get("error", "Unknown error")}
        except Exception as e:
            logger.exception("Gmail send_message failed: %s", e)
            return {"success": False, "error": str(e)}

    # ----- Internal helpers -----

    def _get_access_token(self, mailbox_config) -> Optional[str]:
        """Get or refresh OAuth token for Gmail."""
        if not _GOOGLE_LIBS_AVAILABLE:
            logger.error("Google Auth libraries required for Gmail adapter - install google-auth")
            return None

        config = mailbox_config.config_json or {}

        # Service account flow
        if config.get("auth_mode") == "SERVICE_ACCOUNT":
            try:
                sa_credentials = ServiceAccountCredentials.from_service_account_info(
                    config.get("service_account_json", {}),
                    scopes=["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"],
                )
                sa_credentials.refresh(Request())
                return sa_credentials.token
            except Exception as e:
                logger.exception("Service account token refresh failed: %s", e)
                return None

        # OAuth2 flow with refresh token
        try:
            if not config.get("refresh_token"):
                return None

            refresh_data = {
                "client_id": config.get("client_id", ""),
                "client_secret": config.get("client_secret", ""),
                "refresh_token": config.get("refresh_token", ""),
                "grant_type": "refresh_token",
            }

            resp = requests.post(self.TOKEN_URI, data=refresh_data, timeout=10)
            resp.raise_for_status()

            token_data = resp.json()
            # Update config with new token (optional)
            return token_data.get("access_token")
        except Exception as e:
            logger.exception("OAuth token refresh failed: %s", e)
            return None

    def _request_gmail(
        self, token: str, method: str, endpoint: str, params: Dict[str, Any] = None, json: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Make authenticated request to Gmail API."""
        try:
            headers = {"Authorization": f"Bearer {token}"}
            url = f"{self.GMAIL_API_URL}{endpoint}"

            if method == "GET":
                resp = requests.get(url, headers=headers, params=params, timeout=30)
            elif method == "POST":
                resp = requests.post(url, headers=headers, json=json, params=params, timeout=30)
            else:
                return {"success": False, "error": f"Unsupported method: {method}"}

            resp.raise_for_status()
            return {"success": True, "data": resp.json()}
        except requests.exceptions.RequestException as e:
            logger.exception("Gmail API request failed: %s", e)
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.exception("Unexpected error in _request_gmail: %s", e)
            return {"success": False, "error": str(e)}
