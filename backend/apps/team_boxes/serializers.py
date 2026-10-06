from rest_framework import serializers

from apps.mail_directory.models import MailboxAccessGrant


class TeamBoxMemberSerializer(serializers.ModelSerializer):
    mailbox_id = serializers.UUIDField(source="grantee_mailbox.id", read_only=True)
    email = serializers.EmailField(source="grantee_mailbox.email", read_only=True)
    full_name = serializers.CharField(source="grantee_mailbox.full_name", read_only=True)

    class Meta:
        model = MailboxAccessGrant
        fields = [
            "id",
            "mailbox_id",
            "email",
            "full_name",
            "can_read",
            "can_manage",
            "can_send_as",
            "can_send_on_behalf",
            "active",
            "created_at",
            "updated_at",
        ]


class TeamBoxSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    email = serializers.EmailField()
    full_name = serializers.CharField()
    local_part = serializers.CharField()
    domain = serializers.UUIDField(source="domain_id")
    domain_name = serializers.CharField(source="domain.domain")
    kind = serializers.CharField()
    status = serializers.CharField()
    quota_mb = serializers.IntegerField()
    storage_used_mb = serializers.IntegerField()
    mail_service_ready = serializers.BooleanField(source="mail_engine_provisioned")
    mail_service_message = serializers.CharField(source="mail_engine_error")
    created_at = serializers.DateTimeField()
    member_count = serializers.SerializerMethodField()

    def get_member_count(self, obj):
        return obj.access_grants_received.filter(
            grant_type="team_box",
            active=True,
        ).count()


class TeamBoxCreateSerializer(serializers.Serializer):
    local_part = serializers.CharField(max_length=64)
    domain_id = serializers.UUIDField()
    display_name = serializers.CharField(max_length=255)
    quota_mb = serializers.IntegerField(required=False, allow_null=True, min_value=1)

    def validate_local_part(self, value):
        return value.lower().strip()


class TeamBoxStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["active", "disabled"])


class TeamBoxMemberCreateSerializer(serializers.Serializer):
    mailbox_id = serializers.UUIDField()
    can_read = serializers.BooleanField(default=True)
    can_manage = serializers.BooleanField(default=True)
    can_send_as = serializers.BooleanField(default=True)
    can_send_on_behalf = serializers.BooleanField(default=False)

    def validate(self, attrs):
        if attrs.get("can_manage") and not attrs.get("can_read"):
            raise serializers.ValidationError(
                {"can_manage": "Manage permission requires read permission."}
            )
        if not any(
            attrs.get(field)
            for field in (
                "can_read",
                "can_manage",
                "can_send_as",
                "can_send_on_behalf",
            )
        ):
            raise serializers.ValidationError(
                "A TeamBox member must have at least one permission."
            )
        return attrs


class TeamBoxMemberUpdateSerializer(serializers.Serializer):
    can_read = serializers.BooleanField(required=False)
    can_manage = serializers.BooleanField(required=False)
    can_send_as = serializers.BooleanField(required=False)
    can_send_on_behalf = serializers.BooleanField(required=False)
