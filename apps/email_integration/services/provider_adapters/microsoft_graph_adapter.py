"""Microsoft Graph adapter implementation for Microsoft 365 mailboxes."""
from __future__ import annotations

import base64
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from apps.email_integration.services.provider_adapters.base import BaseEmailProviderAdapter


class MicrosoftGraphEmailAdapter(BaseEmailProviderAdapter):
    """Adapter implementation for Microsoft 365 Graph APIs."""

    GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
    TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    DEFAULT_TIMEOUT_SECONDS = 30
    DEFAULT_POLL_PAGE_SIZE = 25

    def _mailbox_config(self, mailbox_config) -> Dict[str, Any]:
        cfg = mailbox_config.config_json if isinstance(mailbox_config.config_json, dict) else {}
        return {
            "tenant_id": (cfg.get("tenant_id") or os.getenv("EMAIL_GRAPH_TENANT_ID") or "").strip(),
            "client_id": (cfg.get("client_id") or os.getenv("EMAIL_GRAPH_CLIENT_ID") or "").strip(),
            "client_secret": (cfg.get("client_secret") or os.getenv("EMAIL_GRAPH_CLIENT_SECRET") or "").strip(),
            "scope": (cfg.get("scope") or os.getenv("EMAIL_GRAPH_SCOPE") or "https://graph.microsoft.com/.default").strip(),
            "graph_base_url": (cfg.get("graph_base_url") or self.GRAPH_BASE_URL).rstrip("/"),
            "user_id": (cfg.get("user_id") or mailbox_config.mailbox_address or "").strip(),
            "timeout_seconds": int(cfg.get("timeout_seconds") or self.DEFAULT_TIMEOUT_SECONDS),
            "poll_page_size": int(cfg.get("poll_page_size") or self.DEFAULT_POLL_PAGE_SIZE),
        }

    @staticmethod
    def _iso_to_datetime(value: str | None):
        if not value:
            return None
        parsed = value.strip()
        if parsed.endswith("Z"):
            parsed = parsed[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(parsed)
        except ValueError:
            return None

    @staticmethod
    def _participants(raw_list: list | None) -> List[Dict[str, str]]:
        participants: List[Dict[str, str]] = []
        for item in raw_list or []:
            email_obj = item.get("emailAddress") if isinstance(item, dict) else None
            if not isinstance(email_obj, dict):
                continue
            email = (email_obj.get("address") or "").strip()
            name = (email_obj.get("name") or "").strip()
            if email:
                participants.append({"email": email, "name": name})
        return participants

    @staticmethod
    def _message_body(raw_message: dict) -> Dict[str, str]:
        body_obj = raw_message.get("body") if isinstance(raw_message.get("body"), dict) else {}
        content_type = (body_obj.get("contentType") or "").strip().upper()
        content = body_obj.get("content") or ""
        if content_type == "HTML":
            return {"body_text": "", "body_html": content}
        return {"body_text": content, "body_html": ""}

    def _get_access_token(self, mailbox_config) -> str:
        cfg = self._mailbox_config(mailbox_config)
        if not cfg["tenant_id"]:
            raise ValueError("Microsoft Graph tenant_id is required in mailbox config_json or EMAIL_GRAPH_TENANT_ID")
        if not cfg["client_id"]:
            raise ValueError("Microsoft Graph client_id is required in mailbox config_json or EMAIL_GRAPH_CLIENT_ID")
        if not cfg["client_secret"]:
            raise ValueError("Microsoft Graph client_secret is required in mailbox config_json or EMAIL_GRAPH_CLIENT_SECRET")

        token_url = self.TOKEN_URL_TEMPLATE.format(tenant_id=cfg["tenant_id"])
        response = requests.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "scope": cfg["scope"],
            },
            timeout=cfg["timeout_seconds"],
        )
        if response.status_code >= 400:
            raise ValueError(f"Graph token request failed with status={response.status_code}: {response.text[:300]}")
        payload = response.json()
        access_token = (payload.get("access_token") or "").strip()
        if not access_token:
            raise ValueError("Graph token response did not include access_token")
        return access_token

    def _request_graph_text(self, mailbox_config, method: str, url: str, *, params=None, extra_headers=None, _token: str | None = None) -> str:
        cfg = self._mailbox_config(mailbox_config)
        token = _token or self._get_access_token(mailbox_config)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        response = requests.request(
            method,
            url,
            headers=headers,
            params=params,
            timeout=cfg["timeout_seconds"],
        )
        if response.status_code >= 400:
            raise ValueError(f"Graph request failed method={method} status={response.status_code}: {response.text[:300]}")
        return response.text or ""

    def _request_graph(self, mailbox_config, method: str, url: str, *, params=None, payload=None, _token: str | None = None) -> dict:
        cfg = self._mailbox_config(mailbox_config)
        token = _token or self._get_access_token(mailbox_config)
        response = requests.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            params=params,
            json=payload,
            timeout=cfg["timeout_seconds"],
        )
        if response.status_code >= 400:
            raise ValueError(f"Graph request failed method={method} status={response.status_code}: {response.text[:300]}")
        if not response.text:
            return {}
        return response.json()

    def _normalize_message(self, mailbox_config, raw_message: dict, attachments: list | None = None) -> Dict[str, Any]:
        cfg = self._mailbox_config(mailbox_config)
        from_obj = raw_message.get("from") if isinstance(raw_message.get("from"), dict) else {}
        from_email_obj = from_obj.get("emailAddress") if isinstance(from_obj.get("emailAddress"), dict) else {}
        internet_headers = raw_message.get("internetMessageHeaders") if isinstance(raw_message.get("internetMessageHeaders"), list) else []
        headers_dict = {}
        for header in internet_headers:
            if not isinstance(header, dict):
                continue
            name = (header.get("name") or "").strip()
            value = header.get("value") or ""
            if name:
                headers_dict[name] = value

        body_data = self._message_body(raw_message)
        normalized = {
            "provider_message_id": (raw_message.get("id") or "").strip(),
            "internet_message_id": (raw_message.get("internetMessageId") or "").strip(),
            "provider_thread_id": (raw_message.get("conversationId") or "").strip(),
            "internet_conversation_id": (headers_dict.get("Thread-Index") or "").strip(),
            "subject": raw_message.get("subject") or "",
            "from_email": (from_email_obj.get("address") or "").strip(),
            "from_name": (from_email_obj.get("name") or "").strip(),
            "to": self._participants(raw_message.get("toRecipients")),
            "cc": self._participants(raw_message.get("ccRecipients")),
            "bcc": self._participants(raw_message.get("bccRecipients")),
            "reply_to": self._participants(raw_message.get("replyTo")),
            "sent_at": self._iso_to_datetime(raw_message.get("sentDateTime")),
            "received_at": self._iso_to_datetime(raw_message.get("receivedDateTime")),
            "body_text": body_data["body_text"],
            "body_html": body_data["body_html"],
            "headers": headers_dict,
            "attachments": attachments or [],
            "has_attachments": bool(attachments or raw_message.get("hasAttachments")),
            "graph_web_link": raw_message.get("webLink") or "",
            "trace_id": raw_message.get("id") or "",
            "mailbox_address": mailbox_config.mailbox_address,
            "mailbox_user_id": cfg["user_id"],
        }
        return normalized

    def subscribe_mailbox(self, mailbox_config) -> Dict[str, object]:
        cfg = self._mailbox_config(mailbox_config)
        user_id = cfg["user_id"]
        if not user_id:
            raise ValueError("Graph mailbox user_id could not be resolved")
        url = f"{cfg['graph_base_url']}/users/{user_id}/mailFolders/Inbox"
        inbox_data = self._request_graph(mailbox_config, "GET", url)
        return {
            "subscribed": True,
            "provider": "MICROSOFT_365",
            "mailbox_id": mailbox_config.pk,
            "inbox_id": inbox_data.get("id") or "",
            "display_name": inbox_data.get("displayName") or "Inbox",
        }

    def get_inbox_counts(self, mailbox_config) -> Dict[str, int]:
        cfg = self._mailbox_config(mailbox_config)
        user_id = cfg["user_id"]
        if not user_id:
            raise ValueError("Graph mailbox user_id could not be resolved")

        token = self._get_access_token(mailbox_config)
        folder_url = f"{cfg['graph_base_url']}/users/{user_id}/mailFolders/Inbox"
        folder_data = self._request_graph(
            mailbox_config,
            "GET",
            folder_url,
            params={"$select": "totalItemCount,unreadItemCount"},
            _token=token,
        )

        total_count = int(folder_data.get("totalItemCount") or 0)

        attachment_count_url = f"{cfg['graph_base_url']}/users/{user_id}/mailFolders/Inbox/messages/$count"
        attachment_count_text = self._request_graph_text(
            mailbox_config,
            "GET",
            attachment_count_url,
            params={"$filter": "hasAttachments eq true"},
            extra_headers={"ConsistencyLevel": "eventual"},
            _token=token,
        )
        attachment_count = int((attachment_count_text or "0").strip() or 0)

        return {
            "total": total_count,
            "with_attachments": attachment_count,
            "unread": int(folder_data.get("unreadItemCount") or 0),
        }

    def poll_all_messages_metadata(
        self,
        mailbox_config,
        since_cursor: Optional[str] = None,
        max_unique_sender_emails: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch ALL messages from the mailbox using pagination, with minimal fields only.

        Returns lightweight dicts with just: provider_message_id, from_email, from_name,
        subject, has_attachments.  No body, no attachment download.  Used for dropdown
        population so the full inbox is always represented regardless of poll_page_size.

        PERF: token is fetched ONCE and reused for every page so we don't hit Azure AD
        for a new access_token on each paginated Graph call.
        """
        import time
        t_total = time.time()

        cfg = self._mailbox_config(mailbox_config)
        user_id = cfg["user_id"]
        if not user_id:
            raise ValueError("Graph mailbox user_id could not be resolved")

        print(f"[Graph] poll_all_messages_metadata START mailbox={mailbox_config.mailbox_address}")

        # Fetch token ONCE -- reused for every paginated request below
        t0 = time.time()
        token = self._get_access_token(mailbox_config)
        print(f"[Graph] token acquired in {time.time() - t0:.2f}s")

        # Minimal field set - much faster than full message fetch
        select_fields = ["id", "subject", "from", "hasAttachments", "receivedDateTime"]
        params: Dict[str, Any] = {
            "$top": 100,  # up to 100 items per page (safe max)
            "$orderby": "receivedDateTime desc",
            "$select": ",".join(select_fields),
        }
        if since_cursor:
            params["$filter"] = f"receivedDateTime ge {since_cursor}"

        base_url = f"{cfg['graph_base_url']}/users/{user_id}/mailFolders/Inbox/messages"
        all_items: List[Dict[str, Any]] = []
        seen_sender_emails = set()
        url: Optional[str] = base_url
        first_page = True
        page_num = 0

        while url:
            page_num += 1
            t_page = time.time()
            if first_page:
                # Pass pre-fetched token so _request_graph skips its own token fetch
                result = self._request_graph(mailbox_config, "GET", url, params=params, _token=token)
                first_page = False
            else:
                # nextLink already has all query params encoded
                result = self._request_graph(mailbox_config, "GET", url, params=None, _token=token)

            page_items = result.get("value") if isinstance(result.get("value"), list) else []
            all_items.extend(page_items)
            print(f"[Graph] page {page_num}: {len(page_items)} items in {time.time() - t_page:.2f}s (total so far: {len(all_items)})")

            if max_unique_sender_emails:
                for item in page_items:
                    if not isinstance(item, dict):
                        continue
                    from_obj = item.get("from") if isinstance(item.get("from"), dict) else {}
                    from_email_obj = from_obj.get("emailAddress") if isinstance(from_obj.get("emailAddress"), dict) else {}
                    sender_email = (from_email_obj.get("address") or "").strip()
                    if sender_email:
                        seen_sender_emails.add(sender_email)
                print(
                    f"[Graph] unique sender emails so far: {len(seen_sender_emails)}"
                    f" / {max_unique_sender_emails}"
                )
                if len(seen_sender_emails) >= max_unique_sender_emails:
                    print(
                        f"[Graph] early stop after {page_num} page(s) - "
                        f"reached demo limit of {max_unique_sender_emails} unique sender emails"
                    )
                    url = None
                    continue

            # Follow @odata.nextLink to get next page
            next_link = result.get("@odata.nextLink") or ""
            url = next_link.strip() if next_link.strip() else None

        print(f"[Graph] pagination done: {page_num} page(s), {len(all_items)} raw items")

        # Normalise into lightweight dicts
        t_norm = time.time()
        lightweight: List[Dict[str, Any]] = []
        for item in all_items:
            if not isinstance(item, dict):
                continue
            from_obj = item.get("from") if isinstance(item.get("from"), dict) else {}
            from_email_obj = from_obj.get("emailAddress") if isinstance(from_obj.get("emailAddress"), dict) else {}
            lightweight.append({
                "provider_message_id": (item.get("id") or "").strip(),
                "subject": (item.get("subject") or "").strip(),
                "from_email": (from_email_obj.get("address") or "").strip(),
                "from_name": (from_email_obj.get("name") or "").strip(),
                "has_attachments": bool(item.get("hasAttachments")),
            })
        print(f"[Graph] normalised {len(lightweight)} messages in {time.time() - t_norm:.3f}s")
        print(f"[Graph] poll_all_messages_metadata DONE total={time.time() - t_total:.2f}s")
        return lightweight

    def poll_messages(
        self,
        mailbox_config,
        since_cursor: Optional[str] = None,
        include_attachments: bool = True,
    ) -> List[Dict[str, object]]:
        cfg = self._mailbox_config(mailbox_config)
        user_id = cfg["user_id"]
        if not user_id:
            raise ValueError("Graph mailbox user_id could not be resolved")

        select_fields = [
            "id",
            "internetMessageId",
            "conversationId",
            "subject",
            "from",
            "toRecipients",
            "ccRecipients",
            "bccRecipients",
            "replyTo",
            "sentDateTime",
            "receivedDateTime",
            "body",
            "hasAttachments",
            "internetMessageHeaders",
            "webLink",
        ]
        params = {
            "$top": max(1, cfg["poll_page_size"]),
            "$orderby": "receivedDateTime desc",
            "$select": ",".join(select_fields),
        }
        if since_cursor:
            params["$filter"] = f"receivedDateTime ge {since_cursor}"

        url = f"{cfg['graph_base_url']}/users/{user_id}/mailFolders/Inbox/messages"
        result = self._request_graph(mailbox_config, "GET", url, params=params)
        messages = result.get("value") if isinstance(result.get("value"), list) else []

        normalized_messages: List[Dict[str, object]] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            provider_message_id = (item.get("id") or "").strip()
            attachments: List[Dict[str, Any]] = []
            if include_attachments and provider_message_id and item.get("hasAttachments"):
                attachments = self.get_attachments(mailbox_config, provider_message_id)
            normalized_messages.append(self._normalize_message(mailbox_config, item, attachments=attachments))
        return normalized_messages

    def poll_message_previews(self, mailbox_config, limit: int = 10) -> List[Dict[str, Any]]:
        cfg = self._mailbox_config(mailbox_config)
        user_id = cfg["user_id"]
        if not user_id:
            raise ValueError("Graph mailbox user_id could not be resolved")

        select_fields = [
            "id",
            "subject",
            "from",
            "receivedDateTime",
            "hasAttachments",
            "bodyPreview",
            "webLink",
        ]
        url = f"{cfg['graph_base_url']}/users/{user_id}/mailFolders/Inbox/messages"
        result = self._request_graph(
            mailbox_config,
            "GET",
            url,
            params={
                "$top": max(1, limit),
                "$orderby": "receivedDateTime desc",
                "$select": ",".join(select_fields),
            },
        )
        items = result.get("value") if isinstance(result.get("value"), list) else []
        previews: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            from_obj = item.get("from") if isinstance(item.get("from"), dict) else {}
            from_email_obj = from_obj.get("emailAddress") if isinstance(from_obj.get("emailAddress"), dict) else {}
            previews.append(
                {
                    "provider_message_id": (item.get("id") or "").strip(),
                    "subject": (item.get("subject") or "").strip(),
                    "from_email": (from_email_obj.get("address") or "").strip(),
                    "from_name": (from_email_obj.get("name") or "").strip(),
                    "received_at": item.get("receivedDateTime") or "",
                    "body_preview": (item.get("bodyPreview") or "").strip(),
                    "has_attachments": bool(item.get("hasAttachments")),
                    "graph_web_link": item.get("webLink") or "",
                }
            )
        return previews

    def get_message(self, mailbox_config, provider_message_id: str) -> Dict[str, object]:
        cfg = self._mailbox_config(mailbox_config)
        user_id = cfg["user_id"]
        if not user_id:
            raise ValueError("Graph mailbox user_id could not be resolved")
        if not provider_message_id:
            raise ValueError("provider_message_id is required")

        select_fields = [
            "id",
            "internetMessageId",
            "conversationId",
            "subject",
            "from",
            "toRecipients",
            "ccRecipients",
            "bccRecipients",
            "replyTo",
            "sentDateTime",
            "receivedDateTime",
            "body",
            "hasAttachments",
            "internetMessageHeaders",
            "webLink",
        ]
        url = f"{cfg['graph_base_url']}/users/{user_id}/messages/{provider_message_id}"
        raw_message = self._request_graph(
            mailbox_config,
            "GET",
            url,
            params={"$select": ",".join(select_fields)},
        )
        attachments: List[Dict[str, Any]] = []
        if raw_message.get("hasAttachments"):
            attachments = self.get_attachments(mailbox_config, provider_message_id)
        return self._normalize_message(mailbox_config, raw_message, attachments=attachments)

    def get_message_preview(self, mailbox_config, provider_message_id: str) -> Dict[str, object]:
        cfg = self._mailbox_config(mailbox_config)
        user_id = cfg["user_id"]
        if not user_id:
            raise ValueError("Graph mailbox user_id could not be resolved")
        if not provider_message_id:
            raise ValueError("provider_message_id is required")

        token = self._get_access_token(mailbox_config)
        select_fields = [
            "id",
            "subject",
            "from",
            "receivedDateTime",
            "body",
            "bodyPreview",
            "hasAttachments",
            "webLink",
        ]
        url = f"{cfg['graph_base_url']}/users/{user_id}/messages/{provider_message_id}"
        raw_message = self._request_graph(
            mailbox_config,
            "GET",
            url,
            params={"$select": ",".join(select_fields)},
            _token=token,
        )
        body_data = self._message_body(raw_message)
        attachments = self.get_attachment_summaries(mailbox_config, provider_message_id, _token=token)[:10]
        from_obj = raw_message.get("from") if isinstance(raw_message.get("from"), dict) else {}
        from_email_obj = from_obj.get("emailAddress") if isinstance(from_obj.get("emailAddress"), dict) else {}
        return {
            "provider_message_id": (raw_message.get("id") or "").strip(),
            "subject": raw_message.get("subject") or "",
            "from_email": (from_email_obj.get("address") or "").strip(),
            "from_name": (from_email_obj.get("name") or "").strip(),
            "received_at": raw_message.get("receivedDateTime") or "",
            "body_text": body_data["body_text"],
            "body_preview": (raw_message.get("bodyPreview") or "").strip(),
            "has_attachments": bool(raw_message.get("hasAttachments")),
            "attachments": attachments,
            "graph_web_link": raw_message.get("webLink") or "",
        }

    def get_attachment_summaries(
        self,
        mailbox_config,
        provider_message_id: str,
        _token: str | None = None,
    ) -> List[Dict[str, object]]:
        cfg = self._mailbox_config(mailbox_config)
        user_id = cfg["user_id"]
        if not user_id:
            raise ValueError("Graph mailbox user_id could not be resolved")
        if not provider_message_id:
            raise ValueError("provider_message_id is required")

        url = f"{cfg['graph_base_url']}/users/{user_id}/messages/{provider_message_id}/attachments"
        raw = self._request_graph(
            mailbox_config,
            "GET",
            url,
            params={"$select": "id,name,lastModifiedDateTime,size,contentType"},
            _token=_token,
        )
        items = raw.get("value") if isinstance(raw.get("value"), list) else []

        attachments: List[Dict[str, object]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if (item.get("@odata.type") or "").endswith("itemAttachment"):
                continue
            attachments.append(
                {
                    "provider_attachment_id": (item.get("id") or "").strip(),
                    "filename": item.get("name") or "attachment.bin",
                    "timestamp": (item.get("lastModifiedDateTime") or "").strip(),
                    "content_type": item.get("contentType") or "application/octet-stream",
                    "size_bytes": item.get("size") or 0,
                }
            )
        return attachments

    def get_attachments(self, mailbox_config, provider_message_id: str) -> List[Dict[str, object]]:
        cfg = self._mailbox_config(mailbox_config)
        user_id = cfg["user_id"]
        if not user_id:
            raise ValueError("Graph mailbox user_id could not be resolved")
        if not provider_message_id:
            raise ValueError("provider_message_id is required")

        url = f"{cfg['graph_base_url']}/users/{user_id}/messages/{provider_message_id}/attachments"
        raw = self._request_graph(mailbox_config, "GET", url)
        items = raw.get("value") if isinstance(raw.get("value"), list) else []

        attachments: List[Dict[str, object]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if (item.get("@odata.type") or "").endswith("itemAttachment"):
                continue
            content_b64 = item.get("contentBytes") or ""
            content_bytes = b""
            if content_b64:
                try:
                    content_bytes = base64.b64decode(content_b64)
                except Exception:
                    content_bytes = b""
            attachments.append(
                {
                    "provider_attachment_id": (item.get("id") or "").strip(),
                    "filename": item.get("name") or "attachment.bin",
                    "timestamp": (item.get("lastModifiedDateTime") or "").strip(),
                    "content_type": item.get("contentType") or "application/octet-stream",
                    "size_bytes": item.get("size") or len(content_bytes),
                    "content_bytes": content_bytes,
                }
            )
        return attachments

    def send_message(self, mailbox_config, payload: Dict[str, object]) -> Dict[str, object]:
        cfg = self._mailbox_config(mailbox_config)
        user_id = cfg["user_id"]
        if not user_id:
            raise ValueError("Graph mailbox user_id could not be resolved")

        recipients_raw = payload.get("to") or []
        recipients = []
        for item in recipients_raw:
            email = ""
            if isinstance(item, dict):
                email = (item.get("email") or item.get("address") or "").strip()
            elif isinstance(item, str):
                email = item.strip()
            if email:
                recipients.append({"emailAddress": {"address": email}})

        if not recipients:
            raise ValueError("At least one recipient is required")

        graph_payload = {
            "message": {
                "subject": payload.get("subject") or "",
                "body": {
                    "contentType": "HTML" if (payload.get("body_html") or "").strip() else "Text",
                    "content": (payload.get("body_html") or payload.get("body_text") or ""),
                },
                "toRecipients": recipients,
            },
            "saveToSentItems": True,
        }

        url = f"{cfg['graph_base_url']}/users/{user_id}/sendMail"
        self._request_graph(mailbox_config, "POST", url, payload=graph_payload)
        return {
            "sent": True,
            "provider": "MICROSOFT_365",
            "provider_message_id": payload.get("provider_message_id", ""),
        }
