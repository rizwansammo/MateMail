"""
MateMail-owned Mail Engine error types.

Product code must never inspect engine error strings. The adapter translates
whatever the engine said into one of these types, and callers branch on the
type.

Two rules make these safe to handle carelessly:

1. **`str(exc)` is the customer message.** The technical detail lives in
   `.technical_detail` and is never part of `__str__`. So even the old
   anti-pattern `field = str(exc)` cannot leak an engine hostname, port, API
   path or product name into a customer-visible field.

2. **`customer_message` is authored by MateMail**, not derived from engine text.
   It describes what the customer should understand and do, in MateMail's terms.

Raw engine detail is for logs only. Log it with `exc.technical_detail`.
"""
from __future__ import annotations


class MailEngineError(Exception):
    """
    Base class for every failure crossing the Mail Engine boundary.

    Used directly only when the engine's response cannot be mapped to a more
    specific type — the customer message stays deliberately vague because we do
    not know what happened.
    """

    #: MateMail-authored. Safe to show a customer. Never contains engine detail.
    customer_message = (
        "Mail service configuration could not be completed. "
        "Our team has been notified — please try again shortly."
    )

    def __init__(self, technical_detail: str = "", *, operation: str = ""):
        #: Engine-shaped text for server logs ONLY. Never serialize this.
        self.technical_detail = technical_detail or ""
        #: Which boundary operation failed, for log correlation.
        self.operation = operation or ""
        super().__init__(self.customer_message)

    def __str__(self) -> str:
        # Deliberately the customer message, not the technical detail.
        return self.customer_message

    def __repr__(self) -> str:
        # Repr is developer-facing (logs, tracebacks) so detail is allowed here.
        return f"{type(self).__name__}(operation={self.operation!r}, detail={self.technical_detail!r})"

    @property
    def log_message(self) -> str:
        """One-line technical summary for the server log."""
        parts = [type(self).__name__]
        if self.operation:
            parts.append(f"op={self.operation}")
        if self.technical_detail:
            parts.append(self.technical_detail)
        return " | ".join(parts)


class EngineUnavailable(MailEngineError):
    """
    The engine could not be reached, timed out, or returned a transport error.

    Retryable. Callers should queue a retry rather than surface a hard failure.
    """

    customer_message = (
        "Mail service is temporarily unavailable. "
        "This will be retried automatically — no action is needed."
    )


class AlreadyExists(MailEngineError):
    """
    The engine already holds a conflicting object that is not the one we meant.

    Note this is NOT raised by the idempotent `ensure_*` operations, which treat
    an existing matching object as success. It signals a genuine collision.
    """

    customer_message = "That address is already in use."


class NotFound(MailEngineError):
    """
    The referenced object does not exist in the engine.

    Not raised by the idempotent `delete_*` operations, which treat a missing
    object as already-deleted.
    """

    customer_message = "That mail resource could not be found."


class Rejected(MailEngineError):
    """The engine understood the request and refused it (validation, policy)."""

    customer_message = (
        "Mail service rejected this request. Please check the address and try again."
    )


class EngineCapabilityMissing(MailEngineError):
    """
    The engine has no API for this operation at the pinned version.

    Distinct from `Rejected` (the engine understood and refused) and from
    `NotFound` (the object is absent). This one means MateMail asked for
    something the engine does not implement, which is a gap in *our* plan, not
    a runtime failure — and it must surface loudly rather than be swallowed as
    a no-op success.
    """

    customer_message = (
        "That action is not available yet. Our team has been notified."
    )


class QuotaExceeded(MailEngineError):
    """The request would exceed a limit enforced by the engine."""

    customer_message = (
        "This would exceed the mail storage available on your plan. "
        "Reduce the quota or upgrade your plan to continue."
    )


#: Every concrete type, for exhaustiveness tests.
#:
#: `tests/test_engine_leak.py` walks this tuple to assert that no type's
#: customer-facing message can carry engine detail. A type missing from here is
#: therefore a type nothing checks — which is what happened to
#: EngineCapabilityMissing between its introduction and P4C-A. Add new error
#: types here in the same commit that defines them.
ALL_ERROR_TYPES = (
    MailEngineError,
    EngineUnavailable,
    AlreadyExists,
    NotFound,
    Rejected,
    EngineCapabilityMissing,
    QuotaExceeded,
)
