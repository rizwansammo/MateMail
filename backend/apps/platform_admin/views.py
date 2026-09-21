import logging

from django.db.models import F
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.billing.models import Plan, PlanTier, Subscription, SubscriptionStatus
from apps.billing.serializers import SubscriptionSerializer
from .approval import (
    ApprovalError,
    approve_tenant,
    reactivate_tenant,
    reject_tenant,
    set_mailbox_suspended,
    set_outbound_enabled,
    suspend_tenant,
)
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
                plan_name = sub.plan.display_name
                sub_status = sub.status
                trial_ends_at = sub.trial_ends_at.isoformat() if sub.trial_ends_at else None
            except Exception:
                plan_tier = None
                plan_name = None
                sub_status = None
                trial_ends_at = None

            data.append({
                "id": str(t.id),
                "name": t.name,
                "slug": t.slug,
                "status": t.status,
                "plan": t.plan,
                "plan_tier": plan_tier,
                "plan_name": plan_name,
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

        # Domain's fields are `domain` and `added_at`; the admin UI consumes
        # `name` and `created_at`, so alias rather than rename the API contract.
        domains = list(
            tenant.domains
            .values("id", "status", name=F("domain"), created_at=F("added_at"))
            .order_by("-added_at")[:30]
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
            # Approval and outbound state. Both are current facts a console
            # operator acts on, and both were previously only derivable by
            # reading the audit log — which meant the detail page could not
            # show whether sending was switched off without a second request.
            "approved_at": tenant.approved_at.isoformat() if tenant.approved_at else None,
            "approved_by": tenant.approved_by.email if tenant.approved_by else None,
            "review_reason": tenant.review_reason,
            "outbound_disabled": tenant.outbound_disabled,
            "outbound_disabled_at": (
                tenant.outbound_disabled_at.isoformat()
                if tenant.outbound_disabled_at else None
            ),
            "can_send_outbound": tenant.can_send_mail,
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
    """
    POST /api/platform/tenants/{id}/suspend/

    Stops a workspace completely: no sending, no receiving, no provisioning.
    Optional body: {"reason": "..."}.

    The response reports `engine_applied` — whether the engine-side half was
    queued. Suspension is two mechanisms, and an operator handling an abuse
    incident needs to know if only one of them took.
    """
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request, pk):
        if not Tenant.objects.filter(pk=pk).exists():
            return Response({"detail": "Not found."}, status=404)

        try:
            tenant, engine_queued = suspend_tenant(
                pk, actor=request.user, reason=request.data.get("reason", "")
            )
        except ApprovalError as exc:
            return Response({"detail": str(exc)}, status=400)

        return Response({
            "id": str(tenant.id),
            "status": tenant.status,
            "engine_applied": engine_queued,
        })


class AdminTenantActivateView(APIView):
    """
    POST /api/platform/tenants/{id}/activate/

    Returns a suspended workspace to service. Refuses a workspace that has
    never been approved — approval is a separate, explicit act.
    """
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request, pk):
        if not Tenant.objects.filter(pk=pk).exists():
            return Response({"detail": "Not found."}, status=404)

        try:
            tenant, engine_queued = reactivate_tenant(
                pk, actor=request.user, reason=request.data.get("reason", "")
            )
        except ApprovalError as exc:
            return Response({"detail": str(exc)}, status=400)

        return Response({
            "id": str(tenant.id),
            "status": tenant.status,
            "engine_applied": engine_queued,
        })


class AdminTenantPlanView(APIView):
    """
    POST /api/platform/tenants/{id}/plan/

    Assign a plan. Private Beta is approval-based, not time-limited, so there
    is no trial-extension operation.
    """
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request, pk):
        try:
            tenant = Tenant.objects.get(pk=pk)
        except Tenant.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)

        plan_tier = request.data.get("plan_tier")
        if not plan_tier:
            return Response({"detail": "Provide plan_tier."}, status=400)

        plan = Plan.objects.filter(tier=plan_tier, is_active=True).first()
        if not plan:
            return Response({"detail": f"Unknown plan tier: {plan_tier}"}, status=400)

        sub, _ = Subscription.objects.get_or_create(
            tenant=tenant,
            defaults={
                "plan": plan,
                "status": SubscriptionStatus.ACTIVE,
                "trial_ends_at": None,
            },
        )
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
        log_event(
            tenant,
            LogEventType.PLAN_CHANGED,
            source=request.user.email,
            metadata={"plan": plan_tier},
        )

        return Response(SubscriptionSerializer(sub).data)


# ── Approval and abuse controls (P5) ─────────────────────────────────────────
#
# Every endpoint below is IsPlatformAdmin, which refuses API-key credentials
# outright — a key minted by a member of staff must never become a
# platform-admin credential sitting in a customer's config file. A customer
# cannot reach these at all: APIKeyScopeMiddleware denies /api/platform/, and
# the permission denies non-staff sessions.

class AdminTenantApproveView(APIView):
    """
    POST /api/platform/tenants/{id}/approve/   {"reason": "optional"}

    Lets a workspace use the Mail Engine. Until this runs, the workspace can
    sign in and configure itself but can provision nothing.
    """
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request, pk):
        try:
            tenant = approve_tenant(
                pk, actor=request.user, reason=request.data.get("reason", "")
            )
        except Tenant.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)
        except ApprovalError as exc:
            return Response({"detail": str(exc)}, status=409)

        return Response({
            "id": str(tenant.id),
            "status": tenant.status,
            "approved_at": tenant.approved_at,
            "approved_by": request.user.email,
        })


class AdminTenantRejectView(APIView):
    """
    POST /api/platform/tenants/{id}/reject/   {"reason": "optional"}

    Refuses a workspace. Nothing is deleted, and the decision is reversible by
    approving later — a rejection that destroyed data would make a mistake
    unrecoverable and an appeal impossible.
    """
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request, pk):
        try:
            tenant = reject_tenant(
                pk, actor=request.user, reason=request.data.get("reason", "")
            )
        except Tenant.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)
        except ApprovalError as exc:
            return Response({"detail": str(exc)}, status=409)

        return Response({"id": str(tenant.id), "status": tenant.status})


class AdminTenantOutboundView(APIView):
    """
    POST /api/platform/tenants/{id}/outbound/   {"enabled": false, "reason": "..."}

    Switches a workspace's outbound sending off or on without suspending it.
    The first abuse response to reach for: it stops sending immediately, leaves
    everything else working, and reverses just as fast if the suspicion was
    wrong.
    """
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request, pk):
        enabled = request.data.get("enabled")
        if not isinstance(enabled, bool):
            return Response({"enabled": "Provide true or false."}, status=400)

        try:
            tenant = set_outbound_enabled(
                pk, enabled=enabled, actor=request.user,
                reason=request.data.get("reason", ""),
            )
        except Tenant.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)
        except ApprovalError as exc:
            return Response({"detail": str(exc)}, status=409)

        return Response({
            "id": str(tenant.id),
            "outbound_disabled": tenant.outbound_disabled,
        })


class AdminPendingTenantsView(APIView):
    """
    GET /api/platform/tenants/pending/

    The approval queue: workspaces waiting on a decision, oldest first, so the
    person doing the reviewing has somewhere to look.
    """
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request):
        pending = (
            Tenant.objects
            .filter(status=TenantStatus.PENDING_APPROVAL)
            .select_related("owner")
            .order_by("created_at")
        )
        return Response([
            {
                "id": str(t.id),
                "name": t.name,
                "slug": t.slug,
                "owner_email": t.owner.email if t.owner else "",
                "created_at": t.created_at,
                "waiting_days": (timezone.now() - t.created_at).days,
            }
            for t in pending
        ])



class AdminMailboxSuspendView(APIView):
    """
    POST   /api/platform/mailboxes/{id}/suspend/   — suspend one mailbox
    DELETE /api/platform/mailboxes/{id}/suspend/   — release it

    MateMail's narrowest abuse response: stop one compromised account without
    touching the rest of a workspace that has done nothing wrong.

    Only a platform admin can set or clear this. A tenant admin's own control
    offers active and disabled only, and cannot change a suspended mailbox at
    all — otherwise a customer could simply re-enable the account MateMail
    just stopped.

    Optional body: {"reason": "..."}.
    """
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def _act(self, request, pk, *, suspended: bool):
        from apps.mailboxes.models import Mailbox

        try:
            mailbox = set_mailbox_suspended(
                pk,
                suspended=suspended,
                actor=request.user,
                reason=request.data.get("reason", "") if request.data else "",
            )
        except Mailbox.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)
        except ApprovalError as exc:
            return Response({"detail": str(exc)}, status=400)

        return Response({"id": str(mailbox.id), "status": mailbox.status})

    def post(self, request, pk):
        return self._act(request, pk, suspended=True)

    def delete(self, request, pk):
        return self._act(request, pk, suspended=False)
