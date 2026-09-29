"""
NativeMailEngineAdapter — MateMail's client for the MateMail Native Engine.

WHAT IT TALKS TO
    `matemail-native-api` over HTTP, inside the engine's private network. It
    does NOT open a database connection. Django must never connect to the engine
    database (NE0.2): the API is the only direct writer, and an adapter holding
    a second write path would end that invariant on its first query.

WHY IT IS THE SAME SHAPE AS MailcowAdapter
    So NE5 is a swap rather than a rewrite. Both implement the same port, take
    the same DTOs, raise the same typed errors, and are exercised by the same
    contract tests. Product code cannot tell which one is underneath, which is
    the entire point of the port.

WHAT NE2 IMPLEMENTS
    Domains, mailboxes, passwords, quotas, aliases, forwarding and DKIM — the
    provisioning subset.

    Rate limits, storage usage, the queue and the quarantine raise
    `EngineCapabilityMissing`. They are not stubbed to return empty results: an
    adapter that answers "the queue is empty" without looking would report a
    healthy queue during an incident. Refusing is the honest answer until NE4
    wires the measurements.

NOT IN PRODUCTION
    `MAIL_ENGINE_ADAPTER` stays "mailcow". This class exists, is tested, and is
    not selected.
"""
from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlparse

import requests
from django.conf import settings

from .adapter import MailEngineAdapter
from .dto import (
    AliasSpec,
    DkimKeyInfo,
    DomainSpec,
    EngineDomain,
    EngineHealth,
    EngineMailbox,
    ForwardingSpec,
    MailboxSpec,
    MailboxUsage,
    RateLimit,
)
from .errors import (
    EngineCapabilityMissing,
    EngineUnavailable,
    MailEngineError,
    NotFound,
    QuotaExceeded,
    Rejected,
)

logger = logging.getLogger(__name__)

_TIMEOUT = 15

#: Operations the Native Engine will gain later. Named here so the refusal says
#: which phase owns the work instead of "not implemented".
#:
#: ALL EIGHT BELONG TO NE4, and none of them block the NE3 mail path.
#:
#: They were previously split NE3/NE4, which read as though NE3 were unfinished
#: without them. It is not: NE3 delivers authenticated submission, sender
#: authorisation, filtering, DKIM signing, LMTP delivery and quota ENFORCEMENT,
#: and none of that calls any of these.
#:
#:   rate limits    NE4 lists rate limiting as work it validates in isolation.
#:                  The mechanism belongs with Rspamd's ratelimit module and the
#:                  policy path, not with the delivery path NE3 built.
#:   mailbox usage  Enforcement already works (Dovecot refuses an over-quota
#:                  delivery). READING usage back needs an administrative channel
#:                  to Dovecot that NE4's quota validation needs anyway, and it
#:                  must exist before NE5 puts customers in front of it.
#:   queue and quarantine
#:                  NE4, unchanged — these are operational surfaces over a queue
#:                  that only becomes interesting once traffic is real.
#:
#: Refusing with `EngineCapabilityMissing` is deliberate. A fabricated
#: `used_mb=0` or an empty queue would be a measurement that is wrong the moment
#: the first message is delivered, and wrong quietly.
#: Operations the Native Engine does not implement.
#:
#: EMPTY AS OF NE4. Every method of the 26-method port is implemented against
#: the real engine — rate limits enforced by the submission policy service,
#: usage measured by Dovecot, queue and quarantine driven by Postfix's own
#: queue. `_unavailable` is kept because a future contract addition should
#: refuse honestly rather than return a plausible-looking zero.
_LATER: dict[str, str] = {}


class NativeMailEngineAdapter(MailEngineAdapter):
    def __init__(self, base_url: str = "", secret: str = ""):
        # No loopback fallback:
        # loopback is this container, never the engine, so a default would turn
        # a missing setting into a confusing connection error against ourselves.
        base = base_url or getattr(settings, "NATIVE_ENGINE_API_URL", "") or ""
        self._base = base.rstrip("/")
        self._host = urlparse(self._base).hostname or "matemail-native-api"
        self._session = requests.Session()
        self._session.headers.update({
            "X-Native-Api-Secret": secret or getattr(settings, "NATIVE_ENGINE_API_SECRET", ""),
            "Content-Type": "application/json",
        })

    # ── Transport ───────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, *, json=None, params=None,
                 operation: str = "", allow_404: bool = False):
        """
        One engine call.

        A request body may contain a mailbox password, so it is NEVER logged —
        not on success, not on failure, not at debug level. Only the method, the
        path and the status ever reach a log line.
        """
        operation = operation or f"{method} {path}"
        try:
            response = self._session.request(
                method, f"{self._base}{path}", json=json, params=params, timeout=_TIMEOUT
            )
        except requests.RequestException as exc:
            logger.error("Native engine transport failure [%s]: %s", operation, exc)
            raise EngineUnavailable(f"transport: {exc}", operation=operation) from exc

        if response.status_code in (401, 403):
            logger.error(
                "Native engine rejected our credentials [%s] status=%s — check "
                "NATIVE_ENGINE_API_SECRET", operation, response.status_code,
            )
            raise EngineUnavailable(
                f"auth rejected status={response.status_code}", operation=operation
            )

        if response.status_code >= 500:
            logger.error("Native engine server error [%s] status=%s", operation,
                         response.status_code)
            raise EngineUnavailable(f"status={response.status_code}", operation=operation)

        if response.status_code == 404:
            if allow_404:
                return None
            raise NotFound(f"status=404 path={path}", operation=operation)

        if response.status_code >= 400:
            raise self._classify(response, operation=operation)

        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            logger.error("Native engine returned non-JSON [%s]", operation)
            raise EngineUnavailable("non-JSON response", operation=operation)

    @staticmethod
    def _classify(response, *, operation: str) -> MailEngineError:
        """
        Turn a 4xx into the typed error product code catches.

        The engine's message is used for classification and is safe to carry: it
        is authored by our own API, describes the problem and never echoes a
        password (`validation.password` raises messages about the field, never
        the value).
        """
        try:
            message = (response.json() or {}).get("error", "")
        except ValueError:
            message = response.text[:200]
        lowered = str(message).lower()
        if "quota" in lowered:
            return QuotaExceeded(str(message), operation=operation)
        return Rejected(str(message) or f"status={response.status_code}", operation=operation)

    def _unavailable(self, operation: str):
        phase = _LATER[operation]
        return EngineCapabilityMissing(
            f"{operation} is not implemented by the Native Engine yet ({phase})",
            operation=operation,
        )

    # ── Domains ─────────────────────────────────────────────────────────────

    def ensure_domain(self, spec: DomainSpec) -> None:
        self._request("POST", "/v1/domains/ensure", json={
            "name": spec.name,
            "dkim_selector": spec.dkim_selector,
            "max_mailboxes": spec.max_mailboxes,
            "max_aliases": spec.max_aliases,
            "default_quota_mb": spec.default_quota_mb,
            "max_quota_mb": spec.max_quota_mb,
            "total_quota_mb": spec.total_quota_mb,
            "active": spec.active,
        }, operation="ensure_domain")

    def set_domain_active(self, domain_name: str, active: bool) -> None:
        self._request("POST", "/v1/domains/set-active",
                      json={"name": domain_name, "active": active},
                      operation="set_domain_active")

    def delete_domain(self, domain_name: str) -> None:
        # Idempotent by contract: the engine treats an absent domain as the
        # desired end state, so there is nothing to tolerate here.
        self._request("POST", "/v1/domains/delete", json={"name": domain_name},
                      operation="delete_domain")

    def list_domains(self) -> list[EngineDomain]:
        data = self._request("GET", "/v1/domains", operation="list_domains") or {}
        return [EngineDomain(name=d["name"], active=bool(d["active"]))
                for d in data.get("domains", [])]

    # ── Mailboxes ───────────────────────────────────────────────────────────

    def ensure_mailbox(self, spec: MailboxSpec, password: str = "") -> None:
        payload = {
            "address": spec.address,
            "domain": spec.domain,
            "display_name": spec.display_name,
            "quota_mb": spec.quota_mb,
            "active": spec.active,
        }
        # Omitted entirely when absent, rather than sent as "". The engine treats
        # a missing password as "keep the existing credential", which is the
        # retry guarantee the contract requires.
        if password:
            payload["password"] = password
        self._request("POST", "/v1/mailboxes/ensure", json=payload, operation="ensure_mailbox")

    def set_mailbox_active(self, address: str, active: bool) -> None:
        self._request("POST", "/v1/mailboxes/set-active",
                      json={"address": address, "active": active},
                      operation="set_mailbox_active")

    def set_mailbox_password(self, address: str, password: str) -> None:
        if not password:
            # Refused before the request is built, so an empty credential never
            # travels anywhere. The contract raises Rejected for this.
            raise Rejected("refusing to set an empty mailbox password",
                           operation="set_mailbox_password")
        self._request("POST", "/v1/mailboxes/set-password",
                      json={"address": address, "password": password},
                      operation="set_mailbox_password")

    def set_mailbox_quota(self, address: str, quota_mb: int) -> None:
        self._request("POST", "/v1/mailboxes/set-quota",
                      json={"address": address, "quota_mb": quota_mb},
                      operation="set_mailbox_quota")

    def delete_mailbox(self, address: str) -> None:
        self._request("POST", "/v1/mailboxes/delete", json={"address": address},
                      operation="delete_mailbox")

    def list_mailboxes(self, domain_name: str = "") -> list[EngineMailbox]:
        params = {"domain": domain_name} if domain_name else None
        data = self._request("GET", "/v1/mailboxes", params=params,
                             operation="list_mailboxes") or {}
        return [
            EngineMailbox(address=m["address"], active=bool(m["active"]),
                          quota_mb=int(m["quota_mb"]))
            for m in data.get("mailboxes", [])
        ]

    def get_last_login(self, address: str) -> Optional[str]:
        """
        None until NE4, and that is an honest answer rather than a placeholder:
        the engine genuinely holds no login record yet, because nothing has
        authenticated against it. NE0.21's auth-event endpoint populates this.
        """
        data = self._request("GET", "/v1/mailboxes", params={"address": address},
                             operation="get_last_login", allow_404=True)
        if not data:
            return None
        for mailbox in data.get("mailboxes", []):
            if mailbox["address"] == address:
                return mailbox.get("last_login_at")
        return None

    # ── Aliases and forwarding ──────────────────────────────────────────────

    def ensure_alias(self, spec: AliasSpec) -> None:
        self._request("POST", "/v1/aliases/ensure", json={
            "address": spec.address,
            "destinations": list(spec.destinations),
            "active": spec.active,
        }, operation="ensure_alias")

    def delete_alias(self, address: str) -> None:
        self._request("POST", "/v1/aliases/delete", json={"address": address},
                      operation="delete_alias")

    def ensure_forwarding(self, spec: ForwardingSpec) -> None:
        self._request("POST", "/v1/forwarding/ensure", json={
            "mailbox_address": spec.mailbox_address,
            "destinations": list(spec.destinations),
        }, operation="ensure_forwarding")

    # ── DKIM (DEC-007r: public material only) ───────────────────────────────

    def get_dkim_public_key(self, domain_name: str) -> Optional[DkimKeyInfo]:
        data = self._request("GET", "/v1/dkim", params={"domain": domain_name},
                             operation="get_dkim_public_key", allow_404=True)
        if not data:
            return None
        return self._dkim_info(data)

    def rotate_dkim_key(self, domain_name: str, selector: str = "") -> DkimKeyInfo:
        payload = {"domain": domain_name}
        if selector:
            payload["selector"] = selector
        data = self._request("POST", "/v1/dkim/rotate", json=payload,
                             operation="rotate_dkim_key")
        return self._dkim_info(data)

    def delete_dkim_key(self, domain_name: str) -> None:
        self._request("POST", "/v1/dkim/delete", json={"domain": domain_name},
                      operation="delete_dkim_key")

    @staticmethod
    def _dkim_info(data: dict) -> DkimKeyInfo:
        """
        Build the DTO from named fields only.

        Deliberately NOT `DkimKeyInfo(**data)`. Splatting would carry whatever
        the engine happened to send, so a future API field named `private_key`
        would either land in the DTO or raise at a confusing place. Naming the
        four public fields means private material has nowhere to go even if the
        engine started sending it.
        """
        return DkimKeyInfo(
            selector=data.get("selector", ""),
            public_key=data.get("public_key", ""),
            dns_record_name=data.get("dns_record_name", ""),
            dns_record_value=data.get("dns_record_value", ""),
        )

    # ── Not yet implemented: refuse rather than fabricate ───────────────────

    # ── NE4: operations ─────────────────────────────────────────────────────

    #: The port's window vocabulary is already the engine's. There is no
    #: translation table here on purpose — the Native Engine was built against
    #: this contract, so a mapping layer would only be somewhere for the two to
    #: drift apart.

    def set_mailbox_rate_limit(self, address: str, limit: RateLimit) -> None:
        self._request("POST", "/v1/mailboxes/rate-limit/set", json={"address": address, "messages": limit.messages, "window": limit.window},
                          operation="set_mailbox_rate_limit")

    def get_mailbox_rate_limit(self, address: str) -> Optional[RateLimit]:
        """
        The configured limit, or None when none was ever set.

        None and `RateLimit(messages=0)` are different answers: the first means
        nothing was configured, the second means someone deliberately lifted the
        limit. Collapsing them would lose the distinction the DTO documents.
        """
        data = self._request(
            "GET", "/v1/mailboxes/rate-limit", params={"address": address},
            operation="get_mailbox_rate_limit", allow_404=True,
        )
        if not isinstance(data, dict) or data.get("messages") is None:
            return None
        return RateLimit(messages=int(data["messages"]),
                         window=data.get("window", "hour"))

    def clear_mailbox_rate_limit(self, address: str) -> None:
        self._request("POST", "/v1/mailboxes/rate-limit/clear", json={"address": address},
                          operation="clear_mailbox_rate_limit")

    def get_mailbox_usage(self, address: str) -> Optional[MailboxUsage]:
        """
        Real consumption, measured by Dovecot through the engine.

        The engine converts doveadm's kilobytes to the megabytes this contract
        uses, once, on its side. Doing it here as well is how a value gets
        divided by 1024 twice.
        """
        data = self._request(
            "GET", "/v1/mailboxes/usage", params={"address": address},
            operation="get_mailbox_usage", allow_404=True,
        )
        if not isinstance(data, dict) or not data.get("address"):
            return None
        return MailboxUsage(
            address=data["address"],
            used_mb=int(data.get("used_mb", 0) or 0),
            quota_mb=int(data.get("quota_mb", 0) or 0),
            message_count=(int(data["message_count"])
                           if data.get("message_count") is not None else None),
        )

    def get_queue_status(self) -> list[dict]:
        """
        The engine's real queue, excluding anything held.

        Held mail is quarantine and is reported by `get_quarantine_items`. A
        single list mixing the two would make "deferred" and "awaiting a human"
        indistinguishable, which are opposite operational situations.
        """
        data = self._request("GET", "/v1/queue", operation="get_queue_status")
        items = data.get("items") if isinstance(data, dict) else None
        return items if isinstance(items, list) else []

    def cancel_queue_message(self, engine_message_id: str) -> None:
        """Drop one queued message. Idempotent — already gone is success."""
        self._request("POST", "/v1/queue/cancel", json={"queue_id": engine_message_id},
                          operation="cancel_queue_message")

    def get_quarantine_items(self) -> list[dict]:
        data = self._request("GET", "/v1/quarantine", operation="get_quarantine_items")
        items = data.get("items") if isinstance(data, dict) else None
        return items if isinstance(items, list) else []

    def release_quarantine_item(self, engine_message_id: str) -> None:
        """
        Release one held message to its ORIGINAL recipients.

        The engine releases by un-holding the queue file, so the sender and
        recipients are whatever the message already carried. There is no
        parameter here that could redirect it, and that is deliberate rather
        than incidental.
        """
        self._request("POST", "/v1/quarantine/release", json={"queue_id": engine_message_id},
                          operation="release_quarantine_item")


    # ── Health ──────────────────────────────────────────────────────────────

    def check_health(self) -> EngineHealth:
        """Never raises — an unreachable engine is a reported fact, not an error."""
        try:
            data = self._request("GET", "/ready", operation="check_health") or {}
        except MailEngineError as exc:
            return EngineHealth(reachable=False, detail=exc.log_message)
        except Exception as exc:                       # noqa: BLE001
            return EngineHealth(reachable=False, detail=type(exc).__name__)
        ready = bool(data.get("ready"))
        return EngineHealth(
            reachable=ready,
            detail=f"schema version {data.get('schema_version')}"
                   if ready else "engine schema is not compatible",
        )
