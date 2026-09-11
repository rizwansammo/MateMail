from django.apps import AppConfig


class DomainsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.domains"
    label = "domains"

    def ready(self):
        # Registers the deploy check that refuses a production configuration
        # without DKIM_ENCRYPTION_KEY. See apps/domains/checks.py.
        from . import checks  # noqa: F401
