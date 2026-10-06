from rest_framework import serializers

from apps.mail_directory.models import MailboxAccessGrant


class DelegationSerializer(serializers.ModelSerializer):
    target_mailbox_id = serializers.UUIDField(source="target_mailbox.id", read_only=True)
    target_email = serializers.EmailField(source="target_mailbox.email", read_only=True)
    target_name = serializers.CharField(source="target_mailbox.full_name", read_only=True)
    delegate_mailbox_id = serializers.UUIDField(source="grantee_mailbox.id", read_only=True)
    delegate_email = serializers.EmailField(source="grantee_mailbox.email", read_only=True)
    delegate_name = serializers.CharField(source="grantee_mailbox.full_name", read_only=True)

    class Meta:
        model = MailboxAccessGrant
        fields = [
            "id",
            "target_mailbox_id",
            "target_email",
            "target_name",
            "delegate_mailbox_id",
            "delegate_email",
            "delegate_name",
            "can_read",
            "can_manage",
            "can_send_as",
            "can_send_on_behalf",
            "active",
            "created_at",
            "updated_at",
        ]


class DelegationCreateSerializer(serializers.Serializer):
    target_mailbox_id = serializers.UUIDField()
    delegate_mailbox_id = serializers.UUIDField()
    can_read = serializers.BooleanField(default=True)
    can_manage = serializers.BooleanField(default=False)
    can_send_as = serializers.BooleanField(default=False)
    can_send_on_behalf = serializers.BooleanField(default=False)

    def validate(self, attrs):
        if attrs["target_mailbox_id"] == attrs["delegate_mailbox_id"]:
            raise serializers.ValidationError(
                {"delegate_mailbox_id": "A mailbox cannot be delegated to itself."}
            )
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
                "A delegation must grant at least one permission."
            )
        return attrs


class DelegationUpdateSerializer(serializers.Serializer):
    can_read = serializers.BooleanField(required=False)
    can_manage = serializers.BooleanField(required=False)
    can_send_as = serializers.BooleanField(required=False)
    can_send_on_behalf = serializers.BooleanField(required=False)
    active = serializers.BooleanField(required=False)
