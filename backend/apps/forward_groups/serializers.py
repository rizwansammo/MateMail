from rest_framework import serializers

from .models import (
    ForwardGroup,
    ForwardGroupAllowedSender,
    ForwardGroupMember,
    ForwardGroupMemberRole,
    ForwardGroupSenderPolicy,
)


class ForwardGroupMemberSerializer(serializers.ModelSerializer):
    mailbox_id = serializers.UUIDField(source="mailbox.id", read_only=True)
    email = serializers.EmailField(source="mailbox.email", read_only=True)
    full_name = serializers.CharField(source="mailbox.full_name", read_only=True)
    kind = serializers.CharField(source="mailbox.kind", read_only=True)

    class Meta:
        model = ForwardGroupMember
        fields = ["id", "mailbox_id", "email", "full_name", "kind", "role", "created_at"]


class ForwardGroupAllowedSenderSerializer(serializers.ModelSerializer):
    mailbox_id = serializers.UUIDField(source="mailbox.id", read_only=True)
    email = serializers.EmailField(source="mailbox.email", read_only=True)
    full_name = serializers.CharField(source="mailbox.full_name", read_only=True)

    class Meta:
        model = ForwardGroupAllowedSender
        fields = ["id", "mailbox_id", "email", "full_name", "created_at"]


class ForwardGroupSerializer(serializers.ModelSerializer):
    domain_name = serializers.CharField(source="domain.domain", read_only=True)
    member_count = serializers.SerializerMethodField()
    sender_count = serializers.SerializerMethodField()
    mail_service_ready = serializers.BooleanField(
        source="mail_engine_provisioned", read_only=True
    )
    mail_service_message = serializers.CharField(
        source="mail_engine_error", read_only=True
    )

    class Meta:
        model = ForwardGroup
        fields = [
            "id",
            "address",
            "local_part",
            "display_name",
            "domain",
            "domain_name",
            "status",
            "sender_policy",
            "member_count",
            "sender_count",
            "mail_service_ready",
            "mail_service_message",
            "created_at",
            "updated_at",
        ]

    def get_member_count(self, obj):
        return obj.members.count()

    def get_sender_count(self, obj):
        return obj.allowed_senders.count()


class ForwardGroupCreateSerializer(serializers.Serializer):
    local_part = serializers.CharField(max_length=64)
    domain_id = serializers.UUIDField()
    display_name = serializers.CharField(max_length=255)
    member_mailbox_ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
    )
    sender_policy = serializers.ChoiceField(
        choices=ForwardGroupSenderPolicy.choices,
        default=ForwardGroupSenderPolicy.ANYONE,
    )
    allowed_sender_mailbox_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
    )

    def validate_local_part(self, value):
        return value.strip().lower()

    def validate(self, attrs):
        attrs["member_mailbox_ids"] = list(dict.fromkeys(attrs["member_mailbox_ids"]))
        attrs["allowed_sender_mailbox_ids"] = list(
            dict.fromkeys(attrs.get("allowed_sender_mailbox_ids") or [])
        )
        return attrs


class ForwardGroupMemberCreateSerializer(serializers.Serializer):
    mailbox_id = serializers.UUIDField()
    role = serializers.ChoiceField(
        choices=ForwardGroupMemberRole.choices,
        default=ForwardGroupMemberRole.MEMBER,
    )


class ForwardGroupMemberUpdateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=ForwardGroupMemberRole.choices)


class ForwardGroupSenderCreateSerializer(serializers.Serializer):
    mailbox_id = serializers.UUIDField()


class ForwardGroupPolicySerializer(serializers.Serializer):
    sender_policy = serializers.ChoiceField(
        choices=ForwardGroupSenderPolicy.choices
    )


class ForwardGroupStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["active", "disabled"])
