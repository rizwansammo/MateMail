from django.apps import AppConfig


class PostBoxConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.postbox"
    label = "postbox"
    verbose_name = "MateMail PostBox"
