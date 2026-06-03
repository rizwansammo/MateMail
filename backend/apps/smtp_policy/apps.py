from django.apps import AppConfig


class SmtpPolicyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.smtp_policy"
    verbose_name = "SMTP Policy"
