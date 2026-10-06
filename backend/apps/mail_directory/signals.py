from django.db.models.signals import post_delete
from django.dispatch import receiver

from apps.aliases.models import Alias
from apps.mailboxes.models import Mailbox, MailboxKind

from .models import AddressKind
from .services import release_address


@receiver(post_delete, sender=Mailbox)
def release_mailbox_address(sender, instance, **kwargs):
    kind = (
        AddressKind.TEAM_BOX
        if instance.kind == MailboxKind.TEAM_BOX
        else AddressKind.MAILBOX
    )
    release_address(instance.email, expected_kind=kind)


@receiver(post_delete, sender=Alias)
def release_alias_address(sender, instance, **kwargs):
    release_address(instance.source_address, expected_kind=AddressKind.ALIAS)
