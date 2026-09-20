from rest_framework import serializers

from .models import (
    ALLOWED_PERMISSIONS,
    PURPOSE_LABELS,
    PURPOSE_PERMISSIONS,
    normalise_permissions,
)


class IntegrationCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    purpose = serializers.ChoiceField(
        choices=tuple((key, label) for key, label in PURPOSE_LABELS.items()),
        default="custom",
    )
    mailbox_id = serializers.UUIDField()
    permissions = serializers.ListField(
        child=serializers.CharField(),
        required=False,
    )

    def validate(self, attrs):
        purpose = attrs.get("purpose", "custom")
        if "permissions" not in attrs:
            attrs["permissions"] = list(PURPOSE_PERMISSIONS[purpose])
        return attrs

    def validate_permissions(self, values):
        invalid = sorted(set(values) - ALLOWED_PERMISSIONS)
        if invalid:
            raise serializers.ValidationError(
                "Unknown permission: " + ", ".join(invalid)
            )
        cleaned = normalise_permissions(values)
        if not cleaned:
            raise serializers.ValidationError(
                "Choose at least one permission for this connected app."
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
