from rest_framework import serializers
from .models import Plan, Subscription


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = [
            "id",
            "tier",
            "display_name",
            "price_monthly",
            "max_domains",
            "max_mailboxes",
            "max_members",
            "max_aliases",
            "default_storage_per_mailbox_mb",
            "max_storage_per_mailbox_mb",
            "max_storage_total_mb",
            "max_messages_per_hour_per_mailbox",
            "max_messages_per_day_per_tenant",
            "includes_spam_quarantine",
            "includes_audit_logs",
            "includes_queue_visibility",
            "includes_backup_controls",
            "includes_team_roles",
        ]


class SubscriptionSerializer(serializers.ModelSerializer):
    plan = PlanSerializer(read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Subscription
        fields = [
            "id",
            "status",
            "status_display",
            "plan",
            "trial_ends_at",
            "current_period_start",
            "current_period_end",
            "created_at",
        ]
