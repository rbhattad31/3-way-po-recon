"""Template views for email integration UI pages."""
import json
import uuid
from urllib.parse import urlencode

from django.contrib import messages
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.html import format_html
from django.views import View
from django.http import JsonResponse
from django.urls import reverse

from apps.core.permissions import PermissionRequiredMixin
from apps.email_integration.enums import (
    EmailActionStatus,
    EmailActionType,
    EmailDirection,
    EmailMessageClassification,
    EmailProcessingStatus,
    EmailProvider,
    EmailRoutingDecisionStatus,
    EmailRoutingDecisionType,
    EmailRoutingStatus,
    TargetDomain,
)
from apps.email_integration.models import (
    EmailAction,
    EmailAttachment,
    EmailMessage,
    EmailRoutingDecision,
    EmailTemplate,
    EmailThread,
    MailboxConfig,
)
from apps.email_integration.services.inbound_ingestion_service import InboundIngestionService
from apps.email_integration.services.mailbox_service import MailboxService
from apps.email_integration.services.outbound_email_service import OutboundEmailService
from apps.email_integration.services.processing_service import EmailProcessingService
from apps.email_integration.services.routing_service import RoutingService
from apps.email_integration.services.triage_service import TriageService
from apps.email_integration.services.provider_adapters.microsoft_graph_adapter import MicrosoftGraphEmailAdapter
from apps.email_integration.tasks import poll_mailboxes_task, relink_email_threads_task, retry_failed_email_actions_task


class EmailIntegrationDashboardView(PermissionRequiredMixin, View):
    """Simple UI dashboard for mailbox and message operations."""

    required_permission = "email.view"
    template_name = "email_integration/dashboard.html"
    redirect_view_name = "email_integration:dashboard"
    connect_success_view_name = "email_integration:dashboard"
    connect_view_name = "email_integration:connect_mailbox"

    def get_redirect_view_name(self) -> str:
        return self.redirect_view_name

    def get_connect_success_view_name(self) -> str:
        return self.connect_success_view_name

    def get_connect_view_name(self) -> str:
        return self.connect_view_name

    def _redirect_current(self):
        return redirect(self.get_redirect_view_name())

    def _redirect_connect(self):
        return redirect(self.get_connect_view_name())

    def _require_verified_mailbox(self, request):
        tenant = getattr(request, "tenant", None)
        is_platform_admin = getattr(request.user, "is_platform_admin", False)
        if self._verified_mailboxes(tenant=tenant, is_platform_admin=is_platform_admin).exists():
            return None
        messages.info(request, "Connect and verify a mailbox for this tenant before accessing email functionalities.")
        return self._redirect_connect()

    @staticmethod
    def _can_manage(request) -> bool:
        return bool(getattr(request.user, "has_permission", lambda *_: False)("email.manage"))

    @staticmethod
    def _can_send(request) -> bool:
        return bool(getattr(request.user, "has_permission", lambda *_: False)("email.send"))

    @staticmethod
    def _default_mailbox_name(mailbox_address: str) -> str:
        local_part = (mailbox_address or "").split("@", 1)[0].strip()
        if not local_part:
            return "Shared Inbox"
        normalized = local_part.replace(".", " ").replace("_", " ").replace("-", " ").strip()
        return f"{normalized.title() or 'Shared'} Inbox"

    @staticmethod
    def _guess_provider(mailbox_address: str) -> str:
        domain = (mailbox_address or "").split("@", 1)[-1].strip().lower()
        if domain in {"gmail.com", "googlemail.com"}:
            return EmailProvider.GMAIL
        return EmailProvider.MICROSOFT_365

    @staticmethod
    def _scoped_mailboxes(*, tenant, is_platform_admin):
        qs = MailboxConfig.objects.filter(is_active=True)
        if tenant is not None and not is_platform_admin:
            qs = qs.filter(tenant=tenant)
        return qs

    @staticmethod
    def _scoped_messages(*, tenant, is_platform_admin):
        qs = EmailMessage.objects.all()
        if tenant is not None and not is_platform_admin:
            qs = qs.filter(tenant=tenant)
        return qs

    @staticmethod
    def _scoped_actions(*, tenant, is_platform_admin):
        qs = EmailAction.objects.all()
        if tenant is not None and not is_platform_admin:
            qs = qs.filter(tenant=tenant)
        return qs

    @staticmethod
    def _scoped_attachments(*, tenant, is_platform_admin):
        qs = EmailAttachment.objects.all()
        if tenant is not None and not is_platform_admin:
            qs = qs.filter(tenant=tenant)
        return qs

    @staticmethod
    def _verified_mailboxes(*, tenant, is_platform_admin):
        qs = EmailIntegrationDashboardView._scoped_mailboxes(tenant=tenant, is_platform_admin=is_platform_admin)
        return qs.exclude(last_success_at__isnull=True).filter(last_error_message="")

    @staticmethod
    def _build_mailbox_config(request, mailbox_address: str, existing_config=None) -> dict:
        merged_config = dict(existing_config or {})
        provider = (request.POST.get("provider") or "").strip()
        tenant_id = (request.POST.get("graph_tenant_id") or "").strip() or str(merged_config.get("tenant_id") or "").strip()
        client_id = (request.POST.get("graph_client_id") or "").strip() or str(merged_config.get("client_id") or "").strip()
        client_secret = (request.POST.get("graph_client_secret") or "").strip() or str(merged_config.get("client_secret") or "").strip()
        smtp_host = (request.POST.get("smtp_host") or "").strip() or str(merged_config.get("smtp_host") or "").strip()
        smtp_port = (request.POST.get("smtp_port") or "").strip() or str(merged_config.get("smtp_port") or "").strip()
        smtp_security = (request.POST.get("smtp_security") or "").strip().upper() or str(merged_config.get("smtp_security") or "").strip().upper()
        smtp_username = (request.POST.get("smtp_username") or "").strip() or str(merged_config.get("smtp_username") or "").strip()
        smtp_password = (request.POST.get("smtp_password") or "").strip() or str(merged_config.get("smtp_password") or "").strip()
        user_id = str(merged_config.get("user_id") or "").strip() or mailbox_address
        scope = str(merged_config.get("scope") or "https://graph.microsoft.com/.default").strip() or "https://graph.microsoft.com/.default"
        graph_base_url = str(merged_config.get("graph_base_url") or "https://graph.microsoft.com/v1.0").strip() or "https://graph.microsoft.com/v1.0"

        timeout_seconds = str(merged_config.get("timeout_seconds") or "30").strip()
        poll_page_size = str(merged_config.get("poll_page_size") or "25").strip()

        merged_config.update(
            {
                "provider": provider,
                "tenant_id": tenant_id,
                "client_id": client_id,
                "client_secret": client_secret,
                "smtp_host": smtp_host,
                "smtp_port": int(smtp_port) if smtp_port else None,
                "smtp_security": smtp_security,
                "smtp_username": smtp_username,
                "smtp_password": smtp_password,
                "user_id": user_id,
                "scope": scope,
                "graph_base_url": graph_base_url,
                "timeout_seconds": int(timeout_seconds or "30"),
                "poll_page_size": int(poll_page_size or "25"),
            }
        )
        return merged_config

    @staticmethod
    def _record_action(*, tenant, actor_user, action_type, action_status, trace_id="", payload=None, result=None, error="", email_message=None, thread=None):
        EmailAction.objects.create(
            tenant=tenant,
            email_message=email_message,
            thread=thread,
            action_type=action_type,
            action_status=action_status,
            performed_by_user=actor_user,
            actor_primary_role=(getattr(actor_user, "role", "") or "") if actor_user else "",
            payload_json=payload or {},
            result_json=result or {},
            error_message=error or "",
            trace_id=trace_id or "",
        )

    def _retry_message(self, *, request, message_obj):
        triage_result = TriageService.triage_message(message_obj, message_obj.mailbox)
        message_obj.message_classification = triage_result["classification"]
        message_obj.intent_type = triage_result["intent"]
        message_obj.sender_trust_level = triage_result["trust_level"]
        message_obj.matched_entity_type = triage_result.get("entity_type") or ""
        message_obj.matched_entity_id = triage_result.get("entity_id")
        message_obj.processing_status = EmailProcessingStatus.CLASSIFIED
        message_obj.routing_status = EmailRoutingStatus.PENDING
        message_obj.save(
            update_fields=[
                "message_classification",
                "intent_type",
                "sender_trust_level",
                "matched_entity_type",
                "matched_entity_id",
                "processing_status",
                "routing_status",
                "updated_at",
            ]
        )
        decision = RoutingService.apply_routing(message_obj, triage_result)
        self._record_action(
            tenant=message_obj.tenant,
            actor_user=request.user,
            action_type=EmailActionType.TRIGGER_RECONCILIATION,
            action_status=EmailActionStatus.COMPLETED,
            trace_id=message_obj.trace_id,
            email_message=message_obj,
            thread=message_obj.thread,
            payload={"operation": "retry_message", "message_id": message_obj.pk},
            result={"routing_decision_id": decision.pk, "target_domain": decision.target_domain},
        )

    def _override_route(self, *, request, message_obj, target_domain):
        if target_domain not in [
            TargetDomain.AP,
            TargetDomain.PROCUREMENT,
            TargetDomain.TRIAGE,
            TargetDomain.NOTIFICATION_ONLY,
        ]:
            raise ValueError("Invalid target domain")

        handler_cls = RoutingService.HANDLER_BY_DOMAIN.get(target_domain)
        if handler_cls is None:
            raise ValueError("No handler configured for target domain")

        decision = EmailRoutingDecision.objects.create(
            tenant=message_obj.tenant,
            email_message=message_obj,
            decision_type=EmailRoutingDecisionType.MANUAL,
            target_domain=target_domain,
            target_handler=handler_cls.handler_name,
            target_entity_type=message_obj.matched_entity_type or "",
            target_entity_id=message_obj.matched_entity_id,
            confidence_score=1.0,
            deterministic_flag=True,
            rule_name="manual_override",
            rule_version="v1",
            llm_used=False,
            reasoning_summary="Manual route override from email workbench.",
            evidence_json={"source": "dashboard"},
            final_status=EmailRoutingDecisionStatus.PROPOSED,
        )

        handler_cls().process(message_obj, decision)
        decision.final_status = EmailRoutingDecisionStatus.APPLIED
        decision.save(update_fields=["final_status", "updated_at"])

        message_obj.routing_status = EmailRoutingStatus.ROUTED
        message_obj.processing_status = EmailProcessingStatus.ROUTED
        message_obj.save(update_fields=["routing_status", "processing_status", "updated_at"])

        self._record_action(
            tenant=message_obj.tenant,
            actor_user=request.user,
            action_type=EmailActionType.REOPEN_ENTITY,
            action_status=EmailActionStatus.COMPLETED,
            trace_id=message_obj.trace_id,
            email_message=message_obj,
            thread=message_obj.thread,
            payload={"operation": "override_route", "target_domain": target_domain},
            result={"routing_decision_id": decision.pk, "target_handler": decision.target_handler},
        )

    def _ignore_message(self, *, request, message_obj):
        message_obj.processing_status = EmailProcessingStatus.IGNORED
        message_obj.routing_status = EmailRoutingStatus.TRIAGED
        message_obj.save(update_fields=["processing_status", "routing_status", "updated_at"])
        self._record_action(
            tenant=message_obj.tenant,
            actor_user=request.user,
            action_type=EmailActionType.IGNORE_EMAIL,
            action_status=EmailActionStatus.COMPLETED,
            trace_id=message_obj.trace_id,
            email_message=message_obj,
            thread=message_obj.thread,
            payload={"operation": "ignore_message", "message_id": message_obj.pk},
            result={"processing_status": message_obj.processing_status},
        )

    def _run_selected_messages(self, *, request, tenant, is_platform_admin, selected_ids):
        message_qs = (
            self._scoped_messages(tenant=tenant, is_platform_admin=is_platform_admin)
            .filter(pk__in=selected_ids, direction=EmailDirection.INBOUND)
            .select_related("mailbox", "thread")
        )
        processed = 0
        failed = 0

        for message_obj in message_qs:
            try:
                self._retry_message(request=request, message_obj=message_obj)
                processed += 1
            except Exception as exc:
                failed += 1
                self._record_action(
                    tenant=message_obj.tenant,
                    actor_user=request.user,
                    action_type=EmailActionType.TRIGGER_RECONCILIATION,
                    action_status=EmailActionStatus.FAILED,
                    trace_id=message_obj.trace_id,
                    email_message=message_obj,
                    thread=message_obj.thread,
                    payload={"operation": "run_selected_messages", "message_id": message_obj.pk},
                    error=str(exc),
                )
        return processed, failed

    @staticmethod
    def _parse_multivalue_text(value: str):
        raw = (value or "").replace("\n", ",")
        parts = [item.strip() for item in raw.split(",") if item.strip()]
        deduped = []
        for item in parts:
            if item not in deduped:
                deduped.append(item)
        return deduped

    def _send_outbound(self, *, request, mailbox, tenant):
        template_code = (request.POST.get("template_code") or "").strip()
        to_recipients_raw = (request.POST.get("to_recipients") or "").strip()
        subject = (request.POST.get("subject") or "").strip()
        body_text = (request.POST.get("body_text") or "").strip()
        variables_raw = (request.POST.get("template_variables") or "").strip()

        to_recipients = [s.strip() for s in to_recipients_raw.split(",") if s.strip()]
        if not to_recipients:
            raise ValueError("At least one recipient is required")

        trace_id = uuid.uuid4().hex
        if template_code:
            variables = {}
            if variables_raw:
                variables = json.loads(variables_raw)
                if not isinstance(variables, dict):
                    raise ValueError("Template variables must be a JSON object")
            send_result = OutboundEmailService.send_templated_email(
                tenant=tenant,
                mailbox=mailbox,
                template_code=template_code,
                variables=variables,
                to_recipients=to_recipients,
                actor_user=request.user,
                trace_id=trace_id,
            )
            rendered_subject = send_result.get("rendered_subject", "")
            rendered_body = send_result.get("rendered_body_text", "")
        else:
            if not subject:
                raise ValueError("Subject is required for custom outbound email")
            payload = {
                "provider_message_id": f"out-{uuid.uuid4().hex}",
                "subject": subject,
                "body_text": body_text,
                "body_html": "",
                "to": [{"email": email} for email in to_recipients],
            }
            send_result = MailboxService.get_adapter(mailbox).send_message(mailbox, payload)
            self._record_action(
                tenant=tenant,
                actor_user=request.user,
                action_type=EmailActionType.SEND_OUTBOUND_EMAIL,
                action_status=EmailActionStatus.COMPLETED,
                trace_id=trace_id,
                payload=payload,
                result=send_result,
            )
            rendered_subject = subject
            rendered_body = body_text

        EmailMessage.objects.create(
            tenant=tenant,
            mailbox=mailbox,
            direction="OUTBOUND",
            provider_message_id=(send_result.get("provider_message_id") or f"out-{uuid.uuid4().hex}"),
            internet_message_id="",
            subject=rendered_subject,
            from_email=mailbox.mailbox_address,
            from_name=mailbox.name,
            to_json=[{"email": email} for email in to_recipients],
            sent_at=timezone.now(),
            body_text=rendered_body,
            body_html="",
            body_preview=(rendered_body or "")[:1000],
            has_attachments=False,
            message_classification=EmailMessageClassification.GENERAL_QUERY,
            processing_status=EmailProcessingStatus.PROCESSED,
            routing_status=EmailRoutingStatus.ROUTED,
            provider_payload_json=send_result,
            raw_headers_json={},
            trace_id=trace_id,
        )

    def _build_operational_context(self, request) -> dict:
        tenant = getattr(request, "tenant", None)
        is_platform_admin = getattr(request.user, "is_platform_admin", False)

        query = (request.GET.get("q") or "").strip()
        mailbox_filter = (request.GET.get("mailbox") or "").strip()
        processing_filter = (request.GET.get("processing_status") or "").strip()
        classification_filter = (request.GET.get("classification") or "").strip()
        routing_filter = (request.GET.get("routing_status") or "").strip()
        action_status_filter = (request.GET.get("action_status") or "").strip()

        mailboxes = MailboxConfig.objects.filter(is_active=True)
        threads = EmailThread.objects.all()
        message_qs = EmailMessage.objects.all()
        actions = EmailAction.objects.all()

        if tenant is not None and not is_platform_admin:
            mailboxes = mailboxes.filter(tenant=tenant)
            threads = threads.filter(tenant=tenant)
            message_qs = message_qs.filter(tenant=tenant)
            actions = actions.filter(tenant=tenant)

        if mailbox_filter:
            message_qs = message_qs.filter(mailbox_id=mailbox_filter)
            actions = actions.filter(email_message__mailbox_id=mailbox_filter)
        if processing_filter:
            message_qs = message_qs.filter(processing_status=processing_filter)
        if classification_filter:
            message_qs = message_qs.filter(message_classification=classification_filter)
        if routing_filter:
            message_qs = message_qs.filter(routing_status=routing_filter)
        if action_status_filter:
            actions = actions.filter(action_status=action_status_filter)
        if query:
            message_qs = message_qs.filter(
                Q(subject__icontains=query)
                | Q(from_email__icontains=query)
                | Q(provider_message_id__icontains=query)
                | Q(trace_id__icontains=query)
            )
            actions = actions.filter(
                Q(action_type__icontains=query)
                | Q(error_message__icontains=query)
                | Q(trace_id__icontains=query)
            )

        now = timezone.now()
        verified_mailboxes = self._verified_mailboxes(tenant=tenant, is_platform_admin=is_platform_admin)
        selected_mailbox = None
        if mailbox_filter:
            selected_mailbox = mailboxes.filter(pk=mailbox_filter).first()
        if selected_mailbox is None:
            selected_mailbox = verified_mailboxes.order_by("name").first()
            if selected_mailbox is not None and not mailbox_filter:
                mailbox_filter = str(selected_mailbox.pk)

        selected_mailbox_config = selected_mailbox.config_json if selected_mailbox and isinstance(selected_mailbox.config_json, dict) else {}
        auto_sender_emails = selected_mailbox_config.get("auto_process_sender_emails") or []
        auto_sender_names = selected_mailbox_config.get("auto_process_sender_names") or []
        mailbox_health = []
        for mailbox in mailboxes.order_by("name"):
            last_success_at = mailbox.last_success_at
            sync_age_minutes = None
            health_state = "healthy"
            if last_success_at is not None:
                sync_age_minutes = int((now - last_success_at).total_seconds() // 60)
            if mailbox.last_error_message:
                health_state = "error"
            elif sync_age_minutes is None or sync_age_minutes > 120:
                health_state = "stale"
            mailbox_health.append(
                {
                    "mailbox": mailbox,
                    "health_state": health_state,
                    "sync_age_minutes": sync_age_minutes,
                }
            )

        inbox_requires_mailbox_selection = not bool(mailbox_filter)
        if inbox_requires_mailbox_selection:
            inbox_messages = EmailMessage.objects.none()
        else:
            inbox_messages = list(
                message_qs.filter(direction=EmailDirection.INBOUND, has_attachments=True)
                .select_related("mailbox", "thread", "linked_document_upload")
                .prefetch_related("attachments")
                .order_by("-received_at", "-created_at")[:25]
            )
            for msg in inbox_messages:
                msg.extraction_status = "NOT_STARTED"
                msg.extraction_result_url = ""
                msg.extraction_result_id = None

                linked_upload = getattr(msg, "linked_document_upload", None)
                if linked_upload is None and hasattr(msg, "attachments"):
                    first_linked = next(
                        (att.linked_document_upload for att in msg.attachments.all() if att.linked_document_upload_id),
                        None,
                    )
                    linked_upload = first_linked

                if linked_upload is None:
                    continue

                msg.extraction_status = (linked_upload.processing_state or "QUEUED").upper()
                extraction_result = linked_upload.extraction_results.order_by("-created_at").first()
                if extraction_result is None:
                    continue

                msg.extraction_result_id = extraction_result.pk
                msg.extraction_result_url = reverse(
                    "extraction:result_detail",
                    kwargs={"pk": extraction_result.pk},
                )
                msg.extraction_status = "COMPLETED" if extraction_result.success else "FAILED"

        attachment_candidates_qs = (
            self._scoped_attachments(tenant=tenant, is_platform_admin=is_platform_admin)
            .filter(email_message__direction=EmailDirection.INBOUND)
            .filter(linked_document_upload__isnull=False)
            .select_related("email_message", "email_message__mailbox", "linked_document_upload")
            .order_by("-created_at")[:200]
        )

        # Build hierarchical dropdown filters for ingest form
        attachment_candidates = []
        sender_emails_by_mailbox = {}  # {mailbox_id: [email1, email2, ...]}
        sender_names_by_mailbox_email = {}  # {(mailbox_id, email): [name1, name2, ...]}
        subjects_by_mailbox_email_name = {}  # {(mailbox_id, email, name): [subject1, subject2, ...]}
        attachment_map = {}  # {(mailbox_id, email, name, subject): [attachment_id, ...]}

        for candidate in attachment_candidates_qs:
            message_obj = candidate.email_message
            if not message_obj:
                continue

            mailbox_id = message_obj.mailbox_id
            from_email = (message_obj.from_email or "").strip()
            from_name = (message_obj.from_name or "").strip()
            subject = (message_obj.subject or "").strip()

            # Populate attachment candidate list
            attachment_candidates.append(
                {
                    "id": candidate.pk,
                    "filename": candidate.filename,
                    "mailbox_id": mailbox_id,
                    "mailbox_label": (
                        f"{message_obj.mailbox.name} ({message_obj.mailbox.mailbox_address})"
                        if message_obj.mailbox_id
                        else ""
                    ),
                    "from_email": from_email,
                    "from_name": from_name,
                    "subject": subject,
                    "received_at": message_obj.received_at,
                }
            )

            # Build sender_emails_by_mailbox
            if mailbox_id not in sender_emails_by_mailbox:
                sender_emails_by_mailbox[mailbox_id] = []
            if from_email and from_email not in sender_emails_by_mailbox[mailbox_id]:
                sender_emails_by_mailbox[mailbox_id].append(from_email)

            # Build sender_names_by_mailbox_email
            key_email = (mailbox_id, from_email)
            if key_email not in sender_names_by_mailbox_email:
                sender_names_by_mailbox_email[key_email] = []
            if from_name and from_name not in sender_names_by_mailbox_email[key_email]:
                sender_names_by_mailbox_email[key_email].append(from_name)

            # Build subjects_by_mailbox_email_name
            key_name = (mailbox_id, from_email, from_name)
            if key_name not in subjects_by_mailbox_email_name:
                subjects_by_mailbox_email_name[key_name] = []
            if subject and subject not in subjects_by_mailbox_email_name[key_name]:
                subjects_by_mailbox_email_name[key_name].append(subject)

            # Build attachment_map
            key_subject = (mailbox_id, from_email, from_name, subject)
            if key_subject not in attachment_map:
                attachment_map[key_subject] = []
            if candidate.pk not in attachment_map[key_subject]:
                attachment_map[key_subject].append(candidate.pk)

        # Sort all dropdown lists
        for mailbox_id in sender_emails_by_mailbox:
            sender_emails_by_mailbox[mailbox_id].sort()
        for key in sender_names_by_mailbox_email:
            sender_names_by_mailbox_email[key].sort()
        for key in subjects_by_mailbox_email_name:
            subjects_by_mailbox_email_name[key].sort()

        def _json_dump(value):
            return json.dumps(value, cls=DjangoJSONEncoder)

        return {
            "mailbox_count": mailboxes.count(),
            "thread_count": threads.count(),
            "message_count": message_qs.count(),
            "action_count": actions.count(),
            "inbox_messages": inbox_messages,
            "inbox_requires_mailbox_selection": inbox_requires_mailbox_selection,
            "recent_messages": (
                message_qs.select_related("mailbox", "thread", "linked_document_upload")
                .prefetch_related("attachments")
                .order_by("-received_at", "-created_at")[:20]
            ),
            "recent_actions": (
                actions.select_related("email_message", "performed_by_user")
                .order_by("-created_at")[:20]
            ),
            "triage_messages": (
                message_qs.filter(routing_status=EmailRoutingStatus.TRIAGED)
                .select_related("mailbox", "thread")
                .prefetch_related("attachments")
                .order_by("-received_at", "-created_at")[:15]
            ),
            "failed_actions": (
                actions.filter(action_status=EmailActionStatus.FAILED)
                .select_related("email_message", "performed_by_user")
                .order_by("-created_at")[:15]
            ),
            "mailboxes": mailboxes.order_by("name"),
            "verified_mailboxes": verified_mailboxes.order_by("name"),
            "attachment_candidates": _json_dump(attachment_candidates),
            "sender_emails_by_mailbox": _json_dump(sender_emails_by_mailbox),
            "sender_names_by_mailbox_email": _json_dump({_json_dump(k): v for k, v in sender_names_by_mailbox_email.items()}),
            "subjects_by_mailbox_email_name": _json_dump({_json_dump(k): v for k, v in subjects_by_mailbox_email_name.items()}),
            "attachment_map": _json_dump({_json_dump(k): v for k, v in attachment_map.items()}),
            "tenant_display_name": getattr(tenant, "name", "Platform") if tenant is not None else "Platform",
            "can_manage": self._can_manage(request),
            "can_send": self._can_send(request),
            "mailbox_health": mailbox_health,
            "selected_mailbox": selected_mailbox,
            "auto_sender_emails_text": "\n".join(auto_sender_emails),
            "auto_sender_names_text": "\n".join(auto_sender_names),
            "filters": {
                "q": query,
                "mailbox": mailbox_filter,
                "processing_status": processing_filter,
                "classification": classification_filter,
                "routing_status": routing_filter,
                "action_status": action_status_filter,
            },
            "processing_choices": [choice[0] for choice in EmailProcessingStatus.choices],
            "classification_choices": [choice[0] for choice in EmailMessageClassification.choices],
            "routing_choices": [choice[0] for choice in EmailRoutingStatus.choices],
            "action_status_choices": [choice[0] for choice in EmailActionStatus.choices],
            "target_domain_choices": [
                TargetDomain.AP,
                TargetDomain.PROCUREMENT,
                TargetDomain.TRIAGE,
                TargetDomain.NOTIFICATION_ONLY,
            ],
            "active_templates": EmailTemplate.objects.filter(is_active=True)
            .filter(Q(tenant=tenant) | Q(tenant__isnull=True))
            .order_by("template_code"),
        }

    def post(self, request, *args, **kwargs):
        action = (request.POST.get("action") or "").strip()
        can_manage = self._can_manage(request)
        can_send = self._can_send(request)

        if action == "send_outbound_email" and not can_send:
            messages.error(request, "You do not have permission to send outbound email.")
            return self._redirect_current()

        if action != "send_outbound_email" and not can_manage:
            messages.error(request, "You do not have permission to manage email integration.")
            return self._redirect_current()

        tenant = getattr(request, "tenant", None)
        is_platform_admin = getattr(request.user, "is_platform_admin", False)

        if action in ["create_mailbox", "connect_mailbox", "update_mailbox"]:
            mailbox_id = (request.POST.get("mailbox_id") or "").strip()
            name = (request.POST.get("name") or "").strip()
            mailbox_address = (request.POST.get("mailbox_address") or "").strip()
            provider = (request.POST.get("provider") or "").strip() or self._guess_provider(mailbox_address)

            if not mailbox_address:
                messages.error(request, "Mailbox email address is required.")
                return self._redirect_current()
            if not name:
                name = self._default_mailbox_name(mailbox_address)

            mailbox_qs = MailboxConfig.objects.all()
            if tenant is not None and not is_platform_admin:
                mailbox_qs = mailbox_qs.filter(tenant=tenant)

            mailbox = None
            created = False
            if action == "update_mailbox":
                if not mailbox_id:
                    messages.error(request, "Mailbox selection is required for update.")
                    return self._redirect_current()
                mailbox = mailbox_qs.filter(pk=mailbox_id, is_active=True).first()
                if mailbox is None:
                    messages.error(request, "Mailbox not found for your tenant.")
                    return self._redirect_current()

            mailbox_config = self._build_mailbox_config(
                request,
                mailbox_address,
                existing_config=getattr(mailbox, "config_json", {}) if mailbox else {},
            )

            if provider == EmailProvider.MICROSOFT_365:
                if not mailbox_config.get("tenant_id") or not mailbox_config.get("client_id") or not mailbox_config.get("client_secret"):
                    messages.error(request, "Mail ID, Tenant ID, Client ID, and Client Secret are required for Microsoft 365 mailbox connection.")
                    return self._redirect_current()

            if provider == EmailProvider.GMAIL:
                missing = []
                if not mailbox_config.get("smtp_host"):
                    missing.append("SMTP Server")
                if not mailbox_config.get("smtp_port"):
                    missing.append("SMTP Port")
                if not mailbox_config.get("smtp_username"):
                    missing.append("SMTP Username")
                if not mailbox_config.get("smtp_password"):
                    missing.append("SMTP Password")
                if missing:
                    messages.error(request, "Gmail configuration requires: " + ", ".join(missing))
                    return self._redirect_current()

            mailbox_defaults = {
                "name": name,
                "provider": provider,
                "is_active": True,
                "is_inbound_enabled": True,
                "is_outbound_enabled": False,
                "webhook_enabled": True,
                "polling_enabled": False,
                "poll_interval_minutes": 5,
                "config_json": mailbox_config,
            }

            if action == "update_mailbox" and mailbox is not None:
                for field, value in mailbox_defaults.items():
                    setattr(mailbox, field, value)
                mailbox.mailbox_address = mailbox_address
                mailbox.save()
            else:
                mailbox, created = mailbox_qs.get_or_create(
                    mailbox_address=mailbox_address,
                    defaults={**mailbox_defaults, "tenant": tenant},
                )
                for field, value in mailbox_defaults.items():
                    setattr(mailbox, field, value)
                mailbox.save()

            try:
                result = MailboxService.sync_mailbox(mailbox)
                mailbox.last_success_at = timezone.now()
                mailbox.last_error_message = ""
                mailbox.save(update_fields=["last_success_at", "last_error_message", "updated_at"])
                self._record_action(
                    tenant=mailbox.tenant,
                    actor_user=request.user,
                    action_type=EmailActionType.TEST_MAILBOX_CONNECTION,
                    action_status=EmailActionStatus.COMPLETED,
                    payload={"operation": action, "mailbox_id": mailbox.pk},
                    result=result,
                )
                if action == "update_mailbox":
                    messages.success(request, f"Mailbox '{mailbox.mailbox_address}' updated and verified successfully.")
                else:
                    messages.success(request, f"Mailbox '{mailbox.mailbox_address}' connected and verified successfully.")
                return redirect(self.get_connect_success_view_name())
            except Exception as exc:
                mailbox.last_error_message = str(exc)
                mailbox.save(update_fields=["last_error_message", "updated_at"])
                self._record_action(
                    tenant=mailbox.tenant,
                    actor_user=request.user,
                    action_type=EmailActionType.TEST_MAILBOX_CONNECTION,
                    action_status=EmailActionStatus.FAILED,
                    payload={"operation": action, "mailbox_id": mailbox.pk},
                    error=str(exc),
                )
                if action == "update_mailbox":
                    messages.error(request, f"Mailbox updated, but connection verification failed: {exc}")
                else:
                    messages.error(request, f"Mailbox saved, but connection verification failed: {exc}")
            return self._redirect_current()

        if action == "delete_mailbox":
            mailbox_id = (request.POST.get("mailbox_id") or "").strip()
            if not mailbox_id:
                messages.error(request, "Mailbox selection is required for delete.")
                return self._redirect_current()
            mailbox_qs = self._scoped_mailboxes(tenant=tenant, is_platform_admin=is_platform_admin)
            mailbox = mailbox_qs.filter(pk=mailbox_id).first()
            if mailbox is None:
                messages.error(request, "Mailbox not found for your tenant.")
                return self._redirect_current()
            mailbox.is_active = False
            mailbox.save(update_fields=["is_active", "updated_at"])
            messages.success(request, f"Mailbox '{mailbox.mailbox_address}' deleted.")
            return self._redirect_current()

        if action == "test_mailbox_connection":
            mailbox_id = request.POST.get("mailbox_id")
            mailbox_qs = self._scoped_mailboxes(tenant=tenant, is_platform_admin=is_platform_admin)
            mailbox = mailbox_qs.filter(pk=mailbox_id).first()
            if mailbox is None:
                messages.error(request, "Mailbox not found for your tenant.")
                return self._redirect_current()
            try:
                result = MailboxService.sync_mailbox(mailbox)
                mailbox.last_success_at = timezone.now()
                mailbox.last_error_message = ""
                mailbox.save(update_fields=["last_success_at", "last_error_message", "updated_at"])
                self._record_action(
                    tenant=mailbox.tenant,
                    actor_user=request.user,
                    action_type=EmailActionType.TEST_MAILBOX_CONNECTION,
                    action_status=EmailActionStatus.COMPLETED,
                    payload={"operation": "test_mailbox_connection", "mailbox_id": mailbox.pk},
                    result=result,
                )
                messages.success(request, f"Connection test succeeded for '{mailbox.mailbox_address}'.")
            except Exception as exc:
                mailbox.last_error_message = str(exc)
                mailbox.save(update_fields=["last_error_message", "updated_at"])
                self._record_action(
                    tenant=mailbox.tenant,
                    actor_user=request.user,
                    action_type=EmailActionType.TEST_MAILBOX_CONNECTION,
                    action_status=EmailActionStatus.FAILED,
                    payload={"operation": "test_mailbox_connection", "mailbox_id": mailbox.pk},
                    error=str(exc),
                )
                messages.error(request, f"Connection test failed: {exc}")
            return self._redirect_current()

        if action == "save_auto_sender_rules":
            mailbox_id = (request.POST.get("mailbox_id") or "").strip()
            sender_emails_text = (request.POST.get("auto_sender_emails") or "").strip()
            sender_names_text = (request.POST.get("auto_sender_names") or "").strip()
            if not mailbox_id:
                messages.error(request, "Mailbox is required.")
                return self._redirect_current()

            mailbox = self._scoped_mailboxes(tenant=tenant, is_platform_admin=is_platform_admin).filter(pk=mailbox_id).first()
            if mailbox is None:
                messages.error(request, "Mailbox not found for your tenant.")
                return self._redirect_current()

            config = mailbox.config_json if isinstance(mailbox.config_json, dict) else {}
            sender_emails = [item.lower() for item in self._parse_multivalue_text(sender_emails_text)]
            sender_names = self._parse_multivalue_text(sender_names_text)
            config["auto_process_sender_emails"] = sender_emails
            config["auto_process_sender_names"] = sender_names
            mailbox.config_json = config
            mailbox.save(update_fields=["config_json", "updated_at"])
            messages.success(request, f"Auto-process sender rules saved for mailbox '{mailbox.name}'.")
            return redirect(f"{self._redirect_current().url}?mailbox={mailbox.pk}")

        if action == "run_auto_sender_processing":
            mailbox_id = (request.POST.get("mailbox_id") or "").strip()
            if not mailbox_id:
                messages.error(request, "Mailbox is required.")
                return self._redirect_current()

            mailbox = self._scoped_mailboxes(tenant=tenant, is_platform_admin=is_platform_admin).filter(pk=mailbox_id).first()
            if mailbox is None:
                messages.error(request, "Mailbox not found for your tenant.")
                return self._redirect_current()

            config = mailbox.config_json if isinstance(mailbox.config_json, dict) else {}
            sender_emails = [str(v).strip().lower() for v in (config.get("auto_process_sender_emails") or []) if str(v).strip()]
            sender_names = [str(v).strip() for v in (config.get("auto_process_sender_names") or []) if str(v).strip()]

            if not sender_emails and not sender_names:
                messages.error(request, "Configure sender email or sender name rules first.")
                return redirect(f"{self._redirect_current().url}?mailbox={mailbox.pk}")

            msg_qs = (
                self._scoped_messages(tenant=tenant, is_platform_admin=is_platform_admin)
                .filter(mailbox=mailbox, direction=EmailDirection.INBOUND, has_attachments=True)
            )
            if sender_emails:
                msg_qs = msg_qs.filter(from_email__in=sender_emails)
            if sender_names:
                msg_qs = msg_qs.filter(from_name__in=sender_names)

            selected_ids = list(msg_qs.values_list("pk", flat=True)[:100])
            if not selected_ids:
                messages.warning(request, "No messages matched the configured sender rules.")
                return redirect(f"{self._redirect_current().url}?mailbox={mailbox.pk}")

            processed, failed = self._run_selected_messages(
                request=request,
                tenant=tenant,
                is_platform_admin=is_platform_admin,
                selected_ids=selected_ids,
            )
            if processed:
                messages.success(request, f"Auto processing completed for {processed} message(s).")
            if failed:
                messages.warning(request, f"{failed} message(s) failed during auto processing.")
            return redirect(f"{self._redirect_current().url}?mailbox={mailbox.pk}")

        if action == "ingest_manual_email":
            mailbox_id = request.POST.get("mailbox_id")
            provider_message_id = (request.POST.get("provider_message_id") or "").strip()
            from_email = (request.POST.get("from_email") or "").strip()
            from_name = (request.POST.get("from_name") or "").strip()
            subject = (request.POST.get("subject") or "").strip()
            selected_attachment_name = (request.POST.get("selected_attachment_name") or "").strip()
            body_text = (request.POST.get("body_text") or "").strip()

            if not mailbox_id:
                messages.error(request, "Mailbox is required.")
                return self._redirect_current()

            if not provider_message_id and (not from_email or not from_name or not subject or not selected_attachment_name):
                messages.error(request, "Please select Sender Email, Sender Name, Subject, and Attachment from the dropdowns.")
                return self._redirect_current()

            mailbox_qs = MailboxConfig.objects.filter(pk=mailbox_id, is_active=True)
            if tenant is not None and not is_platform_admin:
                mailbox_qs = mailbox_qs.filter(tenant=tenant)
            mailbox = mailbox_qs.first()
            if mailbox is None:
                messages.error(request, "Mailbox not found for your tenant.")
                return self._redirect_current()

            attachments = []
            try:
                adapter = MicrosoftGraphEmailAdapter()
                if provider_message_id:
                    target_message = adapter.get_message(mailbox, provider_message_id)
                else:
                    messages_from_graph = adapter.poll_messages(mailbox, since_cursor=None)
                    target_message = None
                    for msg in messages_from_graph:
                        if (
                            msg.get("from_email") == from_email
                            and msg.get("from_name") == from_name
                            and msg.get("subject") == subject
                        ):
                            target_message = msg
                            break

                if target_message is None:
                    messages.error(request, "Could not find the selected message in Graph API.")
                    return self._redirect_current()

                from_email = (target_message.get("from_email") or from_email or "").strip()
                from_name = (target_message.get("from_name") or from_name or "").strip()
                subject = (target_message.get("subject") or subject or "").strip()
                if not body_text:
                    body_text = (target_message.get("body_text") or "").strip()

                target_attachment = None
                msg_attachments = target_message.get("attachments") or []
                if not selected_attachment_name and len(msg_attachments) == 1:
                    selected_attachment_name = (msg_attachments[0].get("filename") or "").strip()
                for att in msg_attachments:
                    if att.get("filename") == selected_attachment_name:
                        target_attachment = att
                        break

                if target_attachment is None:
                    messages.error(request, f"Attachment '{selected_attachment_name}' not found in the selected message.")
                    return self._redirect_current()

                attachments.append({
                    "provider_attachment_id": target_attachment.get("provider_attachment_id") or uuid.uuid4().hex,
                    "filename": target_attachment.get("filename") or "attachment.bin",
                    "content_type": target_attachment.get("content_type") or "application/octet-stream",
                    "content_bytes": target_attachment.get("content_bytes") or b"",
                    "size_bytes": target_attachment.get("size_bytes") or 0,
                })
                
            except Exception as exc:
                messages.error(request, f"Failed to fetch attachment from Graph API: {str(exc)[:200]}")
                return self._redirect_current()

            payload = {
                "provider_message_id": f"ui-{uuid.uuid4().hex}",
                "internet_message_id": f"<{uuid.uuid4().hex}@local.email.integration>",
                "subject": subject,
                "from_email": from_email,
                "from_name": from_name,
                "to": [{"email": mailbox.mailbox_address}],
                "body_text": body_text,
                "attachments": attachments,
                "trace_id": uuid.uuid4().hex,
            }

            try:
                message_obj = InboundIngestionService.ingest_message_payload(
                    mailbox,
                    payload,
                    tenant=mailbox.tenant,
                    actor_user=request.user,
                )
                extraction_workbench_url = reverse("extraction:workbench")
                redirect_url = extraction_workbench_url

                if message_obj.linked_document_upload_id:
                    extraction_result = (
                        message_obj.linked_document_upload.extraction_results.order_by("-created_at").first()
                    )
                    if extraction_result is not None:
                        redirect_url = reverse(
                            "extraction:result_detail",
                            kwargs={"pk": extraction_result.pk},
                        )
                    elif message_obj.linked_document_upload.original_filename:
                        q = urlencode({"q": message_obj.linked_document_upload.original_filename})
                        redirect_url = f"{extraction_workbench_url}?{q}"

                messages.success(
                    request,
                    format_html(
                        "Run_Extraction_Agent started successfully for message #{}. Redirected to extraction.",
                        message_obj.pk,
                    ),
                )
                return redirect(redirect_url)
            except Exception as exc:
                messages.error(request, f"Email ingest failed: {exc}")
            return self._redirect_current()

        if action == "retry_message":
            message_id = request.POST.get("message_id")
            message_qs = self._scoped_messages(tenant=tenant, is_platform_admin=is_platform_admin).select_related("mailbox", "thread")
            message_obj = message_qs.filter(pk=message_id).first()
            if message_obj is None:
                messages.error(request, "Message not found for your tenant.")
                return self._redirect_current()
            try:
                self._retry_message(request=request, message_obj=message_obj)
                messages.success(request, f"Message #{message_obj.pk} reprocessed successfully.")
            except Exception as exc:
                self._record_action(
                    tenant=message_obj.tenant,
                    actor_user=request.user,
                    action_type=EmailActionType.TRIGGER_RECONCILIATION,
                    action_status=EmailActionStatus.FAILED,
                    trace_id=message_obj.trace_id,
                    email_message=message_obj,
                    thread=message_obj.thread,
                    payload={"operation": "retry_message", "message_id": message_obj.pk},
                    error=str(exc),
                )
                messages.error(request, f"Retry failed: {exc}")
            return self._redirect_current()

        if action == "override_route":
            message_id = request.POST.get("message_id")
            target_domain = (request.POST.get("target_domain") or "").strip()
            message_qs = self._scoped_messages(tenant=tenant, is_platform_admin=is_platform_admin).select_related("mailbox", "thread")
            message_obj = message_qs.filter(pk=message_id).first()
            if message_obj is None:
                messages.error(request, "Message not found for your tenant.")
                return self._redirect_current()
            try:
                self._override_route(request=request, message_obj=message_obj, target_domain=target_domain)
                messages.success(request, f"Message #{message_obj.pk} routed to {target_domain}.")
            except Exception as exc:
                self._record_action(
                    tenant=message_obj.tenant,
                    actor_user=request.user,
                    action_type=EmailActionType.REOPEN_ENTITY,
                    action_status=EmailActionStatus.FAILED,
                    trace_id=message_obj.trace_id,
                    email_message=message_obj,
                    thread=message_obj.thread,
                    payload={"operation": "override_route", "message_id": message_obj.pk, "target_domain": target_domain},
                    error=str(exc),
                )
                messages.error(request, f"Route override failed: {exc}")
            return self._redirect_current()

        if action == "ignore_message":
            message_id = request.POST.get("message_id")
            message_qs = self._scoped_messages(tenant=tenant, is_platform_admin=is_platform_admin).select_related("thread")
            message_obj = message_qs.filter(pk=message_id).first()
            if message_obj is None:
                messages.error(request, "Message not found for your tenant.")
                return self._redirect_current()
            self._ignore_message(request=request, message_obj=message_obj)
            messages.success(request, f"Message #{message_obj.pk} ignored.")
            return self._redirect_current()

        if action == "run_selected_messages":
            selected_ids = request.POST.getlist("selected_message_ids")
            if not selected_ids:
                messages.error(request, "Please select at least one email message.")
                return self._redirect_current()
            processed, failed = self._run_selected_messages(
                request=request,
                tenant=tenant,
                is_platform_admin=is_platform_admin,
                selected_ids=selected_ids,
            )
            if processed:
                messages.success(request, f"Processed {processed} selected message(s).")
            if failed:
                messages.warning(request, f"{failed} selected message(s) failed during processing.")
            return self._redirect_current()

        if action == "send_outbound_email":
            mailbox_id = request.POST.get("mailbox_id")
            mailbox = self._scoped_mailboxes(tenant=tenant, is_platform_admin=is_platform_admin).filter(pk=mailbox_id).first()
            if mailbox is None:
                messages.error(request, "Mailbox not found for your tenant.")
                return self._redirect_current()
            if not mailbox.is_outbound_enabled:
                messages.error(request, "Selected mailbox is not outbound-enabled.")
                return self._redirect_current()
            try:
                self._send_outbound(request=request, mailbox=mailbox, tenant=mailbox.tenant)
                messages.success(request, "Outbound email sent and logged.")
            except Exception as exc:
                messages.error(request, f"Outbound email failed: {exc}")
            return self._redirect_current()

        if action == "trigger_poll":
            poll_mailboxes_task.delay(tenant_id=getattr(tenant, "pk", None))
            messages.success(request, "Polling triggered.")
            return self._redirect_current()

        if action == "retry_failed_actions":
            retry_failed_email_actions_task.delay(tenant_id=getattr(tenant, "pk", None))
            messages.success(request, "Failed email actions queued for retry.")
            return self._redirect_current()

        if action == "relink_threads":
            relink_email_threads_task.delay(tenant_id=getattr(tenant, "pk", None))
            messages.success(request, "Thread relink queued.")
            return self._redirect_current()

        messages.error(request, "Unknown action.")
        return self._redirect_current()

    def get(self, request, *args, **kwargs):
        context = self._build_operational_context(request)
        return render(request, self.template_name, context)


class EmailIntegrationConnectView(EmailIntegrationDashboardView):
    """First screen: tenant-specific mailbox connection setup."""

    template_name = "email_integration/connect_mailbox.html"
    redirect_view_name = "email_integration:connect_mailbox"
    connect_success_view_name = "email_integration:dashboard"

    def get(self, request, *args, **kwargs):
        tenant = getattr(request, "tenant", None)
        is_platform_admin = getattr(request.user, "is_platform_admin", False)
        config_mode = (request.GET.get("config") or "").strip() in {"1", "true", "yes"}
        if not config_mode and self._verified_mailboxes(tenant=tenant, is_platform_admin=is_platform_admin).exists():
            return redirect("email_integration:dashboard")

        context = self._build_operational_context(request)
        edit_mailbox = None
        edit_mailbox_id = (request.GET.get("edit") or "").strip()
        if edit_mailbox_id:
            edit_mailbox = self._scoped_mailboxes(tenant=tenant, is_platform_admin=is_platform_admin).filter(pk=edit_mailbox_id).first()

        context.update(
            {
                "page_title": "Connect Mailbox",
                "config_mode": config_mode,
                "editing_mailbox": edit_mailbox,
            }
        )
        return render(request, self.template_name, context)


class EmailIntegrationFeatureView(EmailIntegrationDashboardView):
    """Base class for dedicated tenant-scoped feature pages."""

    page_title = "Email Integration"
    page_description = ""

    def get(self, request, *args, **kwargs):
        redirect_response = self._require_verified_mailbox(request)
        if redirect_response is not None:
            return redirect_response
        context = self._build_operational_context(request)
        context.update(
            {
                "page_title": self.page_title,
                "page_description": self.page_description,
            }
        )
        return render(request, self.template_name, context)


class EmailIntegrationFunctionalityDashboardView(EmailIntegrationFeatureView):
    template_name = "email_integration/dashboard.html"
    redirect_view_name = "email_integration:dashboard"
    page_title = "Functionality Dashboard"
    page_description = "Choose a tenant-scoped email capability to continue."


class EmailIntegrationIngestView(EmailIntegrationFeatureView):
    template_name = "email_integration/ingest.html"
    redirect_view_name = "email_integration:ingest"
    page_title = "Ingest Email + PDF"
    page_description = "Upload and process inbound email content for the current tenant."


class EmailIntegrationMailboxHealthView(EmailIntegrationFeatureView):
    template_name = "email_integration/mailbox_health.html"
    redirect_view_name = "email_integration:mailbox_health"
    page_title = "Mailbox Health"
    page_description = "Monitor, test, and review tenant mailbox connection status."


class EmailIntegrationInboxProcessingView(EmailIntegrationFeatureView):
    template_name = "email_integration/inbox_processing.html"
    redirect_view_name = "email_integration:inbox_processing"
    page_title = "Inbox Processing"
    page_description = "Review inbound messages with attachments and run processing operations."


class EmailIntegrationRecentMessagesView(EmailIntegrationFeatureView):
    template_name = "email_integration/recent_messages.html"
    redirect_view_name = "email_integration:recent_messages"
    page_title = "Recent Messages"
    page_description = "Inspect recent tenant emails and perform retry, ignore, or route override actions."


class EmailIntegrationTriageQueueView(EmailIntegrationFeatureView):
    template_name = "email_integration/triage_queue.html"
    redirect_view_name = "email_integration:triage_queue"
    page_title = "Triage Queue"
    page_description = "Review unresolved or triaged tenant emails and relink related threads."


class EmailIntegrationFailedActionsView(EmailIntegrationFeatureView):
    template_name = "email_integration/failed_actions.html"
    redirect_view_name = "email_integration:failed_actions"
    page_title = "Failed Actions"
    page_description = "Monitor failed email operations and retry them safely."


class EmailIntegrationActionLedgerView(EmailIntegrationFeatureView):
    template_name = "email_integration/action_ledger.html"
    redirect_view_name = "email_integration:action_ledger"
    page_title = "Action Ledger"
    page_description = "Review the tenant-specific governed audit trail for email actions."


class EmailIntegrationOutboundEmailView(EmailIntegrationFeatureView):
    template_name = "email_integration/outbound_email.html"
    redirect_view_name = "email_integration:outbound_email"
    page_title = "Send Outbound Email"
    page_description = "Send templated or custom outbound messages from verified tenant mailboxes."


class EmailIntegrationDropdownDataView(View):
    """AJAX endpoint to fetch dropdown data dynamically from Microsoft Graph API."""

    @staticmethod
    def _fetch_graph_message_metadata(mailbox, logger):
        import time
        demo_sender_limit = 10
        adapter = MicrosoftGraphEmailAdapter()
        print(f"[DropdownView] _fetch_graph START mailbox={mailbox.mailbox_address}")
        logger.info(f"Calling Graph API for mailbox {mailbox.mailbox_address} (paginated metadata fetch)")
        t0 = time.time()
        messages = adapter.poll_all_messages_metadata(
            mailbox,
            since_cursor=None,
            max_unique_sender_emails=demo_sender_limit,
        )
        elapsed = time.time() - t0
        print(
            f"[DropdownView] _fetch_graph DONE: {len(messages)} messages in {elapsed:.2f}s "
            f"(demo sender limit={demo_sender_limit})"
        )
        logger.info(
            f"Graph API returned {len(messages)} messages in {elapsed:.2f}s "
            f"with demo sender limit={demo_sender_limit}"
        )
        return adapter, messages

    @staticmethod
    def _get_unique_sender_emails(messages):
        sender_emails = set()
        for msg in messages:
            msg_from_email = (msg.get("from_email") or "").strip()
            if msg_from_email:
                sender_emails.add(msg_from_email)
        return sorted(list(sender_emails))

    @staticmethod
    def _get_sender_names(messages, sender_email):
        sender_names = set()
        for msg in messages:
            msg_from_email = (msg.get("from_email") or "").strip()
            msg_from_name = (msg.get("from_name") or "").strip()
            if msg_from_email == sender_email and msg_from_name:
                sender_names.add(msg_from_name)
        return sorted(list(sender_names))

    @staticmethod
    def _get_subjects(messages, sender_email, sender_name):
        subjects = set()
        for msg in messages:
            msg_from_email = (msg.get("from_email") or "").strip()
            msg_from_name = (msg.get("from_name") or "").strip()
            msg_subject = (msg.get("subject") or "").strip()
            if msg_from_email == sender_email and msg_from_name == sender_name and msg_subject:
                subjects.add(msg_subject)
        return sorted(list(subjects))

    @staticmethod
    def _get_provider_message_ids(messages, sender_email, sender_name, subject):
        provider_message_ids = []
        for msg in messages:
            msg_from_email = (msg.get("from_email") or "").strip()
            msg_from_name = (msg.get("from_name") or "").strip()
            msg_subject = (msg.get("subject") or "").strip()
            provider_message_id = (msg.get("provider_message_id") or "").strip()
            matches = True
            if sender_email and msg_from_email != sender_email:
                matches = False
            if sender_name and msg_from_name != sender_name:
                matches = False
            if subject and msg_subject != subject:
                matches = False
            if matches and provider_message_id:
                if provider_message_id not in provider_message_ids:
                    provider_message_ids.append(provider_message_id)
        return provider_message_ids

    @staticmethod
    def _get_attachments(adapter, mailbox, provider_message_ids, logger):
        demo_attachment_limit = 10
        attachments = []
        token = None
        try:
            token = adapter._get_access_token(mailbox)
        except Exception as exc:
            logger.warning(f"Failed to prefetch Graph token for attachment summaries: {exc}")
        for provider_message_id in provider_message_ids:
            try:
                msg_attachments = adapter.get_attachment_summaries(
                    mailbox,
                    provider_message_id,
                    _token=token,
                )
            except Exception as exc:
                logger.warning(f"Failed to fetch attachments for message {provider_message_id}: {exc}")
                continue
            for att in msg_attachments:
                att_data = {
                    "filename": att.get("filename") or "file",
                    "timestamp": att.get("timestamp") or "",
                    "content_type": att.get("content_type") or "",
                }
                if att_data not in attachments:
                    attachments.append(att_data)
                if len(attachments) >= demo_attachment_limit:
                    logger.info(
                        f"Attachment demo limit reached ({demo_attachment_limit}) for mailbox {mailbox.mailbox_address}"
                    )
                    return attachments[:demo_attachment_limit]
        return attachments[:demo_attachment_limit]

    def get(self, request, *args, **kwargs):
        """
        Fetch dropdown data from Graph API based on filter parameters.
        Query params:
        - mailbox_id: Required. The mailbox to fetch from.
        - sender_email: Optional. Filter by sender email.
        - sender_name: Optional. Filter by sender name.
        - subject: Optional. Filter by subject.

        Returns JSON with appropriate dropdown options at the current level.
        """
        import logging

        logger = logging.getLogger(__name__)

        try:
            # Return JSON for unauthenticated requests so AJAX caller sees a clear error
            if not request.user.is_authenticated:
                return JsonResponse({"error": "Not authenticated - please log in again"}, status=401)

            tenant = getattr(request, "tenant", None)
            is_platform_admin = getattr(request.user, "is_platform_admin", False)

            mailbox_id = request.GET.get("mailbox_id", "").strip()
            sender_email = request.GET.get("sender_email", "").strip()
            sender_name = request.GET.get("sender_name", "").strip()
            subject = request.GET.get("subject", "").strip()

            logger.info(f"Dropdown endpoint called with mailbox_id={mailbox_id}, sender_email={sender_email}")

            if not mailbox_id:
                logger.error("mailbox_id is required but not provided")
                return JsonResponse({"error": "mailbox_id is required"}, status=400)

            mailbox_qs = MailboxConfig.objects.filter(pk=mailbox_id, is_active=True)
            if tenant is not None and not is_platform_admin:
                mailbox_qs = mailbox_qs.filter(tenant=tenant)

            mailbox = mailbox_qs.first()
            if mailbox is None:
                logger.warning(f"Mailbox {mailbox_id} not found or not accessible to user")
                return JsonResponse({"error": "Mailbox not found or not accessible"}, status=404)

            logger.info(f"Mailbox found: {mailbox.mailbox_address}, provider={mailbox.provider}")

            config_json = mailbox.config_json if isinstance(mailbox.config_json, dict) else {}
            tenant_id = config_json.get("tenant_id", "").strip()
            client_id = config_json.get("client_id", "").strip()
            client_secret = config_json.get("client_secret", "").strip()

            if not tenant_id or not client_id or not client_secret:
                logger.error(f"Mailbox {mailbox.mailbox_address} is missing Graph credentials")
                return JsonResponse(
                    {
                        "error": "Mailbox is not configured with Graph API credentials (tenant_id, client_id, client_secret)",
                        "missing_fields": [
                            "tenant_id" if not tenant_id else None,
                            "client_id" if not client_id else None,
                            "client_secret" if not client_secret else None,
                        ],
                    },
                    status=400,
                )

            try:
                adapter, messages = self._fetch_graph_message_metadata(mailbox, logger)
            except Exception as exc:
                logger.exception(f"Graph API error: {exc}")
                return JsonResponse(
                    {
                        "error": f"Graph API error: {str(exc)[:500]}",
                        "details": type(exc).__name__,
                    },
                    status=500,
                )

            if not messages:
                logger.warning("No messages returned from Graph API")
                return JsonResponse(
                    {
                        "type": "all_filters",
                        "sender_emails": [],
                        "sender_names": [],
                        "subjects": [],
                        "warning": "No messages found in mailbox",
                    }
                )

            if not sender_email:
                # Return all three filter lists in one shot so the UI can
                # populate sender_email, sender_name and subject simultaneously.
                sender_emails = self._get_unique_sender_emails(messages)
                all_names: list = []
                for em in sender_emails:
                    for n in self._get_sender_names(messages, em):
                        if n not in all_names:
                            all_names.append(n)
                all_names.sort()
                all_subjects: list = []
                for msg in messages:
                    s = (msg.get("subject") or "").strip()
                    if s and s not in all_subjects:
                        all_subjects.append(s)
                all_subjects.sort()
                logger.info(
                    f"Returning all_filters: {len(sender_emails)} emails, "
                    f"{len(all_names)} names, {len(all_subjects)} subjects"
                )
                return JsonResponse(
                    {
                        "type": "all_filters",
                        "sender_emails": sender_emails,
                        "sender_names": all_names,
                        "subjects": all_subjects,
                    }
                )

            if sender_email and sender_name and not subject:
                # Legacy/unused but kept for safety
                subjects = self._get_subjects(messages, sender_email, sender_name)
                logger.info(f"Returning {len(subjects)} subjects for {sender_email}/{sender_name}")
                return JsonResponse({"type": "subjects", "data": subjects})

            if sender_email or sender_name or subject:
                provider_message_ids = self._get_provider_message_ids(
                    messages,
                    sender_email,
                    sender_name,
                    subject,
                )
                attachments = self._get_attachments(adapter, mailbox, provider_message_ids, logger)
                logger.info(
                    f"Returning {len(attachments)} attachments for filters "
                    f"email={sender_email or '-'} name={sender_name or '-'} subject={subject or '-'}"
                )
                return JsonResponse({"type": "attachments", "data": attachments})

            logger.warning("Invalid filter combination")
            return JsonResponse({"error": "Invalid filter combination"}, status=400)

        except Exception as exc:
            logger.exception(f"Unexpected error in dropdown endpoint: {exc}")
            import traceback

            traceback.print_exc()
            return JsonResponse({"error": f"Server error: {str(exc)[:500]}"}, status=500)


class EmailIntegrationInboxPreviewView(View):
    """AJAX endpoint for a lightweight Outlook-style inbox list and preview panel."""

    @staticmethod
    def _resolve_mailbox(request, mailbox_id):
        tenant = getattr(request, "tenant", None)
        is_platform_admin = getattr(request.user, "is_platform_admin", False)
        mailbox_qs = MailboxConfig.objects.filter(pk=mailbox_id, is_active=True)
        if tenant is not None and not is_platform_admin:
            mailbox_qs = mailbox_qs.filter(tenant=tenant)
        return mailbox_qs.first()

    def get(self, request, *args, **kwargs):
        import logging

        logger = logging.getLogger(__name__)
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated - please log in again"}, status=401)

        mailbox_id = (request.GET.get("mailbox_id") or "").strip()
        provider_message_id = (request.GET.get("provider_message_id") or "").strip()
        if not mailbox_id:
            return JsonResponse({"error": "mailbox_id is required"}, status=400)

        mailbox = self._resolve_mailbox(request, mailbox_id)
        if mailbox is None:
            return JsonResponse({"error": "Mailbox not found or not accessible"}, status=404)

        adapter = MicrosoftGraphEmailAdapter()
        try:
            if provider_message_id:
                preview = adapter.get_message_preview(mailbox, provider_message_id)
                logger.info(f"Returning inbox preview for message {provider_message_id}")
                return JsonResponse({"type": "message_preview", "data": preview})

            messages = adapter.poll_message_previews(mailbox, limit=10)
            logger.info(f"Returning {len(messages)} inbox preview messages for mailbox {mailbox.mailbox_address}")
            return JsonResponse({"type": "message_list", "data": messages})
        except Exception as exc:
            logger.exception(f"Inbox preview error: {exc}")
            return JsonResponse({"error": f"Inbox preview error: {str(exc)[:500]}"}, status=500)
