from rest_framework import serializers

from .models import Mailbox


class MailboxSerializer(serializers.ModelSerializer):
    domain_name = serializers.CharField(source="domain.domain", read_only=True)
    # See the note in domains/serializers.py — customer-facing naming.
    mail_service_ready = serializers.BooleanField(source="mail_engine_provisioned", read_only=True)
    mail_service_message = serializers.CharField(source="mail_engine_error", read_only=True)

    class Meta:
        model = Mailbox
        fields = [
            "id", "email", "full_name", "local_part", "kind",
            "domain", "domain_name", "status",
            "quota_mb", "storage_used_mb",
            "mail_service_ready", "mail_service_message",
            "last_login", "created_at",
        ]
        read_only_fields = [
            "id", "email", "kind", "status", "storage_used_mb",
            "mail_service_ready", "mail_service_message",
            "last_login", "created_at",
        ]


class MailboxCreateSerializer(serializers.Serializer):
    local_part = serializers.CharField(max_length=64)
    domain_id = serializers.UUIDField()
    full_name = serializers.CharField(max_length=255)
    # No default and not required: an omitted value means "use the plan's
    # default", resolved server-side by billing.utils.resolve_mailbox_quota.
    # A default here would be a storage policy living in a serializer, which is
    # how a 1 GB trial workspace ended up able to request 10 GB mailboxes.
    quota_mb = serializers.IntegerField(required=False, allow_null=True, min_value=1)
    password = serializers.CharField(min_length=10, write_only=True)

    def validate_local_part(self, value):
        return value.lower().strip()


class MailboxReProvisionSerializer(serializers.Serializer):
    password = serializers.CharField(min_length=10, write_only=True)


class MailboxStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["active", "disabled"])
