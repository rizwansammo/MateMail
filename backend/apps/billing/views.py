from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.tenants.permissions import HasTenantAccess
from .models import SubscriptionStatus
from .serializers import SubscriptionSerializer
from .utils import get_subscription


class BillingView(APIView):
    permission_classes = [IsAuthenticated, HasTenantAccess]

    def get(self, request):
        tenant = request.tenant
        sub = get_subscription(tenant)

        if not sub:
            return Response({
                "subscription": None,
                "usage": _build_usage(tenant, None),
                "trial_days_left": 0,
            })

        plan = sub.plan
        trial_days_left = 0
        if sub.status == SubscriptionStatus.TRIALING and sub.trial_ends_at:
            delta = sub.trial_ends_at - timezone.now()
            trial_days_left = max(0, delta.days)

        return Response({
            "subscription": SubscriptionSerializer(sub).data,
            "usage": _build_usage(tenant, plan),
            "trial_days_left": trial_days_left,
        })


def _build_usage(tenant, plan):
    domain_count = tenant.domains.count()
    mailbox_count = tenant.mailboxes.count()
    member_count = tenant.memberships.filter(status="active").count()
    alias_count = tenant.aliases.count()

    from apps.mailboxes.models import Mailbox
    from django.db.models import Sum

    storage = Mailbox.objects.for_tenant(tenant).aggregate(
        allocated=Sum("quota_mb"),
        used=Sum("storage_used_mb"),
    )

    return {
        "domains": domain_count,
        "mailboxes": mailbox_count,
        "members": member_count,
        "aliases": alias_count,
        "storage_allocated_mb": storage["allocated"] or 0,
        "storage_used_mb": storage["used"] or 0,
        "max_domains": plan.max_domains if plan else None,
        "max_mailboxes": plan.max_mailboxes if plan else None,
        "max_members": plan.max_members if plan else None,
        "max_aliases": plan.max_aliases if plan else None,
        "max_storage_per_mailbox_mb": plan.max_storage_per_mailbox_mb if plan else None,
        "max_storage_total_mb": plan.max_storage_total_mb if plan else None,
    }
