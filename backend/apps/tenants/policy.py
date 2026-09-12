"""
The mail-capability gate.

One authoritative answer to "may this workspace create or change Mail Engine
resources?", and one authoritative answer to "may it send?". Every provisioning
path calls these; nothing re-implements the rule.

That single-point discipline is the whole design. Scattered `if tenant.status
!= SUSPENDED` checks are how one endpoint ends up enforcing a policy the next
one forgot — and the endpoint that forgot is the one an attacker finds. When a
new state or a new condition is added, it is added here and every caller
inherits it.

Everything here **fails closed**. An unknown status, a missing subscription, a
tenant that cannot be loaded: all denied. And every refusal carries a
MateMail-authored message safe to show a customer — no status codes, no
internal vocabulary, no engine detail.
"""
from __future__ import annotations

import logging

from .models import MAIL_ENABLED_STATUSES, TenantStatus

logger = logging.getLogger(__name__)


class MailNotPermitted(Exception):
    """
    This workspace may not use the Mail Engine right now.

    `str(exc)` is the customer-facing message, following the same rule as the
    Mail Engine's own errors: a caller that writes `detail = str(exc)` cannot
    leak anything internal. `reason_code` is for logs and tests, never for a
    response body.
    """

    def __init__(self, customer_message: str, *, reason_code: str):
        self.customer_message = customer_message
        self.reason_code = reason_code
        super().__init__(customer_message)

    def __str__(self) -> str:
        return self.customer_message


#: Customer-facing text per refusal reason. Deliberately vague about *why* a
#: workspace was rejected or suspended — that conversation happens with a human,
#: not through an API error a script can enumerate.
_MESSAGES = {
    "pending_approval": (
        "This workspace is waiting for approval before email can be set up. "
        "We will be in touch once it has been reviewed."
    ),
    "rejected": (
        "This workspace has not been approved for email. "
        "Please contact support if you believe this is a mistake."
    ),
    "suspended": (
        "This workspace is suspended, so email settings cannot be changed. "
        "Please contact support."
    ),
    "cancelled": (
        "This workspace has been closed, so email settings cannot be changed."
    ),
    "past_due": (
        "This workspace is past due, so email settings cannot be changed until "
        "billing is up to date."
    ),
    "not_approved": (
        "This workspace is waiting for approval before email can be set up."
    ),
    "unknown_state": (
        "Email is not available for this workspace right now. "
        "Our team has been notified."
    ),
    "outbound_disabled": (
        "Sending is temporarily disabled for this workspace. "
        "Please contact support."
    ),
}


def assert_can_use_mail(tenant) -> None:
    """
    Raise `MailNotPermitted` unless this workspace may touch the Mail Engine.

    Call this before **any** path that creates, changes or provisions a domain,
    mailbox, alias or forwarding rule. Not before read-only views: a suspended
    workspace can still look at what it has.
    """
    if tenant is None:
        raise MailNotPermitted(_MESSAGES["unknown_state"], reason_code="no_tenant")

    status = tenant.status

    if status == TenantStatus.PENDING_APPROVAL:
        raise MailNotPermitted(_MESSAGES["pending_approval"], reason_code="pending_approval")
    if status == TenantStatus.REJECTED:
        raise MailNotPermitted(_MESSAGES["rejected"], reason_code="rejected")
    if status == TenantStatus.SUSPENDED:
        raise MailNotPermitted(_MESSAGES["suspended"], reason_code="suspended")
    if status == TenantStatus.CANCELLED:
        raise MailNotPermitted(_MESSAGES["cancelled"], reason_code="cancelled")
    if status == TenantStatus.PAST_DUE:
        raise MailNotPermitted(_MESSAGES["past_due"], reason_code="past_due")

    if status not in MAIL_ENABLED_STATUSES:
        # A status added later without being considered here. Denied rather
        # than permitted, and loud, because silence is how this becomes a hole.
        logger.error(
            "Tenant %s has status %r, which is not in MAIL_ENABLED_STATUSES. "
            "Denying mail access. Add it to the policy deliberately.",
            getattr(tenant, "id", "?"), status,
        )
        raise MailNotPermitted(_MESSAGES["unknown_state"], reason_code="unknown_state")

    if tenant.approved_at is None:
        # Mail-enabled status but never approved. Reachable if a status is set
        # directly — a fixture, a shell, a future admin action that forgets.
        # The approval timestamp is the fact; the status alone is not.
        raise MailNotPermitted(_MESSAGES["not_approved"], reason_code="not_approved")


def assert_can_send_mail(tenant) -> None:
    """
    Raise unless this workspace may send outbound mail right now.

    Strictly narrower than `assert_can_use_mail`: outbound can be switched off
    on its own as an abuse response, leaving the workspace able to receive mail
    and administer itself.
    """
    assert_can_use_mail(tenant)
    if tenant.outbound_disabled:
        raise MailNotPermitted(_MESSAGES["outbound_disabled"], reason_code="outbound_disabled")


def mail_denial_reason(tenant) -> str:
    """
    The reason code, or `""` when mail is permitted.

    For logs, tests and the SMTP policy bridge — which needs to branch on the
    reason rather than catch an exception. Never put this in a customer
    response; use the exception's message.
    """
    try:
        assert_can_use_mail(tenant)
    except MailNotPermitted as exc:
        return exc.reason_code
    return ""


#: Refusals that are expected to resolve, and so must NOT produce a permanent
#: bounce (Stage 14 — the bounce classification).
#:
#: This distinction is the difference between mail delayed and mail destroyed.
#: A remote server told 5xx gives up immediately and returns the message to its
#: sender; told 4xx it queues and retries for several days. Every refusal in the
#: policy bridge used to be a flat REJECT, so a workspace suspended over a
#: billing question on Friday permanently bounced every message sent to it until
#: Monday — and those messages were gone, with the senders told the addresses
#: did not work.
#:
#: The rule: refuse permanently only when the answer will not change. Suspension
#: is reversible by design, past-due is a payment away, and approval is pending
#: precisely because someone is going to decide it. Rejection and cancellation
#: are decisions already made.
#:
#: `unknown_state` and `no_tenant` are temporary on purpose. They mean MateMail
#: does not know, and "I do not know" must never be expressed as "this address
#: does not exist" — that fails closed for delivery while failing SAFE for the
#: customer's mail.
TEMPORARY_DENIALS = frozenset({
    "pending_approval",
    "not_approved",
    "suspended",
    "past_due",
    "unknown_state",
    "no_tenant",
})


def is_temporary_denial(reason_code: str) -> bool:
    """
    Should this refusal be a temporary deferral rather than a permanent bounce?

    Unrecognised codes are treated as temporary. A reason nobody has classified
    is a reason nobody has thought about, and the safe reading of that is
    "retry", not "destroy the message".
    """
    if not reason_code:
        return False
    return reason_code in TEMPORARY_DENIALS or reason_code not in _MESSAGES
