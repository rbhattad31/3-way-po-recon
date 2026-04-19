"""Template views for email integration UI pages."""
import json
import uuid

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View

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
from apps.email_integration.tasks import poll_mailboxes_task, relink_email_threads_task, retry_failed_email_actions_task


class EmailIntegrationDashboardView(PermissionRequiredMixin, View):
    """Simple UI dashboard for mailbox and message operations."""

    required_permission = "email.view"

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
            rendered_subject = ""
            rendered_body = ""
            template = EmailTemplate.objects.filter(template_code=template_code, is_active=True).filter(Q(tenant=tenant) | Q(tenant__isnull=True)).first()
            if template is not None:
                rendered_subject = template.subject_template or ""
                rendered_body = template.body_text_template or ""
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

    def post(self, request, *args, **kwargs):
        action = (request.POST.get("action") or "").strip()
        can_manage = self._can_manage(request)
        can_send = self._can_send(request)

        if action == "send_outbound_email" and not can_send:
            messages.error(request, "You do not have permission to send outbound email.")
            return redirect("email_integration:dashboard")

        if action != "send_outbound_email" and not can_manage:
            messages.error(request, "You do not have permission to manage email integration.")
            return redirect("email_integration:dashboard")

        tenant = getattr(request, "tenant", None)
        is_platform_admin = getattr(request.user, "is_platform_admin", False)

        if action == "create_mailbox":
            name = (request.POST.get("name") or "").strip()
            mailbox_address = (request.POST.get("mailbox_address") or "").strip()
            provider = (request.POST.get("provider") or "").strip() or self._guess_provider(mailbox_address)
            if not mailbox_address:
                messages.error(request, "Mailbox email address is required.")
                return redirect("email_integration:dashboard")
            if not name:
                name = self._default_mailbox_name(mailbox_address)

            mailbox_defaults = {
                "name": name,
                "provider": provider,
                "is_active": True,
                "is_inbound_enabled": True,
                "is_outbound_enabled": False,
                "webhook_enabled": True,
                "polling_enabled": False,
            }
            mailbox_qs = MailboxConfig.objects.all()
            if tenant is not None and not is_platform_admin:
                mailbox_qs = mailbox_qs.filter(tenant=tenant)
            mailbox, created = mailbox_qs.get_or_create(
                mailbox_address=mailbox_address,
                defaults={**mailbox_defaults, "tenant": tenant},
            )
            if not created:
                for field, value in mailbox_defaults.items():
                    setattr(mailbox, field, value)
                mailbox.save()
                messages.success(request, f"Mailbox '{mailbox.mailbox_address}' updated.")
            else:
                messages.success(request, f"Mailbox '{mailbox.mailbox_address}' created.")
            return redirect("email_integration:dashboard")

        if action == "test_mailbox_connection":
            mailbox_id = request.POST.get("mailbox_id")
            mailbox_qs = self._scoped_mailboxes(tenant=tenant, is_platform_admin=is_platform_admin)
            mailbox = mailbox_qs.filter(pk=mailbox_id).first()
            if mailbox is None:
                messages.error(request, "Mailbox not found for your tenant.")
                return redirect("email_integration:dashboard")
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
            return redirect("email_integration:dashboard")

        if action == "ingest_manual_email":
            mailbox_id = request.POST.get("mailbox_id")
            from_email = (request.POST.get("from_email") or "").strip()
            subject = (request.POST.get("subject") or "").strip()
            body_text = (request.POST.get("body_text") or "").strip()
            from_name = (request.POST.get("from_name") or "").strip()
            upload_file = request.FILES.get("attachment_file")

            if not mailbox_id:
                messages.error(request, "Mailbox is required.")
                return redirect("email_integration:dashboard")

            if upload_file is None and not subject and not body_text:
                messages.error(request, "Please add an attachment or enter a short message.")
                return redirect("email_integration:dashboard")

            mailbox_qs = MailboxConfig.objects.filter(pk=mailbox_id, is_active=True)
            if tenant is not None and not is_platform_admin:
                mailbox_qs = mailbox_qs.filter(tenant=tenant)
            mailbox = mailbox_qs.first()
            if mailbox is None:
                messages.error(request, "Mailbox not found for your tenant.")
                return redirect("email_integration:dashboard")

            attachments = []
            if upload_file is not None:
                attachments.append({
                    "provider_attachment_id": uuid.uuid4().hex,
                    "filename": upload_file.name,
                    "content_type": getattr(upload_file, "content_type", "application/octet-stream"),
                    "content_bytes": upload_file.read(),
                    "size_bytes": upload_file.size,
                })

            if not from_email:
                from_email = "customer-upload@local.email.integration"
            if not from_name and from_email:
                from_name = from_email.split("@", 1)[0].replace(".", " ").replace("_", " ").title()
            if not subject:
                if upload_file is not None:
                    subject = f"Customer upload - {upload_file.name}"
                else:
                    subject = "Customer email upload"

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
                messages.success(request, f"Email ingested successfully (message #{message_obj.pk}).")
            except Exception as exc:
                messages.error(request, f"Email ingest failed: {exc}")
            return redirect("email_integration:dashboard")

        if action == "retry_message":
            message_id = request.POST.get("message_id")
            message_qs = self._scoped_messages(tenant=tenant, is_platform_admin=is_platform_admin).select_related("mailbox", "thread")
            message_obj = message_qs.filter(pk=message_id).first()
            if message_obj is None:
                messages.error(request, "Message not found for your tenant.")
                return redirect("email_integration:dashboard")
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
            return redirect("email_integration:dashboard")

        if action == "override_route":
            message_id = request.POST.get("message_id")
            target_domain = (request.POST.get("target_domain") or "").strip()
            message_qs = self._scoped_messages(tenant=tenant, is_platform_admin=is_platform_admin).select_related("mailbox", "thread")
            message_obj = message_qs.filter(pk=message_id).first()
            if message_obj is None:
                messages.error(request, "Message not found for your tenant.")
                return redirect("email_integration:dashboard")
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
            return redirect("email_integration:dashboard")

        if action == "ignore_message":
            message_id = request.POST.get("message_id")
            message_qs = self._scoped_messages(tenant=tenant, is_platform_admin=is_platform_admin).select_related("thread")
            message_obj = message_qs.filter(pk=message_id).first()
            if message_obj is None:
                messages.error(request, "Message not found for your tenant.")
                return redirect("email_integration:dashboard")
            self._ignore_message(request=request, message_obj=message_obj)
            messages.success(request, f"Message #{message_obj.pk} ignored.")
            return redirect("email_integration:dashboard")

        if action == "run_selected_messages":
            selected_ids = request.POST.getlist("selected_message_ids")
            if not selected_ids:
                messages.error(request, "Please select at least one email message.")
                return redirect("email_integration:dashboard")
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
            return redirect("email_integration:dashboard")

        if action == "send_outbound_email":
            mailbox_id = request.POST.get("mailbox_id")
            mailbox = self._scoped_mailboxes(tenant=tenant, is_platform_admin=is_platform_admin).filter(pk=mailbox_id).first()
            if mailbox is None:
                messages.error(request, "Mailbox not found for your tenant.")
                return redirect("email_integration:dashboard")
            if not mailbox.is_outbound_enabled:
                messages.error(request, "Selected mailbox is not outbound-enabled.")
                return redirect("email_integration:dashboard")
            try:
                self._send_outbound(request=request, mailbox=mailbox, tenant=mailbox.tenant)
                messages.success(request, "Outbound email sent and logged.")
            except Exception as exc:
                messages.error(request, f"Outbound email failed: {exc}")
            return redirect("email_integration:dashboard")

        if action == "trigger_poll":
            poll_mailboxes_task.delay(tenant_id=getattr(tenant, "pk", None))
            messages.success(request, "Polling triggered.")
            return redirect("email_integration:dashboard")

        if action == "retry_failed_actions":
            retry_failed_email_actions_task.delay(tenant_id=getattr(tenant, "pk", None))
            messages.success(request, "Failed email actions queued for retry.")
            return redirect("email_integration:dashboard")

        if action == "relink_threads":
            relink_email_threads_task.delay(tenant_id=getattr(tenant, "pk", None))
            messages.success(request, "Thread relink queued.")
            return redirect("email_integration:dashboard")

        messages.error(request, "Unknown action.")
        return redirect("email_integration:dashboard")

    def get(self, request, *args, **kwargs):
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
        messages = EmailMessage.objects.all()
        actions = EmailAction.objects.all()

        if tenant is not None and not is_platform_admin:
            mailboxes = mailboxes.filter(tenant=tenant)
            threads = threads.filter(tenant=tenant)
            messages = messages.filter(tenant=tenant)
            actions = actions.filter(tenant=tenant)

        if mailbox_filter:
            messages = messages.filter(mailbox_id=mailbox_filter)
            actions = actions.filter(email_message__mailbox_id=mailbox_filter)
        if processing_filter:
            messages = messages.filter(processing_status=processing_filter)
        if classification_filter:
            messages = messages.filter(message_classification=classification_filter)
        if routing_filter:
            messages = messages.filter(routing_status=routing_filter)
        if action_status_filter:
            actions = actions.filter(action_status=action_status_filter)
        if query:
            messages = messages.filter(
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
            inbox_messages = (
                messages.filter(direction=EmailDirection.INBOUND, has_attachments=True)
                .select_related("mailbox", "thread", "linked_document_upload")
                .prefetch_related("attachments")
                .order_by("-received_at", "-created_at")[:25]
            )

        context = {
            "mailbox_count": mailboxes.count(),
            "thread_count": threads.count(),
            "message_count": messages.count(),
            "action_count": actions.count(),
            "inbox_messages": inbox_messages,
            "inbox_requires_mailbox_selection": inbox_requires_mailbox_selection,
            "recent_messages": (
                messages.select_related("mailbox", "thread", "linked_document_upload")
                .prefetch_related("attachments")
                .order_by("-received_at", "-created_at")[:20]
            ),
            "recent_actions": (
                actions.select_related("email_message", "performed_by_user")
                .order_by("-created_at")[:20]
            ),
            "triage_messages": (
                messages.filter(routing_status=EmailRoutingStatus.TRIAGED)
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
            "can_manage": self._can_manage(request),
            "can_send": self._can_send(request),
            "mailbox_health": mailbox_health,
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
        return render(request, "email_integration/dashboard.html", context)
