import logging

from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.models import Plan, PlanTier, Subscription, SubscriptionStatus
from apps.billing.serializers import SubscriptionSerializer
from apps.logs.models import LogEventType, MailLog
from apps.logs.utils import log_event
from apps.tenants.models import Tenant, TenantStatus
from apps.tenants.permissions import IsPlatformAdmin

logger = logging.getLogger(__name__)


class AdminStatsView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request):
        from apps.accounts.models import User
        from apps.domains.models import Domain
        from apps.mailboxes.models import Mailbox

        tenants = Tenant.objects.all()
        status_counts = {}
        for s in TenantStatus:
            status_counts[s.value] = tenants.filter(status=s).count()

        sub_counts = {}
        for s in SubscriptionStatus:
            sub_counts[s.value] = Subscription.objects.filter(status=s).count()

        recent = (
            Tenant.objects
            .select_related("owner")
            .order_by("-created_at")[:10]
        )
        recent_data = [
            {
                "id": str(t.id),
                "name": t.name,
                "status": t.status,
                "owner_email": t.owner.email,
                "created_at": t.created_at.isoformat(),
            }
            for t in recent
        ]

        return Response({
            "total_tenants": tenants.count(),
            "total_users": User.objects.filter(is_active=True).count(),
            "total_domains": Domain.objects.count(),
            "total_mailboxes": Mailbox.objects.count(),
            "tenant_by_status": status_counts,
            "subscriptions_by_status": sub_counts,
            "recent_tenants": recent_data,
        })


class AdminTenantListView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request):
        qs = Tenant.objects.select_related("owner").order_by("-created_at")

        # Optional filters
        status_filter = request.query_params.get("status")
        if status_filter and status_filter != "all":
            qs = qs.filter(status=status_filter)

        search = request.query_params.get("search", "").strip()
        if search:
            from django.db.models import Q
            qs = qs.filter(
                Q(name__icontains=search) | Q(owner__email__icontains=search)
            )

        qs = qs[:200]
        data = []
        for t in qs:
            try:
                sub = t.subscription
                plan_tier = sub.plan.tier
                sub_status = sub.status
                trial_ends_at = sub.trial_ends_at.isoformat() if sub.trial_ends_at else None
            except Exception:
                plan_tier = None
                sub_status = None
                trial_ends_at = None

            data.append({
                "id": str(t.id),
                "name": t.name,
                "slug": t.slug,
                "status": t.status,
                "plan": t.plan,
                "plan_tier": plan_tier,
                "sub_status": sub_status,
                "trial_ends_at": trial_ends_at,
                "owner_email": t.owner.email,
                "domain_count": t.domains.count(),
                "mailbox_count": t.mailboxes.count(),
                "member_count": t.memberships.filter(status="active").count(),
                "created_at": t.created_at.isoformat(),
            })
        return Response(data)


class AdminTenantDetailView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request, pk):
        try:
            tenant = Tenant.objects.select_related("owner").get(pk=pk)
        except Tenant.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)

        try:
            sub = tenant.subscription
            subscription_data = SubscriptionSerializer(sub).data
        except Exception:
            subscription_data = None

        domains = list(
            tenant.domains
            .values("id", "name", "status", "created_at")
            .order_by("-created_at")[:30]
        )
        for d in domains:
            d["id"] = str(d["id"])
            d["created_at"] = d["created_at"].isoformat()

        members = list(
            tenant.memberships
            .filter(status="active")
            .select_related("user")
            .order_by("created_at")[:30]
        )
        members_data = [
            {
                "id": str(m.id),
                "email": m.user.email,
                "full_name": m.user.full_name,
                "role": m.role,
                "joined_at": m.created_at.isoformat(),
            }
            for m in members
        ]

        logs = list(
            MailLog.objects.filter(tenant=tenant)
            .order_by("-created_at")[:20]
            .values("event_type", "result", "source", "ip_address", "created_at")
        )
        for entry in logs:
            entry["created_at"] = entry["created_at"].isoformat()

        plans = list(Plan.objects.filter(is_active=True).values("tier", "display_name"))

        return Response({
            "id": str(tenant.id),
            "name": tenant.name,
            "slug": tenant.slug,
            "status": tenant.status,
            "plan": tenant.plan,
            "owner_email": tenant.owner.email,
            "owner_id": str(tenant.owner_id),
            "created_at": tenant.created_at.isoformat(),
            "updated_at": tenant.updated_at.isoformat(),
            "domain_count": tenant.domains.count(),
            "mailbox_count": tenant.mailboxes.count(),
            "member_count": tenant.memberships.filter(status="active").count(),
            "subscription": subscription_data,
            "domains": domains,
            "members": members_data,
            "recent_logs": logs,
            "available_plans": plans,
        })


class AdminTenantSuspendView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request, pk):
        try:
            tenant = Tenant.objects.get(pk=pk)
        except Tenant.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)

        if tenant.status == TenantStatus.SUSPENDED:
            return Response({"detail": "Already suspended."}, status=400)

        tenant.status = TenantStatus.SUSPENDED
        tenant.save(update_fields=["status", "updated_at"])
        logger.info("Platform admin %s suspended tenant %s", request.user.email, tenant.id)
        log_event(tenant, LogEventType.TENANT_SUSPENDED, source=request.user.email)
        return Response({"id": str(tenant.id), "status": tenant.status})


class AdminTenantActivateView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request, pk):
        try:
            tenant = Tenant.objects.get(pk=pk)
        except Tenant.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)

        tenant.status = TenantStatus.ACTIVE
        tenant.save(update_fields=["status", "updated_at"])
        logger.info("Platform admin %s activated tenant %s", request.user.email, tenant.id)
        log_event(tenant, LogEventType.TENANT_REACTIVATED, source=request.user.email)
        return Response({"id": str(tenant.id), "status": tenant.status})


class AdminTenantPlanView(APIView):
    """
    POST /api/platform/tenants/{id}/plan/

    Assign a plan or extend the trial:
      {"plan_tier": "business"}       → set plan + mark subscription ACTIVE
      {"extend_trial_days": 30}       → extend trial by N days from now
    """
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request, pk):
        try:
            tenant = Tenant.objects.get(pk=pk)
        except Tenant.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)

        plan_tier = request.data.get("plan_tier")
        extend_days = request.data.get("extend_trial_days")

        if not plan_tier and not extend_days:
            return Response({"detail": "Provide plan_tier or extend_trial_days."}, status=400)

        sub, _ = Subscription.objects.get_or_create(
            tenant=tenant,
            defaults={
                "plan": Plan.objects.filter(tier=PlanTier.TRIAL).first(),
                "status": SubscriptionStatus.TRIALING,
                "trial_ends_at": timezone.now() + timezone.timedelta(days=30),
            },
        )

        if plan_tier:
            plan = Plan.objects.filter(tier=plan_tier, is_active=True).first()
            if not plan:
                return Response({"detail": f"Unknown plan tier: {plan_tier}"}, status=400)
            sub.plan = plan
            sub.status = SubscriptionStatus.ACTIVE
            sub.trial_ends_at = None
            sub.save(update_fields=["plan", "status", "trial_ends_at", "updated_at"])
            if tenant.status == TenantStatus.TRIAL:
                tenant.status = TenantStatus.ACTIVE
                tenant.save(update_fields=["status", "updated_at"])
            logger.info(
                "Platform admin %s assigned plan %s to tenant %s",
                request.user.email, plan_tier, tenant.id,
            )
            log_event(tenant, LogEventType.PLAN_CHANGED, source=request.user.email,
                      metadata={"plan": plan_tier})

        elif extend_days:
            try:
                extend_days = int(extend_days)
            except (TypeError, ValueError):
                return Response({"detail": "extend_trial_days must be an integer."}, status=400)
            base = max(sub.trial_ends_at or timezone.now(), timezone.now())
            sub.trial_ends_at = base + timezone.timedelta(days=extend_days)
            sub.status = SubscriptionStatus.TRIALING
            sub.save(update_fields=["trial_ends_at", "status", "updated_at"])
            if tenant.status not in (TenantStatus.ACTIVE, TenantStatus.SUSPENDED):
                tenant.status = TenantStatus.TRIAL
                tenant.save(update_fields=["status", "updated_at"])
            logger.info(
                "Platform admin %s extended trial for tenant %s by %d days",
                request.user.email, tenant.id, extend_days,
            )

        return Response(SubscriptionSerializer(sub).data)
