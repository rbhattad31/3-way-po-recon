from django.apps import AppConfig


class EmailIntegrationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.email_integration"
    verbose_name = "Email Integration"

    def ready(self):
        from . import signals  # noqa: F401
