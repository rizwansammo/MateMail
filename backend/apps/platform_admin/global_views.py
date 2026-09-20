"""
Platform-wide read and oversight APIs.

These are the endpoints behind the Platform Console's cross-organization
pages: every domain, every mailbox, every alias, the queue, the quarantine,
the logs, the audit trail, the plans and the health summary.

THE SHAPE OF EVERY VIEW HERE
    Global by design, and only to a platform administrator. `IsPlatformAdmin`
    additionally refuses an API key even when the key belongs to a member of
    staff, so nothing in this module can become a credential in a customer's
    configuration file.

    Every list is paginated with a hard ceiling. An operator asking for "all
    mailboxes" on a platform with a hundred thousand of them should get the
    first page, not a timeout and a copy of the table in memory.

WHAT IS DELIBERATELY ABSENT
    No message bodies, anywhere. The queue and quarantine views return
    envelope metadata — who sent it, who it was for, what the filter thought —
    because that is what an abuse decision needs. Reading a customer's mail is
    a different boundary, and no endpoint here crosses it.

    No secrets: DKIM private keys, verification tokens, password hashes and
    mailbox credentials are all excluded field by field rather than by
    serialising the model and hoping.
"""
from __future__ import annotations

from django.db.models import Count, Q, Sum
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.aliases.models import Alias
from apps.domains.models import Domain
from apps.forwarding.models import ForwardingRule
from apps.logs.models import MailLog
from apps.mailboxes.models import Mailbox
from apps.mailqueue.models import QueueMessage
from apps.quarantine.models import QuarantineMessage
from apps.tenants.models import Tenant
from apps.tenants.permissions import IsPlatformAdmin

from .models import PlatformAuditLog

#: Page size ceiling. A console table is read by a person; beyond this the
#: answer to "show me everything" is a filter, not a bigger page.
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50


def paginate(request, queryset) -> dict:
    """
    Offset pagination with a bounded page size.

    `total` is a COUNT over the filtered queryset, which is what lets the
    console say "showing 50 of 1,284" rather than leaving an operator unsure
    whether a filter matched everything or nothing.
    """
    try:
        page = max(int(request.query_params.get("page", 1)), 1)
    except (TypeError, ValueError):
        page = 1
    try:
        size = int(request.query_params.get("page_size", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        size = DEFAULT_PAGE_SIZE
    size = max(1, min(size, MAX_PAGE_SIZE))

    total = queryset.count()
    start = (page - 1) * size
    return {
        "results": list(queryset[start:start + size]),
        "page": page,
        "page_size": size,
        "total": total,
        "has_next": start + size < total,
    }


def _iso(value):
    return value.isoformat() if value else None


def _tenant_brief(tenant) -> dict | None:
    if tenant is None:
        return None
    return {"id": str(tenant.id), "name": tenant.name, "slug": tenant.slug,
            "status": tenant.status}


class PlatformListView(APIView):
    """Shared base: platform-admin only, paginated, with a search term."""

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    #: Fields an operator may order by. An allowlist, because `order_by` on
    #: arbitrary user input is both an error surface and a way to sort by a
    #: column that should not be exposed.
    ordering_fields: tuple[str, ...] = ()
    default_ordering = "-created_at"

    def ordering(self, request) -> str:
        requested = request.query_params.get("ordering", "")
        if requested.lstrip("-") in self.ordering_fields:
            return requested
        return self.default_ordering

    def search_term(self, request) -> str:
        return (request.query_params.get("search") or "").strip()

    def respond(self, request, queryset, serialise):
        page = paginate(request, queryset)
        return Response({
            "results": [serialise(row) for row in page["results"]],
            "page": page["page"],
            "page_size": page["page_size"],
            "total": page["total"],
            "has_next": page["has_next"],
        })


# ── Domains ─────────────────────────────────────────────────────────────────

class PlatformDomainListView(PlatformListView):
    """Every domain on the platform, with its verification and DKIM state."""

    ordering_fields = ("domain", "added_at", "status", "dns_health_score")
    default_ordering = "-added_at"

    def get(self, request):
        qs = Domain.objects.select_related("tenant").all()

        term = self.search_term(request)
        if term:
            qs = qs.filter(Q(domain__icontains=term) | Q(tenant__name__icontains=term))
        if status_filter := request.query_params.get("status"):
            qs = qs.filter(status=status_filter)
        if ownership := request.query_params.get("ownership_status"):
            qs = qs.filter(ownership_status=ownership)
        if tenant_id := request.query_params.get("tenant"):
            qs = qs.filter(tenant_id=tenant_id)
        if request.query_params.get("provisioned") == "false":
            qs = qs.filter(mail_engine_provisioned=False)

        return self.respond(request, qs.order_by(self.ordering(request)), serialise_domain)


def serialise_domain(domain) -> dict:
    """
    Field by field, never `model_to_dict`.

    `dkim_private_key` and `verification_token` live on this model. A
    serialiser that enumerated fields automatically would ship both the moment
    somebody added a new one, and the DKIM private key is the credential that
    lets anyone sign mail as the customer.
    """
    return {
        "id": str(domain.id),
        "domain": domain.domain,
        "tenant": _tenant_brief(domain.tenant),
        "status": domain.status,
        "ownership_status": domain.ownership_status,
        "ownership_verified_at": _iso(domain.ownership_verified_at),
        "verification_last_checked_at": _iso(domain.verification_last_checked_at),
        "verification_last_error": domain.verification_last_error,
        "ownership_recheck_failures": domain.ownership_recheck_failures,
        "dns_health_score": domain.dns_health_score,
        "dkim_selector": domain.dkim_selector,
        # Whether a key exists, never the key.
        "dkim_configured": bool(domain.dkim_public_key),
        "mail_engine_provisioned": domain.mail_engine_provisioned,
        "mail_engine_error": domain.mail_engine_error,
        "added_at": _iso(domain.added_at),
        "verified_at": _iso(domain.verified_at),
    }


# ── Mailboxes ───────────────────────────────────────────────────────────────

class PlatformMailboxListView(PlatformListView):
    ordering_fields = ("email", "created_at", "status", "storage_used_mb")
    default_ordering = "-created_at"

    def get(self, request):
        qs = Mailbox.objects.select_related("tenant", "domain").all()

        term = self.search_term(request)
        if term:
            qs = qs.filter(
                Q(email__icontains=term)
                | Q(full_name__icontains=term)
                | Q(tenant__name__icontains=term)
                | Q(domain__domain__icontains=term)
            )
        if status_filter := request.query_params.get("status"):
            qs = qs.filter(status=status_filter)
        if tenant_id := request.query_params.get("tenant"):
            qs = qs.filter(tenant_id=tenant_id)
        if domain_id := request.query_params.get("domain"):
            qs = qs.filter(domain_id=domain_id)

        return self.respond(request, qs.order_by(self.ordering(request)), serialise_mailbox)


def serialise_mailbox(mailbox) -> dict:
    return {
        "id": str(mailbox.id),
        "email": mailbox.email,
        "full_name": mailbox.full_name,
        "tenant": _tenant_brief(mailbox.tenant),
        "domain": mailbox.domain.domain if mailbox.domain else None,
        "status": mailbox.status,
        "quota_mb": mailbox.quota_mb,
        "storage_used_mb": mailbox.storage_used_mb,
        "mail_engine_provisioned": mailbox.mail_engine_provisioned,
        "mail_engine_error": mailbox.mail_engine_error,
        "last_login": _iso(mailbox.last_login),
        "created_at": _iso(mailbox.created_at),
        # No password, no hash, no credential of any kind.
    }


# ── Aliases and forwarding ──────────────────────────────────────────────────

class PlatformAliasListView(PlatformListView):
    """
    Alias oversight, not alias editing.

    Day-to-day alias management belongs to the Organization Console. What a
    platform operator needs is the cross-organization view that an abuse
    report starts from.
    """

    ordering_fields = ("source_address", "created_at", "status")
    default_ordering = "-created_at"

    def get(self, request):
        qs = Alias.objects.select_related("tenant", "domain", "destination_mailbox")

        term = self.search_term(request)
        if term:
            qs = qs.filter(
                Q(source_address__icontains=term)
                | Q(destination_address__icontains=term)
                | Q(tenant__name__icontains=term)
            )
        if status_filter := request.query_params.get("status"):
            qs = qs.filter(status=status_filter)
        if tenant_id := request.query_params.get("tenant"):
            qs = qs.filter(tenant_id=tenant_id)

        return self.respond(request, qs.order_by(self.ordering(request)), serialise_alias)


def serialise_alias(alias) -> dict:
    destination = alias.destination_address
    if not destination and alias.destination_mailbox:
        destination = alias.destination_mailbox.email
    return {
        "id": str(alias.id),
        "kind": "alias",
        "source": alias.source_address,
        "destination": destination,
        "tenant": _tenant_brief(alias.tenant),
        "domain": alias.domain.domain if alias.domain else None,
        "status": alias.status,
        "mail_engine_provisioned": alias.mail_engine_provisioned,
        "created_at": _iso(alias.created_at),
        # An alias whose destination leaves the platform is the shape abuse
        # takes, so the console can surface it without re-deriving the rule.
        "external_destination": _is_external(destination),
    }


def _is_external(address: str | None) -> bool:
    """
    True when the destination is not a domain this platform hosts.

    Computed here rather than in the browser so the rule lives with the data,
    and cached per request by the caller's queryset — a handful of domains
    compared against a page of fifty rows.
    """
    if not address or "@" not in address:
        return False
    domain = address.rsplit("@", 1)[1].lower()
    return not Domain.objects.filter(domain__iexact=domain).exists()


class PlatformForwardingListView(PlatformListView):
    ordering_fields = ("destination_email", "created_at", "status")
    default_ordering = "-created_at"

    def get(self, request):
        qs = ForwardingRule.objects.select_related("tenant", "source_mailbox")

        term = self.search_term(request)
        if term:
            qs = qs.filter(
                Q(destination_email__icontains=term)
                | Q(source_mailbox__email__icontains=term)
                | Q(tenant__name__icontains=term)
            )
        if status_filter := request.query_params.get("status"):
            qs = qs.filter(status=status_filter)
        if tenant_id := request.query_params.get("tenant"):
            qs = qs.filter(tenant_id=tenant_id)
        if request.query_params.get("external") == "true":
            # Forwarding off the platform is the case that carries the abuse
            # and data-exfiltration risk, so it is filterable on its own.
            hosted = Domain.objects.values_list("domain", flat=True)
            external = Q()
            for hosted_domain in hosted:
                external |= Q(destination_email__iendswith=f"@{hosted_domain}")
            qs = qs.exclude(external)

        return self.respond(request, qs.order_by(self.ordering(request)),
                            serialise_forwarding)


def serialise_forwarding(rule) -> dict:
    return {
        "id": str(rule.id),
        "kind": "forwarding",
        "source": rule.source_mailbox.email if rule.source_mailbox else None,
        "destination": rule.destination_email,
        "tenant": _tenant_brief(rule.tenant),
        "status": rule.status,
        "keep_copy": rule.keep_copy,
        "mail_engine_provisioned": rule.mail_engine_provisioned,
        "created_at": _iso(rule.created_at),
        "external_destination": _is_external(rule.destination_email),
    }


# ── Mail operations ─────────────────────────────────────────────────────────

class PlatformQueueListView(PlatformListView):
    """
    The outbound queue across organizations. Metadata only.

    `subject` is included because an operator triaging a spam incident needs to
    see that four thousand messages share one subject line. The body is not,
    and there is no endpoint here that returns one.
    """

    ordering_fields = ("queued_at", "status", "retry_count")
    default_ordering = "-queued_at"

    def get(self, request):
        qs = QueueMessage.objects.select_related("tenant")

        term = self.search_term(request)
        if term:
            qs = qs.filter(
                Q(sender__icontains=term)
                | Q(recipient__icontains=term)
                | Q(subject__icontains=term)
                | Q(tenant__name__icontains=term)
            )
        if status_filter := request.query_params.get("status"):
            qs = qs.filter(status=status_filter)
        if tenant_id := request.query_params.get("tenant"):
            qs = qs.filter(tenant_id=tenant_id)

        return self.respond(request, qs.order_by(self.ordering(request)), serialise_queue)


def serialise_queue(message) -> dict:
    return {
        "id": str(message.id),
        "tenant": _tenant_brief(message.tenant),
        "sender": message.sender,
        "recipient": message.recipient,
        "subject": message.subject,
        "status": message.status,
        "reason": message.reason,
        "retry_count": message.retry_count,
        "queued_at": _iso(message.queued_at),
        "last_retry": _iso(message.last_retry),
        "next_retry": _iso(message.next_retry),
    }


class PlatformQuarantineListView(PlatformListView):
    ordering_fields = ("received_at", "spam_score", "status")
    default_ordering = "-received_at"

    def get(self, request):
        qs = QuarantineMessage.objects.select_related("tenant")

        term = self.search_term(request)
        if term:
            qs = qs.filter(
                Q(sender__icontains=term)
                | Q(recipient__icontains=term)
                | Q(subject__icontains=term)
                | Q(tenant__name__icontains=term)
            )
        if status_filter := request.query_params.get("status"):
            qs = qs.filter(status=status_filter)
        if tenant_id := request.query_params.get("tenant"):
            qs = qs.filter(tenant_id=tenant_id)

        return self.respond(request, qs.order_by(self.ordering(request)),
                            serialise_quarantine)


def serialise_quarantine(message) -> dict:
    return {
        "id": str(message.id),
        "tenant": _tenant_brief(message.tenant),
        "sender": message.sender,
        "recipient": message.recipient,
        "subject": message.subject,
        "spam_score": float(message.spam_score),
        "status": message.status,
        "received_at": _iso(message.received_at),
        "actioned_at": _iso(message.actioned_at),
    }


# ── Logs ────────────────────────────────────────────────────────────────────

class PlatformMailLogListView(PlatformListView):
    ordering_fields = ("created_at", "event_type", "result")
    default_ordering = "-created_at"

    def get(self, request):
        qs = MailLog.objects.select_related("tenant")

        term = self.search_term(request)
        if term:
            qs = qs.filter(
                Q(source__icontains=term)
                | Q(event_type__icontains=term)
                | Q(tenant__name__icontains=term)
            )
        if event_type := request.query_params.get("event_type"):
            qs = qs.filter(event_type=event_type)
        if result := request.query_params.get("result"):
            qs = qs.filter(result=result)
        if tenant_id := request.query_params.get("tenant"):
            qs = qs.filter(tenant_id=tenant_id)

        return self.respond(request, qs.order_by(self.ordering(request)), serialise_log)


def serialise_log(row) -> dict:
    return {
        "id": str(row.id),
        "tenant": _tenant_brief(row.tenant),
        "event_type": row.event_type,
        "source": row.source,
        "result": row.result,
        "ip_address": row.ip_address,
        "metadata": row.metadata,
        "created_at": _iso(row.created_at),
    }


class PlatformAuditLogListView(PlatformListView):
    """Who did what in this console."""

    ordering_fields = ("created_at", "action")
    default_ordering = "-created_at"

    def get(self, request):
        qs = PlatformAuditLog.objects.select_related("actor", "tenant", "target_user")

        term = self.search_term(request)
        if term:
            qs = qs.filter(
                Q(actor_email__icontains=term)
                | Q(action__icontains=term)
                | Q(target_label__icontains=term)
                | Q(tenant__name__icontains=term)
            )
        if action := request.query_params.get("action"):
            qs = qs.filter(action=action)
        if tenant_id := request.query_params.get("tenant"):
            qs = qs.filter(tenant_id=tenant_id)

        return self.respond(request, qs.order_by(self.ordering(request)),
                            serialise_audit)


def serialise_audit(row) -> dict:
    return {
        "id": str(row.id),
        "actor_email": row.actor_email,
        "action": row.action,
        "tenant": _tenant_brief(row.tenant),
        "target_label": row.target_label or (
            row.target_user.email if row.target_user else ""
        ),
        "result": row.result,
        "reason": row.reason,
        "ip_address": row.ip_address,
        "metadata": row.metadata,
        "created_at": _iso(row.created_at),
    }


class PlatformAuditActionsView(APIView):
    """The distinct actions present, so the console's filter is not hard-coded."""

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request):
        actions = (
            PlatformAuditLog.objects.values_list("action", flat=True)
            .distinct().order_by("action")
        )
        return Response({"actions": list(actions)})


# ── Plans ───────────────────────────────────────────────────────────────────

class PlatformPlanListView(APIView):
    """
    The plans, and how many organizations are on each.

    Read-only. Assigning a plan to an organization already has an endpoint
    (`tenants/<id>/plan/`); editing the plan catalogue itself is a pricing
    decision, not an operational one, and is deliberately not a console button.
    """

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request):
        from apps.billing.models import Plan

        plans = Plan.objects.annotate(
            subscriber_count=Count("subscriptions", distinct=True)
        ).order_by("price_monthly")

        return Response({"results": [
            {
                "id": str(plan.id),
                "tier": plan.tier,
                "display_name": plan.display_name,
                "price_monthly": float(plan.price_monthly),
                "is_active": plan.is_active,
                "subscriber_count": plan.subscriber_count,
                "limits": {
                    "max_domains": plan.max_domains,
                    "max_mailboxes": plan.max_mailboxes,
                    "max_members": plan.max_members,
                    "max_aliases": plan.max_aliases,
                    "default_storage_per_mailbox_mb": plan.default_storage_per_mailbox_mb,
                    "max_storage_per_mailbox_mb": plan.max_storage_per_mailbox_mb,
                    "max_storage_total_mb": plan.max_storage_total_mb,
                    "max_messages_per_hour_per_mailbox":
                        plan.max_messages_per_hour_per_mailbox,
                    "max_messages_per_day_per_tenant":
                        plan.max_messages_per_day_per_tenant,
                },
                "features": {
                    "includes_spam_quarantine": plan.includes_spam_quarantine,
                    "includes_audit_logs": plan.includes_audit_logs,
                    "includes_queue_visibility": plan.includes_queue_visibility,
                    "includes_backup_controls": plan.includes_backup_controls,
                    "includes_team_roles": plan.includes_team_roles,
                },
            }
            for plan in plans
        ]})


# ── Global search ───────────────────────────────────────────────────────────

class PlatformSearchView(APIView):
    """
    One box, the five things an operator starts from.

    Deliberately small: indexed `icontains` against five tables with a hard
    limit each. An incident starts with an address, a domain or an
    organization name, and this turns any of them into the right page. It is
    not a search product and does not need one.
    """

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    #: Per-category cap. The console shows a jump list, not a result set.
    LIMIT = 8

    def get(self, request):
        term = (request.query_params.get("q") or "").strip()
        if len(term) < 2:
            return Response({"query": term, "results": []})

        results: list[dict] = []

        # A UUID is the other thing an operator pastes — out of a log line or
        # an error report — so it is matched exactly as well as by substring.
        exact_id = _as_uuid(term)

        tenants = Tenant.objects.select_related("owner")
        tenant_match = Q(name__icontains=term) | Q(slug__icontains=term) | Q(
            owner__email__icontains=term)
        if exact_id:
            tenant_match |= Q(id=exact_id)
        for tenant in tenants.filter(tenant_match)[:self.LIMIT]:
            results.append({
                "type": "organization",
                "id": str(tenant.id),
                "label": tenant.name,
                "sublabel": tenant.owner.email if tenant.owner else tenant.slug,
                "status": tenant.status,
                "href": f"/platform/organizations/{tenant.id}",
            })

        domain_match = Q(domain__icontains=term)
        if exact_id:
            domain_match |= Q(id=exact_id)
        for domain in Domain.objects.select_related("tenant").filter(
                domain_match)[:self.LIMIT]:
            results.append({
                "type": "domain",
                "id": str(domain.id),
                "label": domain.domain,
                "sublabel": domain.tenant.name if domain.tenant else "",
                "status": domain.status,
                "href": f"/platform/domains?search={domain.domain}",
            })

        mailbox_match = Q(email__icontains=term)
        if exact_id:
            mailbox_match |= Q(id=exact_id)
        for mailbox in Mailbox.objects.select_related("tenant").filter(
                mailbox_match)[:self.LIMIT]:
            results.append({
                "type": "mailbox",
                "id": str(mailbox.id),
                "label": mailbox.email,
                "sublabel": mailbox.tenant.name if mailbox.tenant else "",
                "status": mailbox.status,
                "href": f"/platform/mailboxes?search={mailbox.email}",
            })

        return Response({"query": term, "results": results})


def _as_uuid(value: str):
    import uuid

    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
