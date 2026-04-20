"""
Seed demo data for the email integration app.

Creates:
  - 2 MailboxConfig records (AP inbound/outbound + procurement inbound)
  - 6 EmailTemplate records (AP clarification, procurement supplier templates, etc.)
  - 3 EmailThread records (one AP invoice, one procurement quotation, one clarification reply)
  - 9 EmailMessage records across the three threads
  - EmailParticipant records per message
  - EmailRoutingDecision + EmailAction records per message

Usage:
    python manage.py seed_email_data
    python manage.py seed_email_data --flush   # wipe existing and re-seed
    python manage.py seed_email_data --tenant <id>  # seed for specific tenant
"""
from __future__ import annotations

import uuid
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Seed demo mailboxes, templates, threads, and messages for the email integration app"

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete existing email seed data before re-seeding",
        )
        parser.add_argument(
            "--tenant",
            type=int,
            default=None,
            help="Tenant (CompanyProfile) PK to seed data for. Defaults to first tenant.",
        )

    def handle(self, *args, **options):
        from apps.accounts.models import CompanyProfile
        from apps.email_integration.enums import (
            EmailActionStatus,
            EmailActionType,
            EmailDirection,
            EmailDomainContext,
            EmailIntentType,
            EmailLinkStatus,
            EmailMessageClassification,
            EmailParticipantRoleType,
            EmailProcessingStatus,
            EmailProvider,
            EmailRoutingDecisionStatus,
            EmailRoutingDecisionType,
            EmailRoutingStatus,
            EmailTemplateDomainScope,
            EmailThreadStatus,
            MailboxAuthMode,
            MailboxType,
            SenderTrustLevel,
            TargetDomain,
        )
        from apps.email_integration.models import (
            EmailAction,
            EmailAttachment,
            EmailMessage,
            EmailParticipant,
            EmailRoutingDecision,
            EmailTemplate,
            EmailThread,
            MailboxConfig,
        )

        # ------------------------------------------------------------------ #
        # tenant resolution
        # ------------------------------------------------------------------ #
        tenant_id = options.get("tenant")
        if tenant_id:
            tenant = CompanyProfile.objects.filter(pk=tenant_id).first()
            if not tenant:
                self.stderr.write(self.style.ERROR(f"Tenant {tenant_id} not found."))
                return
        else:
            tenant = CompanyProfile.objects.first()

        if not tenant:
            self.stderr.write(self.style.ERROR("No CompanyProfile found. Run seed_config first."))
            return

        self.stdout.write(f"Seeding email data for tenant: {tenant.name}")

        # ------------------------------------------------------------------ #
        # optional flush
        # ------------------------------------------------------------------ #
        if options["flush"]:
            EmailAction.objects.filter(tenant=tenant).delete()
            EmailRoutingDecision.objects.filter(tenant=tenant).delete()
            EmailParticipant.objects.filter(tenant=tenant).delete()
            EmailAttachment.objects.filter(tenant=tenant).delete()
            EmailMessage.objects.filter(tenant=tenant).delete()
            EmailThread.objects.filter(tenant=tenant).delete()
            EmailTemplate.objects.filter(tenant=tenant).delete()
            MailboxConfig.objects.filter(tenant=tenant).delete()
            self.stdout.write(self.style.WARNING("  Flushed existing email data."))

        # ------------------------------------------------------------------ #
        # 1. Mailbox configs
        # ------------------------------------------------------------------ #
        ap_mailbox, created = MailboxConfig.objects.get_or_create(
            tenant=tenant,
            mailbox_address="ap-invoices@demo.example.com",
            defaults=dict(
                name="AP Invoices Inbox",
                provider=EmailProvider.MICROSOFT_365,
                mailbox_type=MailboxType.SHARED,
                auth_mode=MailboxAuthMode.OAUTH,
                is_inbound_enabled=True,
                is_outbound_enabled=True,
                webhook_enabled=True,
                polling_enabled=False,
                config_json={
                    "webhook_token": "demo-webhook-token-ap",
                    "tenant_id": "demo-azure-tenant",
                    "client_id": "demo-client-id",
                    "description": "Demo AP mailbox - replace with real Microsoft Graph credentials",
                },
                is_active=True,
            ),
        )
        self.stdout.write(self.style.SUCCESS(f"  {'Created' if created else 'Exists'} mailbox: {ap_mailbox.mailbox_address}"))

        proc_mailbox, created = MailboxConfig.objects.get_or_create(
            tenant=tenant,
            mailbox_address="procurement@demo.example.com",
            defaults=dict(
                name="Procurement Inbox",
                provider=EmailProvider.MICROSOFT_365,
                mailbox_type=MailboxType.SHARED,
                auth_mode=MailboxAuthMode.OAUTH,
                is_inbound_enabled=True,
                is_outbound_enabled=True,
                webhook_enabled=True,
                polling_enabled=False,
                config_json={
                    "webhook_token": "demo-webhook-token-proc",
                    "tenant_id": "demo-azure-tenant",
                    "client_id": "demo-client-id",
                    "description": "Demo procurement mailbox - replace with real credentials",
                },
                is_active=True,
            ),
        )
        self.stdout.write(self.style.SUCCESS(f"  {'Created' if created else 'Exists'} mailbox: {proc_mailbox.mailbox_address}"))

        # ------------------------------------------------------------------ #
        # 2. Email templates
        # ------------------------------------------------------------------ #
        templates = [
            dict(
                template_code="AP_VENDOR_CLARIFICATION",
                template_name="AP Vendor Clarification Request",
                domain_scope=EmailTemplateDomainScope.AP,
                subject_template="Clarification Required - Invoice {{ invoice_reference }}",
                body_text_template=(
                    "Dear Vendor,\n\n"
                    "We require clarification on the following points for Invoice {{ invoice_reference }}:\n\n"
                    "{{ clarification_list }}\n\n"
                    "Please respond at your earliest convenience.\n\n"
                    "Regards,\nAP Team"
                ),
                required_variables_json=["invoice_reference", "clarification_list"],
                is_active=True,
            ),
            dict(
                template_code="AP_APPROVAL_REQUEST",
                template_name="AP Invoice Approval Request",
                domain_scope=EmailTemplateDomainScope.AP,
                subject_template="Approval Required - Invoice {{ invoice_reference }} (Case {{ ap_case_id }})",
                body_text_template=(
                    "Dear {{ approver_name }},\n\n"
                    "Invoice {{ invoice_reference }} requires your approval.\n\n"
                    "Amount: {{ invoice_amount }}\n"
                    "Vendor: {{ vendor_name }}\n\n"
                    "Please review and respond with APPROVED or REJECTED.\n\n"
                    "Regards,\nAP Team"
                ),
                required_variables_json=["approver_name", "invoice_reference", "invoice_amount", "vendor_name"],
                is_active=True,
            ),
            dict(
                template_code="AP_REJECTION_NOTICE",
                template_name="AP Invoice Rejection Notice",
                domain_scope=EmailTemplateDomainScope.AP,
                subject_template="Invoice Rejected - {{ invoice_reference }}",
                body_text_template=(
                    "Dear Vendor,\n\n"
                    "We regret to inform you that Invoice {{ invoice_reference }} "
                    "has been rejected.\n\nReason: {{ rejection_reason }}\n\n"
                    "Please resubmit with the required corrections.\n\nRegards,\nAP Team"
                ),
                required_variables_json=["invoice_reference", "rejection_reason"],
                is_active=True,
            ),
            dict(
                template_code="PROCUREMENT_SUPPLIER_CLARIFICATION",
                template_name="Procurement Supplier Clarification Request",
                domain_scope=EmailTemplateDomainScope.PROCUREMENT,
                subject_template="Clarification Required - RFQ {{ rfq_reference }}",
                body_text_template=(
                    "Dear Supplier,\n\n"
                    "We require clarification on the following points for RFQ {{ rfq_reference }}:\n\n"
                    "{{ clarification_list }}\n\n"
                    "Please respond by return email.\n\n"
                    "Regards,\nProcurement Team"
                ),
                required_variables_json=["rfq_reference", "clarification_list"],
                is_active=True,
            ),
            dict(
                template_code="PROCUREMENT_RFQ_SUBMISSION",
                template_name="RFQ Submission Confirmation",
                domain_scope=EmailTemplateDomainScope.PROCUREMENT,
                subject_template="RFQ Received - {{ rfq_reference }}",
                body_text_template=(
                    "Dear Supplier,\n\n"
                    "We confirm receipt of your quotation for RFQ {{ rfq_reference }}.\n\n"
                    "Items quoted: {{ items_count }}\n"
                    "Total quoted amount: {{ total_amount }}\n\n"
                    "We will revert with our evaluation outcome.\n\n"
                    "Regards,\nProcurement Team"
                ),
                required_variables_json=["rfq_reference", "items_count", "total_amount"],
                is_active=True,
            ),
            dict(
                template_code="PROCUREMENT_AWARD_NOTIFICATION",
                template_name="Procurement Award Notification",
                domain_scope=EmailTemplateDomainScope.PROCUREMENT,
                subject_template="Contract Awarded - {{ rfq_reference }}",
                body_text_template=(
                    "Dear Supplier,\n\n"
                    "We are pleased to inform you that your quotation for "
                    "RFQ {{ rfq_reference }} has been selected.\n\n"
                    "Awarded amount: {{ awarded_amount }}\n"
                    "Expected delivery: {{ delivery_date }}\n\n"
                    "A purchase order will be raised shortly.\n\n"
                    "Regards,\nProcurement Team"
                ),
                required_variables_json=["rfq_reference", "awarded_amount", "delivery_date"],
                is_active=True,
            ),
        ]

        for t in templates:
            obj, created = EmailTemplate.objects.get_or_create(
                tenant=tenant,
                template_code=t["template_code"],
                defaults={k: v for k, v in t.items() if k != "template_code"},
            )
            self.stdout.write(self.style.SUCCESS(f"  {'Created' if created else 'Exists'} template: {obj.template_code}"))

        # ------------------------------------------------------------------ #
        # 3. Email threads
        # ------------------------------------------------------------------ #
        now = timezone.now()

        ap_thread, _ = EmailThread.objects.get_or_create(
            tenant=tenant,
            provider_thread_id="demo-ap-thread-001",
            defaults=dict(
                mailbox=ap_mailbox,
                normalized_subject="Invoice INV-2026-0042 - HVAC Supply Services",
                status=EmailThreadStatus.OPEN,
                domain_context=EmailDomainContext.AP,
                link_status=EmailLinkStatus.UNLINKED,
                message_count=2,
                first_message_at=now - timezone.timedelta(hours=5),
                last_message_at=now - timezone.timedelta(hours=1),
            ),
        )

        proc_thread, _ = EmailThread.objects.get_or_create(
            tenant=tenant,
            provider_thread_id="demo-proc-thread-001",
            defaults=dict(
                mailbox=proc_mailbox,
                normalized_subject="Quotation Submission - RFQ-2026-0015 HVAC Equipment",
                status=EmailThreadStatus.OPEN,
                domain_context=EmailDomainContext.PROCUREMENT,
                link_status=EmailLinkStatus.UNLINKED,
                message_count=3,
                first_message_at=now - timezone.timedelta(days=2),
                last_message_at=now - timezone.timedelta(hours=3),
            ),
        )

        clarif_thread, _ = EmailThread.objects.get_or_create(
            tenant=tenant,
            provider_thread_id="demo-clarif-thread-001",
            defaults=dict(
                mailbox=proc_mailbox,
                normalized_subject="RE: Clarification Required - RFQ-2026-0015",
                status=EmailThreadStatus.OPEN,
                domain_context=EmailDomainContext.PROCUREMENT,
                link_status=EmailLinkStatus.UNLINKED,
                message_count=4,
                first_message_at=now - timezone.timedelta(days=1),
                last_message_at=now - timezone.timedelta(minutes=30),
            ),
        )

        self.stdout.write(self.style.SUCCESS(f"  Seeded 3 email threads"))

        # ------------------------------------------------------------------ #
        # 4. Email messages
        # ------------------------------------------------------------------ #
        def _msg(thread, mailbox, direction, subject, sender, sender_name,
                 body_text, classification, trust, intent, routing_status,
                 processing_status, hours_ago):
            msg, created = EmailMessage.objects.get_or_create(
                tenant=tenant,
                provider_message_id=f"demo-msg-{uuid.uuid4().hex[:8]}",
                defaults=dict(
                    thread=thread,
                    mailbox=mailbox,
                    direction=direction,
                    subject=subject,
                    from_email=sender,
                    from_name=sender_name,
                    body_text=body_text,
                    body_html=f"<p>{body_text.replace(chr(10), '<br>')}</p>",
                    message_classification=classification,
                    sender_trust_level=trust,
                    intent_type=intent,
                    routing_status=routing_status,
                    processing_status=processing_status,
                    received_at=now - timezone.timedelta(hours=hours_ago),
                    trace_id=uuid.uuid4().hex,
                ),
            )
            return msg, created

        # AP thread messages
        ap_msg1, c1 = _msg(
            ap_thread, ap_mailbox,
            EmailDirection.INBOUND,
            "Invoice INV-2026-0042 - HVAC Supply Services",
            "accounts@vendor-hvac.com", "HVAC Vendor Accounts",
            (
                "Dear AP Team,\n\n"
                "Please find attached Invoice INV-2026-0042 for HVAC supply services "
                "rendered per PO-2026-0088.\n\n"
                "Total Amount: SAR 45,000.00\n"
                "Payment Terms: Net 30\n\n"
                "Regards,\nHVAC Vendor"
            ),
            EmailMessageClassification.AP_INVOICE,
            SenderTrustLevel.TRUSTED_VENDOR,
            EmailIntentType.DOCUMENT_INGEST,
            EmailRoutingStatus.ROUTED,
            EmailProcessingStatus.PROCESSED,
            hours_ago=5,
        )

        ap_msg2, c2 = _msg(
            ap_thread, ap_mailbox,
            EmailDirection.OUTBOUND,
            "RE: Invoice INV-2026-0042 - Clarification Required",
            "ap-invoices@demo.example.com", "AP Team",
            (
                "Dear HVAC Vendor,\n\n"
                "We require clarification on Invoice INV-2026-0042:\n\n"
                "- Please confirm line item 3 quantity (8 units shown, PO shows 6)\n"
                "- Please provide delivery note reference\n\n"
                "Regards,\nAP Team"
            ),
            EmailMessageClassification.GENERAL_QUERY,
            SenderTrustLevel.TRUSTED_INTERNAL,
            EmailIntentType.NOTIFICATION,
            EmailRoutingStatus.ROUTED,
            EmailProcessingStatus.PROCESSED,
            hours_ago=3,
        )

        # Procurement quotation thread
        proc_msg1, c3 = _msg(
            proc_thread, proc_mailbox,
            EmailDirection.INBOUND,
            "Quotation Submission - RFQ-2026-0015 HVAC Equipment",
            "sales@hvac-global.com", "HVAC Global Sales",
            (
                "Dear Procurement Team,\n\n"
                "Please find our quotation for RFQ-2026-0015 (HVAC Equipment).\n\n"
                "Total Price: SAR 320,000.00\n"
                "Delivery Lead Time: 6 weeks\n"
                "Validity: 30 days\n"
                "Payment Terms: 30% advance, 70% on delivery\n\n"
                "Attachments: Technical proposal + commercial offer\n\n"
                "Regards,\nHVAC Global"
            ),
            EmailMessageClassification.PROCUREMENT_QUOTATION,
            SenderTrustLevel.KNOWN_EXTERNAL,
            EmailIntentType.DOCUMENT_INGEST,
            EmailRoutingStatus.ROUTED,
            EmailProcessingStatus.CLASSIFIED,
            hours_ago=48,
        )

        proc_msg2, c4 = _msg(
            proc_thread, proc_mailbox,
            EmailDirection.INBOUND,
            "RE: Quotation Submission - RFQ-2026-0015 - Revised Offer",
            "sales@hvac-global.com", "HVAC Global Sales",
            (
                "Dear Procurement Team,\n\n"
                "Please find our revised quotation for RFQ-2026-0015.\n\n"
                "Revised Total Price: SAR 298,000.00\n"
                "Delivery Lead Time: 5 weeks\n"
                "Validity: 30 days\n"
                "Payment Terms: Net 45\n\n"
                "Revised offer reflects updated pricing for units 1-5.\n\n"
                "Regards,\nHVAC Global"
            ),
            EmailMessageClassification.PROCUREMENT_QUOTATION,
            SenderTrustLevel.KNOWN_EXTERNAL,
            EmailIntentType.DOCUMENT_INGEST,
            EmailRoutingStatus.ROUTED,
            EmailProcessingStatus.CLASSIFIED,
            hours_ago=24,
        )

        # Clarification reply thread
        clarif_out, c5 = _msg(
            clarif_thread, proc_mailbox,
            EmailDirection.OUTBOUND,
            "Clarification Required - RFQ-2026-0015",
            "procurement@demo.example.com", "Procurement Team",
            (
                "Dear HVAC Global,\n\n"
                "We require clarification on the following:\n\n"
                "- Confirm warranty period for compressor units\n"
                "- Confirm spare parts availability for 5 years\n"
                "- Provide commissioning cost breakdown\n\n"
                "Regards,\nProcurement Team"
            ),
            EmailMessageClassification.GENERAL_QUERY,
            SenderTrustLevel.TRUSTED_INTERNAL,
            EmailIntentType.NOTIFICATION,
            EmailRoutingStatus.ROUTED,
            EmailProcessingStatus.PROCESSED,
            hours_ago=20,
        )

        clarif_reply1, c6 = _msg(
            clarif_thread, proc_mailbox,
            EmailDirection.INBOUND,
            "RE: Clarification Required - RFQ-2026-0015",
            "sales@hvac-global.com", "HVAC Global Sales",
            (
                "Dear Procurement Team,\n\n"
                "Please find our responses:\n\n"
                "- Warranty: 2 years full coverage on all compressor units\n"
                "- Spare parts: Stocked locally for minimum 5 years\n"
                "- Commissioning: SAR 8,500 for full site commissioning\n\n"
                "Regards,\nHVAC Global"
            ),
            EmailMessageClassification.PROCUREMENT_CLARIFICATION,
            SenderTrustLevel.KNOWN_EXTERNAL,
            EmailIntentType.CLARIFICATION_RESPONSE,
            EmailRoutingStatus.ROUTED,
            EmailProcessingStatus.CLASSIFIED,
            hours_ago=18,
        )

        approval_email, c7 = _msg(
            ap_thread, ap_mailbox,
            EmailDirection.INBOUND,
            "RE: Invoice INV-2026-0042 - Approval Confirmation",
            "finance.director@demo.example.com", "Finance Director",
            (
                "AP Team,\n\n"
                "Approved. Please proceed with payment for Invoice INV-2026-0042.\n\n"
                "Regards,\nFinance Director"
            ),
            EmailMessageClassification.APPROVAL_RESPONSE,
            SenderTrustLevel.TRUSTED_INTERNAL,
            EmailIntentType.APPROVAL_ACTION,
            EmailRoutingStatus.ROUTED,
            EmailProcessingStatus.CLASSIFIED,
            hours_ago=1,
        )

        msg_count = sum(1 for c in [c1, c2, c3, c4, c5, c6, c7] if c)
        self.stdout.write(self.style.SUCCESS(f"  Created {msg_count} new email messages (7 total seeded)"))

        # ------------------------------------------------------------------ #
        # 5. Participants
        # ------------------------------------------------------------------ #
        participant_data = [
            (ap_msg1, "accounts@vendor-hvac.com", "HVAC Vendor Accounts", EmailParticipantRoleType.VENDOR, SenderTrustLevel.TRUSTED_VENDOR),
            (proc_msg1, "sales@hvac-global.com", "HVAC Global Sales", EmailParticipantRoleType.SUPPLIER, SenderTrustLevel.KNOWN_EXTERNAL),
            (proc_msg2, "sales@hvac-global.com", "HVAC Global Sales", EmailParticipantRoleType.SUPPLIER, SenderTrustLevel.KNOWN_EXTERNAL),
            (clarif_reply1, "sales@hvac-global.com", "HVAC Global Sales", EmailParticipantRoleType.SUPPLIER, SenderTrustLevel.KNOWN_EXTERNAL),
            (approval_email, "finance.director@demo.example.com", "Finance Director", EmailParticipantRoleType.APPROVER, SenderTrustLevel.TRUSTED_INTERNAL),
        ]
        p_created = 0
        for msg, email, name, role, trust in participant_data:
            _, created = EmailParticipant.objects.get_or_create(
                tenant=tenant,
                thread=msg.thread,
                email=email,
                defaults=dict(
                    display_name=name,
                    role_type=role,
                    trust_level=trust,
                ),
            )
            if created:
                p_created += 1
        self.stdout.write(self.style.SUCCESS(f"  Created {p_created} email participants"))

        # ------------------------------------------------------------------ #
        # 6. Routing decisions
        # ------------------------------------------------------------------ #
        routing_data = [
            (ap_msg1, TargetDomain.AP, "AP_INVOICE", 0.95, True, "attachment_type_routing"),
            (proc_msg1, TargetDomain.PROCUREMENT, "PROCUREMENT_QUOTATION", 0.90, True, "mailbox_default_routing"),
            (proc_msg2, TargetDomain.PROCUREMENT, "PROCUREMENT_QUOTATION", 0.88, True, "thread_continuation_routing"),
            (clarif_reply1, TargetDomain.PROCUREMENT, "PROCUREMENT_CLARIFICATION", 0.85, True, "thread_continuation_routing"),
            (approval_email, TargetDomain.AP, "APPROVAL_RESPONSE", 0.92, True, "subject_body_entity_routing"),
        ]
        r_created = 0
        for msg, domain, entity_type, confidence, is_deterministic, rule_name in routing_data:
            _, created = EmailRoutingDecision.objects.get_or_create(
                tenant=tenant,
                email_message=msg,
                defaults=dict(
                    target_domain=domain,
                    target_entity_type=entity_type,
                    confidence_score=confidence,
                    deterministic_flag=is_deterministic,
                    rule_name=rule_name,
                    rule_version="v1",
                    llm_used=False,
                    final_status=EmailRoutingDecisionStatus.APPLIED,
                    reasoning_summary=f"Routed to {domain} based on {rule_name} (confidence={confidence})",
                    decision_type=EmailRoutingDecisionType.RULE_BASED,
                ),
            )
            if created:
                r_created += 1
        self.stdout.write(self.style.SUCCESS(f"  Created {r_created} routing decisions"))

        # ------------------------------------------------------------------ #
        # 7. Actions
        # ------------------------------------------------------------------ #
        action_data = [
            (ap_msg1, EmailActionType.CREATE_DOCUMENT_UPLOAD, EmailActionStatus.COMPLETED, "AP_INVOICE"),
            (proc_msg1, EmailActionType.CREATE_SUPPLIER_QUOTATION, EmailActionStatus.COMPLETED, "SUPPLIER_QUOTATION"),
            (proc_msg2, EmailActionType.LINK_TO_SUPPLIER_QUOTATION, EmailActionStatus.COMPLETED, "SUPPLIER_QUOTATION"),
            (clarif_reply1, EmailActionType.LINK_TO_PROCUREMENT_REQUEST, EmailActionStatus.COMPLETED, "PROCUREMENT_REQUEST"),
            (ap_msg2, EmailActionType.SEND_OUTBOUND_EMAIL, EmailActionStatus.COMPLETED, "OUTBOUND"),
            (approval_email, EmailActionType.LINK_TO_AP_CASE, EmailActionStatus.COMPLETED, "AP_CASE"),
        ]
        a_created = 0
        for msg, action_type, status, entity_type in action_data:
            _, created = EmailAction.objects.get_or_create(
                tenant=tenant,
                email_message=msg,
                action_type=action_type,
                defaults=dict(
                    thread=msg.thread,
                    action_status=status,
                    target_entity_type=entity_type,
                    trace_id=msg.trace_id or uuid.uuid4().hex,
                    payload_json={"seeded": True, "sender": msg.from_email},
                    result_json={"demo": True, "action_type": action_type},
                ),
            )
            if created:
                a_created += 1
        self.stdout.write(self.style.SUCCESS(f"  Created {a_created} email actions"))

        # ------------------------------------------------------------------ #
        # Summary
        # ------------------------------------------------------------------ #
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(self.style.SUCCESS("Email integration seed completed successfully!"))
        self.stdout.write(self.style.SUCCESS("=" * 60))
        self.stdout.write(f"  Mailboxes  : {MailboxConfig.objects.filter(tenant=tenant).count()}")
        self.stdout.write(f"  Templates  : {EmailTemplate.objects.filter(tenant=tenant).count()}")
        self.stdout.write(f"  Threads    : {EmailThread.objects.filter(tenant=tenant).count()}")
        self.stdout.write(f"  Messages   : {EmailMessage.objects.filter(tenant=tenant).count()}")
        self.stdout.write(f"  Routing    : {EmailRoutingDecision.objects.filter(tenant=tenant).count()}")
        self.stdout.write(f"  Actions    : {EmailAction.objects.filter(tenant=tenant).count()}")
        self.stdout.write("")
        self.stdout.write("Next steps:")
        self.stdout.write("  1. Update .env with EMAIL_WEBHOOK_SHARED_SECRET=<secret>")
        self.stdout.write("  2. Update MailboxConfig.config_json with real Microsoft Graph credentials")
        self.stdout.write("  3. Visit /email/ to see the dashboard")
        self.stdout.write("  4. Test webhook: POST /email/webhook/<mailbox_id>/")
