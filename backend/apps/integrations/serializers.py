from rest_framework import serializers

from .models import ALLOWED_PERMISSIONS, normalise_permissions


class IntegrationCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, default="NetaMate SalesHub")
    mailbox_id = serializers.UUIDField()
    permissions = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=lambda: ["send_email", "use_signatures"],
    )

    def validate_permissions(self, values):
        invalid = sorted(set(values) - ALLOWED_PERMISSIONS)
        if invalid:
            raise serializers.ValidationError(
                "Unknown permission: " + ", ".join(invalid)
            )
        cleaned = normalise_permissions(values)
        if "send_email" not in cleaned:
            raise serializers.ValidationError(
                "SalesHub connections must be allowed to send email."
            )
        return cleaned


class ConnectStartSerializer(serializers.Serializer):
    tenant_id = serializers.UUIDField()
    integration_secret = serializers.CharField(max_length=256)


class ConnectStatusSerializer(serializers.Serializer):
    tenant_id = serializers.UUIDField()
    poll_token = serializers.CharField(max_length=256)


class AuthorizationSerializer(serializers.Serializer):
    password = serializers.CharField(trim_whitespace=False)
    two_factor_code = serializers.CharField(
        required=False, allow_blank=True, default=""
    )
    mailbox_factor_code = serializers.CharField(
        required=False, allow_blank=True, default=""
    )
