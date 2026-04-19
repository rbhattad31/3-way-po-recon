# Email Integration Flow Chart

```mermaid
flowchart TD
    A[External Mail Provider\nMicrosoft 365 or Gmail] --> B[Webhook Event or Polling Trigger]
    B --> C[Provider Adapter\nsubscribe/poll/get_message/get_attachments]
    C --> D[InboundIngestionService\nNormalize payload]

    D --> E[ThreadLinkingService\nFind or create EmailThread]
    E --> F[Create EmailMessage\nDirection INBOUND]
    F --> G{Sender Allowed?\nEmailPolicyService}

    G -- No --> H[Mark IGNORED]
    H --> Z[EmailAction + AuditEvent + ProcessingLog]

    G -- Yes --> I{Attachments Present?}
    I -- Yes --> J[AttachmentService\nStore files + hash + scan metadata]
    J --> K[Optional DocumentUpload Link]
    I -- No --> L[TriageService]
    K --> L

    L --> M[ClassificationService\nclassify + infer_intent + trust]
    M --> N[EntityLinkingService\nresolve AP_CASE / PROCUREMENT_REQUEST / SUPPLIER_QUOTATION]
    N --> O[RoutingService\ncreate EmailRoutingDecision]

    O --> P{Target Domain}

    P -- AP --> Q[APEmailHandler\nDomain execution]
    Q --> Q1[Create or link DocumentUpload / AP entity]
    Q1 --> Q2[Trigger extraction or AP downstream rerun]

    P -- PROCUREMENT --> R[ProcurementEmailHandler\nDomain execution]
    R --> R1[Create or link SupplierQuotation / ProcurementRequest]
    R1 --> R2[Trigger prefill/validation/analysis rerun]

    P -- TRIAGE or NOTIFICATION_ONLY --> S[NotificationEmailHandler\nNo domain mutation or triage queue]

    Q2 --> T[Update EmailMessage\nROUTED and PROCESSED]
    R2 --> T
    S --> T

    T --> Z[EmailAction + AuditEvent + ProcessingLog]

    U[UI/API/Celery Outbound Request] --> V[OutboundEmailService\nRender template or custom payload]
    V --> W[Provider Adapter send_message]
    W --> X[Create OUTBOUND EmailMessage]
    X --> Y[Record SEND action]
    Y --> Z

    AA[Recovery Tasks\nretry_failed_actions / relink_threads] --> AB[EmailProcessingService]
    AB --> O
```

## Notes
- Shared channel stops at normalization, triage, linking, routing, and governed actions.
- Business execution remains domain-specific (AP vs Procurement).
- Every mutation path records action and audit metadata.
