from django.apps import AppConfig


class ForwardGroupsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.forward_groups"
    label = "forward_groups"

    def ready(self):
        from . import signals  # noqa: F401
