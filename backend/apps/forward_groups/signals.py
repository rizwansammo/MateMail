from django.db.models.signals import post_delete
from django.dispatch import receiver

from apps.mail_directory.models import AddressKind
from apps.mail_directory.services import release_address

from .models import ForwardGroup


@receiver(post_delete, sender=ForwardGroup)
def release_forward_group_address(sender, instance, **kwargs):
    release_address(instance.address, expected_kind=AddressKind.FORWARD_GROUP)
