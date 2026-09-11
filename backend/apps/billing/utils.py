"""
Billing utility helpers — plan limit checks used by domain and mailbox views.
"""
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
    """Returns (allowed: bool, error_message: str)."""
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
    """Returns (allowed: bool, error_message: str)."""
    plan = get_plan(tenant)
    if not plan:
        return True, ""
    if is_trial_expired(tenant):
        return False, "Your free trial has expired. Please upgrade to continue adding mailboxes."
    current = tenant.mailboxes.count()
    if current >= plan.max_mailboxes:
        return False, f"Your {plan.display_name} plan allows up to {plan.max_mailboxes} mailbox(es). Upgrade to add more."
    return True, ""


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
