from rest_framework import serializers
from .models import APIKey, TeamInvite


class TeamInviteSerializer(serializers.ModelSerializer):
    invited_by_email = serializers.EmailField(source="invited_by.email", read_only=True)
    is_pending = serializers.BooleanField(read_only=True)

    class Meta:
        model = TeamInvite
        fields = [
            "id", "email", "role", "invited_by_email",
            "created_at", "expires_at", "accepted_at", "is_revoked", "is_pending",
        ]


class TeamInviteCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=["admin", "support", "read_only"])


class APIKeySerializer(serializers.ModelSerializer):
    created_by_email = serializers.EmailField(source="created_by.email", read_only=True)
    display = serializers.SerializerMethodField()

    class Meta:
        model = APIKey
        fields = [
            "id", "name", "key_prefix", "display",
            "created_by_email", "created_at", "last_used_at", "expires_at", "is_active",
        ]

    def get_display(self, obj):
        return f"mm_{obj.key_prefix}..."


class APIKeyCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
