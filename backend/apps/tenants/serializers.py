from rest_framework import serializers

from .models import Tenant, TenantMembership


class TenantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tenant
        fields = ["id", "name", "slug", "status", "plan", "created_at"]
        read_only_fields = ["id", "slug", "status", "plan", "created_at"]


class TenantCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)


class TenantMembershipSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)

    class Meta:
        model = TenantMembership
        fields = ["id", "email", "full_name", "role", "status", "created_at"]
        read_only_fields = ["id", "email", "full_name", "created_at"]


class MemberInviteSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=["admin", "support", "read_only"])


class MemberRoleUpdateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=["admin", "support", "read_only"])


class WorkspaceDetailSerializer(serializers.ModelSerializer):
    domain_count = serializers.SerializerMethodField()
    mailbox_count = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()
    my_role = serializers.SerializerMethodField()

    class Meta:
        model = Tenant
        fields = [
            "id", "name", "slug", "status", "plan",
            "domain_count", "mailbox_count", "member_count",
            "my_role", "created_at",
        ]
        read_only_fields = ["id", "slug", "status", "plan", "created_at"]

    def get_domain_count(self, obj):
        return obj.domains.count()

    def get_mailbox_count(self, obj):
        return obj.mailboxes.count()

    def get_member_count(self, obj):
        return obj.memberships.filter(status="active").count()

    def get_my_role(self, obj):
        request = self.context.get("request")
        if not request:
            return None
        mem = obj.memberships.filter(user=request.user, status="active").first()
        return mem.role if mem else None
