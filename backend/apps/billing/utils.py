"""
Billing utility helpers — plan limit checks used by domain and mailbox views.
"""
from contextlib import contextmanager

from django.db import transaction
from django.utils import timezone


TRIAL_DAYS = 30


def get_subscription(tenant):
    """Return the tenant's Subscription, or None if not yet created."""
    try:
        return tenant.subscription
    except Exception:
        return None


def get_plan(tenant):
    """Return the Plan for the tenant's active subscription, or None."""
    sub = get_subscription(tenant)
    return sub.plan if sub else None


def days_left_on_trial(tenant):
    """Days remaining in trial. Returns 0 if expired or not trialing."""
    sub = get_subscription(tenant)
    if not sub or not sub.trial_ends_at:
        return 0
    from apps.billing.models import SubscriptionStatus
    if sub.status != SubscriptionStatus.TRIALING:
        return 0
    delta = sub.trial_ends_at - timezone.now()
    return max(0, delta.days)


def is_trial_expired(tenant):
    sub = get_subscription(tenant)
    if not sub:
        return False
    from apps.billing.models import SubscriptionStatus
    return sub.status == SubscriptionStatus.PAST_DUE


def check_domain_limit(tenant):
    """
    Returns (allowed: bool, error_message: str).

    **Not race-safe on its own.** Two concurrent requests can both read a count
    one below the cap and both proceed. Callers that create a resource must run
    this inside `reserve_resource_slot`, which takes the tenant row lock — see
    that function.
    """
    plan = get_plan(tenant)
    if not plan:
        return True, ""
    if is_trial_expired(tenant):
        return False, "Your free trial has expired. Please upgrade to continue adding domains."
    current = tenant.domains.count()
    if current >= plan.max_domains:
        return False, f"Your {plan.display_name} plan allows up to {plan.max_domains} domain(s). Upgrade to add more."
    return True, ""


def check_mailbox_limit(tenant):
    """
    Returns (allowed: bool, error_message: str).

    Same race caveat as `check_domain_limit`.
    """
    plan = get_plan(tenant)
    if not plan:
        return True, ""
    if is_trial_expired(tenant):
        return False, "Your free trial has expired. Please upgrade to continue adding mailboxes."
    current = tenant.mailboxes.count()
    if current >= plan.max_mailboxes:
        return False, f"Your {plan.display_name} plan allows up to {plan.max_mailboxes} mailbox(es). Upgrade to add more."
    return True, ""


def check_alias_limit(tenant):
    """
    Returns (allowed: bool, error_message: str).

    Aliases were previously uncapped in MateMail entirely. An alias is a free
    extra sending and receiving address, which makes it the cheapest way to
    turn one approved workspace into many identities — so it needs a cap for
    the same reason mailboxes do.
    """
    plan = get_plan(tenant)
    if not plan:
        return True, ""
    if is_trial_expired(tenant):
        return False, "Your free trial has expired. Please upgrade to continue adding aliases."
    current = tenant.aliases.count()
    if current >= plan.max_aliases:
        return False, f"Your {plan.display_name} plan allows up to {plan.max_aliases} alias(es). Upgrade to add more."
    return True, ""


@contextmanager
def reserve_resource_slot(tenant, check):
    """
    Hold the tenant row while a cap is checked and the resource is created.

    ## Why a lock is necessary

    `count() >= cap` followed by `create()` is a check-then-act race. Two
    requests arriving together both read a count one below the cap, both decide
    they are fine, and the workspace ends up one over — every time, reliably,
    under any concurrency. For mailboxes and domains that is worse than an
    accounting error: each one provisions a real object into the Mail Engine,
    so the overage is mail infrastructure a customer was never entitled to.

    Serialising on the tenant row is the same pattern `apps.tenants.views`
    already uses for member seats, and the lock is per workspace — one
    customer's mailbox creation never blocks another's.

    Usage:

        with reserve_resource_slot(request.tenant, check_mailbox_limit) as ok:
            if not ok.allowed:
                return Response({"detail": ok.message}, status=402)
            Mailbox.objects.create(...)

    The resource MUST be created inside the block. Creating it afterwards puts
    the write outside the lock and reintroduces the race this exists to close.
    """
    from apps.tenants.models import Tenant

    with transaction.atomic():
        # Re-read under the lock: the caller's copy may predate a concurrent
        # change, and the counts below must be read in the same locked view.
        locked = Tenant.objects.select_for_update().get(pk=tenant.pk)
        allowed, message = check(locked)
        yield _SlotDecision(allowed=allowed, message=message, tenant=locked)


class _SlotDecision:
    __slots__ = ("allowed", "message", "tenant")

    def __init__(self, allowed: bool, message: str, tenant):
        self.allowed = allowed
        self.message = message
        self.tenant = tenant


def count_member_slots(tenant) -> int:
    """
    Seats currently spoken for: active members plus outstanding invites.

    A pending invite reserves a seat. The alternative — counting only accepted
    members — lets a workspace on a three-seat plan send thirty invites and end
    up with thirty members, because each acceptance individually looks fine
    against a count taken before any of them landed. Reserving means an admin
    must revoke an invite to free a seat, which is the behaviour a customer can
    reason about. Documented in docs/SECURITY.md.
    """
    from django.utils import timezone

    active = tenant.memberships.filter(status="active").count()
    pending = tenant.invites.filter(
        is_revoked=False,
        accepted_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).count()
    return active + pending


def check_member_limit(tenant, *, additional: int = 1):
    """
    Returns (allowed: bool, error_message: str).

    `additional` is how many seats the caller is about to take. Callers must
    run this inside the same transaction that creates the membership or invite,
    with the tenant row locked — see `apps.tenants.views` — or two concurrent
    invites can both read the same count and both succeed.
    """
    plan = get_plan(tenant)
    if not plan:
        return True, ""
    if is_trial_expired(tenant):
        return False, "Your free trial has expired."
    if count_member_slots(tenant) + additional > plan.max_members:
        return False, (
            f"Your {plan.display_name} plan allows up to {plan.max_members} "
            f"team member(s), including pending invitations. Remove a member "
            f"or revoke an invitation, or upgrade your plan."
        )
    return True, ""


def resolve_mailbox_quota(plan, requested_mb):
    """
    Decide a mailbox's storage, from the plan — never from the request alone.

    Returns `(quota_mb, error_message)`. A non-empty error means refuse.

    The client MAY choose a size, because paid plans will want that. It may not
    choose one the plan does not permit, and it may not choose by omission
    either: an absent value takes the plan's default, not a constant buried in a
    serializer. The serializer previously defaulted to 10 GB with no ceiling, so
    a 1 GB trial workspace could request any number and be given it.

    Ordering matters. The plan's own limits are validated first: a plan whose
    default exceeds its ceiling is a configuration error, and silently handing
    out the smaller of the two would hide it.
    """
    if plan is None:
        # No subscription resolved. Fall back to the smallest sane mailbox
        # rather than an unbounded one — absence of a plan is not permission.
        return 1024, ""

    default_mb = plan.default_storage_per_mailbox_mb
    ceiling_mb = plan.max_storage_per_mailbox_mb

    if default_mb > ceiling_mb:
        # Refuse rather than clamp: see DEC-016. An operator has to see which
        # of the two numbers is wrong.
        return 0, (
            "Mail storage limits are misconfigured for this plan. "
            "Our team has been notified."
        )

    if requested_mb in (None, ""):
        return default_mb, ""

    try:
        requested_mb = int(requested_mb)
    except (TypeError, ValueError):
        return 0, "Mailbox size must be a whole number of megabytes."

    if requested_mb <= 0:
        return 0, "Mailbox size must be greater than zero."

    if requested_mb > ceiling_mb:
        return 0, (
            f"Your {plan.display_name} plan allows up to {ceiling_mb} MB per "
            f"mailbox. Choose {ceiling_mb} MB or less, or upgrade your plan."
        )

    return requested_mb, ""
