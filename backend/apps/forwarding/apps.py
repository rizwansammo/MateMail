from django.apps import AppConfig

class ForwardingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.forwarding"
    label = "forwarding"
