from django.apps import AppConfig


class MailDirectoryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.mail_directory"
    label = "mail_directory"

    def ready(self):
        from . import signals  # noqa: F401
