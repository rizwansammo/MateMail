from rest_framework import serializers

from .models import ForwardingRule


class ForwardingRuleSerializer(serializers.ModelSerializer):
    mail_service_ready = serializers.BooleanField(source="mail_engine_provisioned", read_only=True)
    source_mailbox_email = serializers.EmailField(source="source_mailbox.email", read_only=True)

    class Meta:
        model = ForwardingRule
        fields = [
            "id", "source_mailbox", "source_mailbox_email",
            "destination_email", "keep_copy", "status",
            "mail_service_ready", "created_at",
        ]


class ForwardingRuleCreateSerializer(serializers.Serializer):
    source_mailbox_id = serializers.UUIDField()
    destination_email = serializers.EmailField()
    keep_copy = serializers.BooleanField(default=True)
