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


def check_member_limit(tenant):
    """Returns (allowed: bool, error_message: str)."""
    plan = get_plan(tenant)
    if not plan:
        return True, ""
    if is_trial_expired(tenant):
        return False, "Your free trial has expired."
    current = tenant.memberships.filter(status="active").count()
    if current >= plan.max_members:
        return False, f"Your {plan.display_name} plan allows up to {plan.max_members} team member(s). Upgrade to add more."
    return True, ""
