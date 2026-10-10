from rest_framework import serializers

from apps.security.scopes import ALL_SCOPES, APIKeyScope, DEFAULT_SCOPES
from apps.security.scopes import normalise as normalise_scopes
from .models import APIKey, TeamInvite


class TeamInviteSerializer(serializers.ModelSerializer):
    invited_by_email = serializers.EmailField(source="invited_by.email", read_only=True)
    is_pending = serializers.BooleanField(read_only=True)

    class Meta:
        model = TeamInvite
        fields = [
            "id", "email", "role", "invited_by_email",
            "created_at", "expires_at", "accepted_at", "is_revoked", "is_pending", "create_mailbox",
        ]


class TeamInviteCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=["admin", "support", "read_only"])
    create_mailbox = serializers.BooleanField(required=False, default=False)


class APIKeySerializer(serializers.ModelSerializer):
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True)
    display = serializers.SerializerMethodField()
    scopes = serializers.SerializerMethodField()
    is_read_only = serializers.BooleanField(read_only=True)

    class Meta:
        model = APIKey
        fields = [
            "id", "name", "key_prefix", "display", "scopes", "is_read_only",
            "created_by_email", "created_at", "last_used_at", "expires_at", "is_active",
        ]

    def get_display(self, obj):
        return f"mm_{obj.key_prefix}..."

    def get_scopes(self, obj):
        # Read through normalise so a row written before scopes existed, or by
        # hand, is presented the same way it is enforced.
        return normalise_scopes(obj.scopes)


class ScopeListField(serializers.ListField):
    """
    A list of known scope values.

    Unknown values are rejected rather than silently dropped: a caller asking
    for "domains:admin" has a wrong mental model of what they are granting, and
    quietly issuing a read-only key would hide that.
    """

    child = serializers.ChoiceField(choices=APIKeyScope.choices)

    def to_internal_value(self, data):
        values = super().to_internal_value(data)
        unknown = sorted(set(values) - ALL_SCOPES)
        if unknown:
            raise serializers.ValidationError(f"Unknown scope(s): {', '.join(unknown)}.")
        return normalise_scopes(values)


class APIKeyCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    # Omitting scopes yields a read-only key. Creating a key as owner or admin
    # does not make it a write key — the write scopes have to be named.
    scopes = ScopeListField(required=False, default=list(DEFAULT_SCOPES))


class APIKeyUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100, required=False)
    scopes = ScopeListField(required=False)
