"""Event-driven onboarding completion for domain and mailbox transitions.

The GET endpoint remains a read-repair path for code using QuerySet.update(),
which deliberately bypasses Django's model signals.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .onboarding import try_record_onboarding_completion


@receiver(post_save, sender="domains.Domain", dispatch_uid="matemail_onboarding_domain_saved")
def on_domain_saved(sender, instance, **kwargs):
    if instance.tenant_id:
        try_record_onboarding_completion(instance.tenant_id)


@receiver(post_save, sender="mailboxes.Mailbox", dispatch_uid="matemail_onboarding_mailbox_saved")
def on_mailbox_saved(sender, instance, **kwargs):
    if instance.tenant_id:
        try_record_onboarding_completion(instance.tenant_id)
