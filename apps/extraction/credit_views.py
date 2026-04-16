"""Credit management views — admin-facing screens for allocating/adjusting credits."""
from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.decorators import observed_action
from apps.core.permissions import permission_required_code
from apps.extraction.credit_models import CreditTransaction, UserCreditAccount
from apps.extraction.forms import CreditAdjustmentForm
from apps.extraction.services.credit_service import CreditService

logger = logging.getLogger(__name__)
User = get_user_model()


@login_required
@permission_required_code("credits.view")
@observed_action("credits.view_list", permission="credits.view", entity_type="UserCreditAccount")
def credit_account_list(request):
    """List all user credit accounts with search and pagination."""
    qs = (
        UserCreditAccount.objects
        .select_related("user")
        .order_by("-updated_at")
    )
    tenant = getattr(request, "tenant", None)
    if tenant and not getattr(request.user, "is_platform_admin", False):
        qs = qs.filter(user__company=tenant)

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(user__email__icontains=q)
            | Q(user__first_name__icontains=q)
            | Q(user__last_name__icontains=q)
        )

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "extraction/credit_account_list.html", {
        "accounts": page_obj,
        "page_obj": page_obj,
        "q": q,
    })


@login_required
@permission_required_code("credits.manage")
@observed_action("credits.view_detail", permission="credits.manage", entity_type="UserCreditAccount")
def credit_account_detail(request, user_id):
    """Detail page for a user's credit account with ledger history."""
    user_qs = User.objects.all()
    tenant = getattr(request, "tenant", None)
    if tenant and not getattr(request.user, "is_platform_admin", False):
        user_qs = user_qs.filter(company=tenant)
    target_user = get_object_or_404(user_qs, pk=user_id)
    account = CreditService.get_or_create_account(target_user)
    CreditService.reset_monthly_if_due(account)
    account.refresh_from_db()

    transactions = (
        CreditTransaction.objects
        .filter(account=account)
        .select_related("created_by")
        .order_by("-created_at")[:50]
    )

    summary = CreditService.get_usage_summary(target_user)
    form = CreditAdjustmentForm()

    return render(request, "extraction/credit_account_detail.html", {
        "target_user": target_user,
        "account": account,
        "summary": summary,
        "transactions": transactions,
        "form": form,
    })


@login_required
@permission_required_code("credits.manage")
@observed_action("credits.adjust", permission="credits.manage", entity_type="UserCreditAccount", audit_event="CREDIT_ALLOCATION_UPDATED")
def credit_account_adjust(request, user_id):
    """Process credit adjustment form submission."""
    if request.method != "POST":
        return redirect("extraction:credit_account_detail", user_id=user_id)

    user_qs = User.objects.all()
    tenant = getattr(request, "tenant", None)
    if tenant and not getattr(request.user, "is_platform_admin", False):
        user_qs = user_qs.filter(company=tenant)
    target_user = get_object_or_404(user_qs, pk=user_id)
    account = CreditService.get_or_create_account(target_user)
    form = CreditAdjustmentForm(request.POST)

    if not form.is_valid():
        for err in form.errors.values():
            messages.error(request, err[0])
        return redirect("extraction:credit_account_detail", user_id=user_id)

    action = form.cleaned_data["action_type"]
    credits = form.cleaned_data.get("credits") or 0
    monthly_limit = form.cleaned_data.get("monthly_limit")
    is_active = form.cleaned_data.get("is_active", True)
    remarks = form.cleaned_data["remarks"].strip()

    try:
        if action == "add":
            CreditService.allocate(target_user, credits, actor=request.user, remarks=remarks)
            messages.success(request, f"Added {credits} credits to {target_user.email}.")

        elif action == "subtract":
            CreditService.adjust(target_user, -credits, actor=request.user, remarks=remarks)
            messages.success(request, f"Subtracted {credits} credits from {target_user.email}.")

        elif action == "set_limit":
            with transaction.atomic():
                acct = UserCreditAccount.objects.select_for_update().get(user=target_user)
                old_limit = acct.monthly_limit
                acct.monthly_limit = monthly_limit
                acct.save(update_fields=["monthly_limit", "updated_at"])

                CreditTransaction.objects.create(
                    account=acct,
                    transaction_type="ADJUST",
                    credits=0,
                    balance_after=acct.balance_credits,
                    reserved_after=acct.reserved_credits,
                    monthly_used_after=acct.monthly_used,
                    reference_type="admin",
                    remarks=f"Monthly limit changed {old_limit} → {monthly_limit}. {remarks}",
                    created_by=request.user,
                )
            messages.success(request, f"Monthly limit set to {monthly_limit} for {target_user.email}.")

        elif action == "toggle_active":
            with transaction.atomic():
                acct = UserCreditAccount.objects.select_for_update().get(user=target_user)
                acct.is_active = is_active
                acct.save(update_fields=["is_active", "updated_at"])

                CreditTransaction.objects.create(
                    account=acct,
                    transaction_type="ADJUST",
                    credits=0,
                    balance_after=acct.balance_credits,
                    reserved_after=acct.reserved_credits,
                    monthly_used_after=acct.monthly_used,
                    reference_type="admin",
                    remarks=f"Account {'activated' if is_active else 'deactivated'}. {remarks}",
                    created_by=request.user,
                )
            status_word = "activated" if is_active else "deactivated"
            messages.success(request, f"Account {status_word} for {target_user.email}.")

    except ValueError as exc:
        messages.error(request, str(exc))

    return redirect("extraction:credit_account_detail", user_id=user_id)
