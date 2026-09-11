from django.apps import AppConfig


class MailEngineConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.mail_engine"
    label = "mail_engine"

    def ready(self):
        # Registers the deploy checks that refuse a real-engine configuration
        # missing its URL or API key, or using plain HTTP. See checks.py.
        from . import checks  # noqa: F401
