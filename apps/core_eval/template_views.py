"""Eval & Learning template views -- browsable UI for eval runs, learning signals, and actions."""
import json

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, render

from apps.core.permissions import permission_required_code
from apps.core_eval.models import EvalRun, EvalFieldOutcome, EvalMetric, LearningAction, LearningSignal

_VIEW_PERM = "eval.view"


# ---------------------------------------------------------------------------
# Eval Runs
# ---------------------------------------------------------------------------
@login_required
@permission_required_code(_VIEW_PERM)
def eval_run_list(request):
    """Browsable list of evaluation runs with filtering."""
    qs = EvalRun.objects.order_by("-created_at")

    # Filters
    app_module = request.GET.get("app_module", "").strip()
    status = request.GET.get("status", "").strip()
    entity_type = request.GET.get("entity_type", "").strip()

    if app_module:
        qs = qs.filter(app_module=app_module)
    if status:
        qs = qs.filter(status=status)
    if entity_type:
        qs = qs.filter(entity_type=entity_type)

    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Distinct values for filter dropdowns
    app_modules = (
        EvalRun.objects.order_by("app_module")
        .values_list("app_module", flat=True)
        .distinct()
    )
    statuses = (
        EvalRun.objects.order_by("status")
        .values_list("status", flat=True)
        .distinct()
    )
    entity_types = (
        EvalRun.objects.order_by("entity_type")
        .values_list("entity_type", flat=True)
        .distinct()
    )

    # KPI counts
    total = EvalRun.objects.count()
    completed = EvalRun.objects.filter(status=EvalRun.Status.COMPLETED).count()
    failed = EvalRun.objects.filter(status=EvalRun.Status.FAILED).count()

    return render(request, "core_eval/eval_run_list.html", {
        "page_obj": page_obj,
        "app_modules": app_modules,
        "statuses": statuses,
        "entity_types": entity_types,
        "current_app_module": app_module,
        "current_status": status,
        "current_entity_type": entity_type,
        "total": total,
        "completed": completed,
        "failed": failed,
    })


@login_required
@permission_required_code(_VIEW_PERM)
def eval_run_detail(request, pk):
    """Detail view for a single EvalRun with its metrics and field outcomes."""
    run = get_object_or_404(EvalRun, pk=pk)
    metrics = run.metrics.order_by("metric_name")
    field_outcomes = run.field_outcomes.order_by("field_name")
    signals = run.learning_signals.select_related("actor").order_by("-created_at")

    def _fmt_json(d):
        if d:
            return json.dumps(d, indent=2, default=str)
        return ""

    # KPI: field outcome status counts
    correct_count = sum(1 for fo in field_outcomes if fo.status == "CORRECT")
    incorrect_count = sum(1 for fo in field_outcomes if fo.status == "INCORRECT")
    missing_count = sum(1 for fo in field_outcomes if fo.status == "MISSING")

    # KPI: extraction confidence from metrics
    run_confidence = None
    for m in metrics:
        if m.metric_name == "extraction_confidence" and m.metric_value is not None:
            run_confidence = m.metric_value
            break

    # Pre-format JSON values on metrics for template rendering
    for m in metrics:
        m.json_value_pretty = _fmt_json(m.json_value) if m.json_value else ""

    # Pre-format payload_json on signals for template rendering
    for sig in signals:
        sig.payload_pretty = _fmt_json(sig.payload_json) if sig.payload_json else ""

    return render(request, "core_eval/eval_run_detail.html", {
        "run": run,
        "metrics": metrics,
        "field_outcomes": field_outcomes,
        "signals": signals,
        "input_snapshot_pretty": _fmt_json(run.input_snapshot_json),
        "result_pretty": _fmt_json(run.result_json),
        "config_pretty": _fmt_json(run.config_json),
        "error_pretty": _fmt_json(run.error_json),
        "correct_count": correct_count,
        "incorrect_count": incorrect_count,
        "missing_count": missing_count,
        "run_confidence": run_confidence,
    })


# ---------------------------------------------------------------------------
# Learning Signals
# ---------------------------------------------------------------------------
@login_required
@permission_required_code(_VIEW_PERM)
def learning_signal_list(request):
    """Browsable list of learning signals with filtering."""
    qs = LearningSignal.objects.select_related("actor").order_by("-created_at")

    # Filters
    app_module = request.GET.get("app_module", "").strip()
    signal_type = request.GET.get("signal_type", "").strip()
    field_name = request.GET.get("field_name", "").strip()

    if app_module:
        qs = qs.filter(app_module=app_module)
    if signal_type:
        qs = qs.filter(signal_type=signal_type)
    if field_name:
        qs = qs.filter(field_name=field_name)

    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Distinct values for filter dropdowns
    app_modules = (
        LearningSignal.objects.order_by("app_module")
        .values_list("app_module", flat=True)
        .distinct()
    )
    signal_types = (
        LearningSignal.objects.order_by("signal_type")
        .values_list("signal_type", flat=True)
        .distinct()
    )
    field_names = (
        LearningSignal.objects.exclude(field_name="")
        .order_by("field_name")
        .values_list("field_name", flat=True)
        .distinct()
    )

    return render(request, "core_eval/learning_signal_list.html", {
        "page_obj": page_obj,
        "app_modules": app_modules,
        "signal_types": signal_types,
        "field_names": field_names,
        "current_app_module": app_module,
        "current_signal_type": signal_type,
        "current_field_name": field_name,
    })


@login_required
@permission_required_code(_VIEW_PERM)
def learning_signal_detail(request, pk):
    """Detail view for a single LearningSignal."""
    signal = get_object_or_404(
        LearningSignal.objects.select_related("actor", "eval_run"),
        pk=pk,
    )
    payload_items = []
    payload_pretty = ""
    if signal.payload_json and isinstance(signal.payload_json, dict):
        payload_items = list(signal.payload_json.items())
        payload_pretty = json.dumps(signal.payload_json, indent=2, default=str)
    return render(request, "core_eval/learning_signal_detail.html", {
        "signal": signal,
        "payload_items": payload_items,
        "payload_pretty": payload_pretty,
    })


# ---------------------------------------------------------------------------
# Learning Actions
# ---------------------------------------------------------------------------
@login_required
@permission_required_code(_VIEW_PERM)
def learning_action_list(request):
    """Browsable list of learning actions with filtering."""
    qs = LearningAction.objects.select_related("proposed_by", "approved_by").order_by("-created_at")

    # Filters
    action_type = request.GET.get("action_type", "").strip()
    status = request.GET.get("status", "").strip()
    app_module = request.GET.get("app_module", "").strip()

    if action_type:
        qs = qs.filter(action_type=action_type)
    if status:
        qs = qs.filter(status=status)
    if app_module:
        qs = qs.filter(app_module=app_module)

    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Distinct values for filter dropdowns
    action_types = (
        LearningAction.objects.order_by("action_type")
        .values_list("action_type", flat=True)
        .distinct()
    )
    statuses = (
        LearningAction.objects.order_by("status")
        .values_list("status", flat=True)
        .distinct()
    )
    app_modules = (
        LearningAction.objects.exclude(app_module="")
        .order_by("app_module")
        .values_list("app_module", flat=True)
        .distinct()
    )

    # KPI counts
    proposed = LearningAction.objects.filter(status=LearningAction.Status.PROPOSED).count()
    approved = LearningAction.objects.filter(status=LearningAction.Status.APPROVED).count()
    applied = LearningAction.objects.filter(status=LearningAction.Status.APPLIED).count()
    rejected = LearningAction.objects.filter(status=LearningAction.Status.REJECTED).count()

    return render(request, "core_eval/learning_action_list.html", {
        "page_obj": page_obj,
        "action_types": action_types,
        "statuses": statuses,
        "app_modules": app_modules,
        "current_action_type": action_type,
        "current_status": status,
        "current_app_module": app_module,
        "proposed": proposed,
        "approved": approved,
        "applied": applied,
        "rejected": rejected,
    })


@login_required
@permission_required_code(_VIEW_PERM)
def learning_action_detail(request, pk):
    """Detail view for a single LearningAction."""
    action = get_object_or_404(
        LearningAction.objects.select_related("proposed_by", "approved_by"),
        pk=pk,
    )
    return render(request, "core_eval/learning_action_detail.html", {
        "action": action,
    })
