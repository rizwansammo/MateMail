from rest_framework import serializers

from .models import Mailbox


class MailboxSerializer(serializers.ModelSerializer):
    domain_name = serializers.CharField(source="domain.domain", read_only=True)

    class Meta:
        model = Mailbox
        fields = [
            "id", "email", "full_name", "local_part",
            "domain", "domain_name", "status",
            "quota_mb", "storage_used_mb",
            "mail_engine_provisioned", "mail_engine_error",
            "last_login", "created_at",
        ]
        read_only_fields = [
            "id", "email", "status", "storage_used_mb",
            "mail_engine_provisioned", "mail_engine_error",
            "last_login", "created_at",
        ]


class MailboxCreateSerializer(serializers.Serializer):
    local_part = serializers.CharField(max_length=64)
    domain_id = serializers.UUIDField()
    full_name = serializers.CharField(max_length=255)
    quota_mb = serializers.IntegerField(default=10240, min_value=100)
    password = serializers.CharField(min_length=10, write_only=True)

    def validate_local_part(self, value):
        return value.lower().strip()


class MailboxReProvisionSerializer(serializers.Serializer):
    password = serializers.CharField(min_length=10, write_only=True)


class MailboxStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["active", "disabled"])
