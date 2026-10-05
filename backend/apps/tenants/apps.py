from django.apps import AppConfig


class TenantsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.tenants"
    label = "tenants"

    def ready(self):
        # Register deployment/security checks without importing models early.
        from . import checks  # noqa: F401
