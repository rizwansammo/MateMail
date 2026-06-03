from rest_framework import serializers

from .models import Alias


class AliasSerializer(serializers.ModelSerializer):
    domain_name = serializers.CharField(source="domain.domain", read_only=True)
    destination_email = serializers.SerializerMethodField()

    class Meta:
        model = Alias
        fields = [
            "id", "source_address", "domain", "domain_name",
            "destination_mailbox", "destination_address", "destination_email",
            "status", "mail_engine_provisioned", "created_at",
        ]

    def get_destination_email(self, obj):
        if obj.destination_mailbox:
            return obj.destination_mailbox.email
        return obj.destination_address


class AliasCreateSerializer(serializers.Serializer):
    source_local_part = serializers.CharField(max_length=64)
    domain_id = serializers.UUIDField()
    destination_mailbox_id = serializers.UUIDField(required=False, allow_null=True)
    destination_address = serializers.EmailField(required=False, allow_blank=True, default="")

    def validate(self, data):
        has_mailbox = bool(data.get("destination_mailbox_id"))
        has_address = bool((data.get("destination_address") or "").strip())
        if not has_mailbox and not has_address:
            raise serializers.ValidationError(
                "Specify either destination_mailbox_id or destination_address."
            )
        if has_mailbox and has_address:
            raise serializers.ValidationError(
                "Specify only one of destination_mailbox_id or destination_address."
            )
        return data
