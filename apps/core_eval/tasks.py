"""Celery tasks for the core_eval app."""
from celery import shared_task
from django.db import models
from django.utils import timezone

from apps.core_eval.models import LearningAction
from apps.core_eval.services.learning_action_service import LearningActionService


@shared_task(name="core_eval.process_approved_learning_actions")
def process_approved_learning_actions():
    """Pick up APPROVED LearningActions and execute them."""
    actions = list(
        LearningAction.objects.filter(
            status="APPROVED",
        ).filter(
            models.Q(next_retry_at__isnull=True)
            | models.Q(next_retry_at__lte=timezone.now())
        ).select_for_update(skip_locked=True)[:50]
    )

    for action in actions:
        try:
            LearningActionService.mark_applied(action)
        except Exception as exc:
            LearningActionService.record_execution_attempt(
                action, log_entry="executor error", error=str(exc),
            )
