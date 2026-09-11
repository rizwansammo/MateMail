from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apps.tenants.models import Tenant


def log_event(
    tenant: "Tenant",
    event_type,
    *,
    result: str = "success",
    source: str = "",
    request=None,
    metadata: dict | None = None,
    **kwargs,
) -> None:
    from apps.logs.models import MailLog

    ip_address = None
    if request is not None:
        # The leftmost X-Forwarded-For entry is whatever the caller put there.
        # An audit trail that records an attacker-chosen address is worse than
        # one that records none, because it reads as evidence. get_client_ip
        # trusts only the hops our own infrastructure added.
        from apps.security.client_ip import get_client_ip

        ip_address = get_client_ip(request)
        if not source and hasattr(request, "user") and request.user.is_authenticated:
            source = request.user.email

    event_value = event_type.value if hasattr(event_type, "value") else str(event_type)

    extra: dict = {}
    for k, v in kwargs.items():
        try:
            extra[k] = str(v.id) if hasattr(v, "id") else str(v)
        except Exception:
            pass
    if metadata:
        extra.update(metadata)

    try:
        MailLog.objects.create(
            tenant=tenant,
            event_type=event_value,
            source=source or "",
            result=result,
            ip_address=ip_address,
            metadata=extra,
        )
    except Exception:
        pass
