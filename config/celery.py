"""Celery configuration for PO Reconciliation project."""
import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("po_recon")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.conf.task_default_queue = "default"
app.autodiscover_tasks()

app.conf.beat_schedule = {
    "poll-mailboxes": {
        "task": "email_integration.poll_mailboxes_task",
        "schedule": crontab(minute="*"),  # Run every 1 minute
    },
    "process-approved-learning-actions": {
        "task": "core_eval.process_approved_learning_actions",
        "schedule": crontab(minute="*/30"),
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
