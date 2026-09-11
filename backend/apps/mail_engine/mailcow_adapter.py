"""
Mail Engine adapter — mailcow-orchestrated Postfix/Dovecot/Rspamd implementation.

This module is deliberately the *only* place in MateMail that knows the engine's
REST shape, hostnames, endpoint paths or response format. Upstream product names
appear here because engineers working on this file need them; per DEC-011 they
must not appear in anything a customer can reach.

Everything leaving this module is a MateMail type: a DTO from `dto.py`, or a
typed exception from `errors.py`. Engine response text is logged and discarded,
never returned.
"""
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
)
from .errors import (
    AlreadyExists,
    EngineCapabilityMissing,
    EngineUnavailable,
    MailEngineError,
    NotFound,
    QuotaExceeded,
    Rejected,
)

logger = logging.getLogger(__name__)

#: The engine release this endpoint mapping was verified against, by reading
#: that tag's own OpenAPI specification and source — not from memory. When the
#: engine is upgraded, re-run that comparison before trusting this file.
#: See docs/MAIL_ENGINE.md § "API mapping".
VERIFIED_AGAINST_ENGINE_VERSION = "2026-07b"

_TIMEOUT = 10

# Engine response fragments mapped to MateMail error types. Matched
# case-insensitively against the engine's message. Order matters: first hit wins.
_ERROR_SIGNATURES: tuple[tuple[str, type[MailEngineError]], ...] = (
    ("already exists", AlreadyExists),
    ("duplicate", AlreadyExists),
    ("is already in use", AlreadyExists),
    ("not found", NotFound),
    ("does not exist", NotFound),
    ("unknown domain", NotFound),
    ("unknown mailbox", NotFound),
    ("quota", QuotaExceeded),
    ("exceeds", QuotaExceeded),
    ("max_quota", QuotaExceeded),
    ("mailbox_quota_left", QuotaExceeded),
)


def _classify(message: str, *, operation: str) -> MailEngineError:
    """
    Translate an engine rejection into a MateMail error type.

    The raw message is attached as `technical_detail` for the log and is never
    part of the exception's customer-facing string.
    """
    haystack = (message or "").lower()
    for fragment, error_type in _ERROR_SIGNATURES:
        if fragment in haystack:
            return error_type(message, operation=operation)
    # Understood-and-refused is the safest default for an explicit engine "no".
    return Rejected(message, operation=operation)


class MailcowAdapter(MailEngineAdapter):
    def __init__(self):
        base = getattr(settings, "MAIL_ENGINE_API_URL", "http://localhost:8080")
        self._base = base.rstrip("/")
        self._host = urlparse(self._base).hostname or "mail-engine"
        self._session = requests.Session()
        self._session.headers.update(
            {
                "X-API-Key": getattr(settings, "MAIL_ENGINE_API_KEY", ""),
                "Content-Type": "application/json",
            }
        )

    # ── Transport ───────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, *, json=None, operation: str = ""):
        """
        Perform one engine call.

        Raises EngineUnavailable for transport problems and a classified error
        for an explicit engine rejection. Returns parsed JSON on success.
        """
        operation = operation or f"{method} {path}"
        url = f"{self._base}{path}"
        try:
            response = self._session.request(method, url, json=json, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            # Transport detail (host, port, TLS) is infrastructure. Log it; the
            # exception carries only a MateMail-authored customer message.
            logger.error("Mail engine transport failure [%s]: %s", operation, exc)
            raise EngineUnavailable(f"transport: {exc}", operation=operation) from exc

        if response.status_code in (401, 403):
            logger.error(
                "Mail engine rejected our credentials [%s] status=%s — check MAIL_ENGINE_API_KEY",
                operation, response.status_code,
            )
            raise EngineUnavailable(
                f"auth rejected status={response.status_code}", operation=operation
            )

        if response.status_code >= 500:
            logger.error("Mail engine server error [%s] status=%s", operation, response.status_code)
            raise EngineUnavailable(
                f"status={response.status_code}", operation=operation
            )

        if response.status_code == 404:
            raise NotFound(f"status=404 path={path}", operation=operation)

        if response.status_code >= 400:
            body = response.text[:300]
            logger.warning("Mail engine 4xx [%s] status=%s body=%s", operation, response.status_code, body)
            raise _classify(body, operation=operation)

        try:
            return response.json()
        except ValueError:
            # A success status with an unparseable body is not something the
            # product can act on, but it is not a customer error either.
            logger.error("Mail engine returned non-JSON [%s]", operation)
            raise EngineUnavailable("non-JSON response", operation=operation)

    def _write(self, path: str, payload: dict, *, operation: str,
               tolerate: tuple[type[MailEngineError], ...] = ()) -> None:
        """
        POST a mutation and raise on failure.

        The engine reports application-level failure inside a 200 body as
        `[{"type": "error", "msg": ...}]`, so the body is inspected too.

        `tolerate` lists error types treated as success — used to make the
        `ensure_*` and `delete_*` operations idempotent.
        """
        try:
            data = self._request("POST", path, json=payload, operation=operation)
            self._raise_for_body(data, operation=operation)
        except tolerate as exc:  # type: ignore[misc]
            logger.info("Mail engine [%s] tolerated as idempotent: %r", operation, exc)

    @staticmethod
    def _raise_for_body(data, *, operation: str) -> None:
        """Inspect the engine's `[{type, msg}]` envelope for an embedded failure."""
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("type", "")).lower() in ("error", "danger"):
                message = item.get("msg", "")
                if isinstance(message, (list, tuple)):
                    message = " ".join(str(m) for m in message)
                logger.warning("Mail engine rejected [%s]: %s", operation, message)
                raise _classify(str(message), operation=operation)

    # ── Domains ─────────────────────────────────────────────────────────────

    def ensure_domain(self, spec: DomainSpec) -> None:
        payload = {
            "domain": spec.name,
            "description": "MateMail-managed",
            "aliases": spec.max_aliases,
            "mailboxes": spec.max_mailboxes,
            "defquota": spec.default_quota_mb,
            "maxquota": spec.max_quota_mb,
            "quota": 0,
            "active": "1" if spec.active else "0",
            "relay_all_recipients": "0",
            "relay_unknown_only": "0",
        }
        # Idempotent: an existing domain is reconciled to the spec instead of
        # being treated as a conflict.
        try:
            self._write("/api/v1/add/domain", payload, operation="ensure_domain")
        except AlreadyExists:
            logger.info("Mail engine already holds domain; reconciling to spec")
            self._write(
                "/api/v1/edit/domain",
                {
                    "items": [spec.name],
                    "attr": {
                        "aliases": spec.max_aliases,
                        "mailboxes": spec.max_mailboxes,
                        "defquota": spec.default_quota_mb,
                        "maxquota": spec.max_quota_mb,
                        "active": "1" if spec.active else "0",
                    },
                },
                operation="ensure_domain.reconcile",
            )

    def set_domain_active(self, domain_name: str, active: bool) -> None:
        self._write(
            "/api/v1/edit/domain",
            {"items": [domain_name], "attr": {"active": "1" if active else "0"}},
            operation="set_domain_active",
        )

    # NOTE on HTTP method: the engine's API router rejects anything but POST
    # on every /delete/ endpoint with 405 "only POST method is allowed"
    # (json_api.php @ 2026-07b). These three calls previously used DELETE,
    # which is the RESTful choice and the wrong one here — every delete would
    # have failed against a real engine. The body is a bare JSON array: for a
    # delete the router assigns the whole request body to $_POST['items'].
    def delete_domain(self, domain_name: str) -> None:
        try:
            data = self._request(
                "POST", "/api/v1/delete/domain", json=[domain_name],
                operation="delete_domain",
            )
            self._raise_for_body(data, operation="delete_domain")
        except NotFound:
            logger.info("Mail engine had no such domain to delete — treating as done")

    def list_domains(self) -> list[EngineDomain]:
        data = self._request("GET", "/api/v1/get/domain/all", operation="list_domains")
        rows = data if isinstance(data, list) else []
        out = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("domain_name"):
                continue
            out.append(
                EngineDomain(
                    name=row["domain_name"],
                    active=str(row.get("active", "1")) in ("1", "true", "True"),
                )
            )
        return out

    # ── Mailboxes ───────────────────────────────────────────────────────────

    def ensure_mailbox(self, spec: MailboxSpec, password: str = "") -> None:
        payload = {
            "local_part": spec.local_part,
            "domain": spec.domain,
            "name": spec.display_name,
            "quota": str(spec.quota_mb),
            "active": "1" if spec.active else "0",
            "force_pw_update": "0",
            "tls_enforce_in": "0",
            "tls_enforce_out": "0",
        }
        if password:
            payload["password"] = password
            payload["password2"] = password

        try:
            self._write("/api/v1/add/mailbox", payload, operation="ensure_mailbox")
        except AlreadyExists:
            # Idempotent path. Note the password is only included when supplied,
            # so a retry without one cannot clear a working credential.
            attr = {
                "name": spec.display_name,
                "quota": str(spec.quota_mb),
                "active": "1" if spec.active else "0",
            }
            if password:
                attr["password"] = password
                attr["password2"] = password
            logger.info("Mail engine already holds mailbox; reconciling to spec")
            self._write(
                "/api/v1/edit/mailbox",
                {"items": [spec.address], "attr": attr},
                operation="ensure_mailbox.reconcile",
            )

    def set_mailbox_active(self, address: str, active: bool) -> None:
        self._write(
            "/api/v1/edit/mailbox",
            {"items": [address], "attr": {"active": "1" if active else "0"}},
            operation="set_mailbox_active",
        )

    def set_mailbox_password(self, address: str, password: str) -> None:
        if not password:
            raise Rejected("empty password", operation="set_mailbox_password")
        self._write(
            "/api/v1/edit/mailbox",
            {"items": [address], "attr": {"password": password, "password2": password}},
            operation="set_mailbox_password",
        )

    def set_mailbox_quota(self, address: str, quota_mb: int) -> None:
        self._write(
            "/api/v1/edit/mailbox",
            {"items": [address], "attr": {"quota": str(quota_mb)}},
            operation="set_mailbox_quota",
        )

    def delete_mailbox(self, address: str) -> None:
        try:
            data = self._request(
                "POST", "/api/v1/delete/mailbox", json=[address],
                operation="delete_mailbox",
            )
            self._raise_for_body(data, operation="delete_mailbox")
        except NotFound:
            logger.info("Mail engine had no such mailbox to delete — treating as done")

    def list_mailboxes(self, domain_name: str = "") -> list[EngineMailbox]:
        # Domain scoping is /get/mailbox/all/{domain}; /get/mailbox/{value}
        # addresses a single mailbox, so it must not be used for a domain.
        path = f"/api/v1/get/mailbox/all/{domain_name}" if domain_name else "/api/v1/get/mailbox/all"
        data = self._request("GET", path, operation="list_mailboxes")
        rows = data if isinstance(data, list) else []
        out = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("username"):
                continue
            out.append(
                EngineMailbox(
                    address=row["username"],
                    active=str(row.get("active", "1")) in ("1", "true", "True"),
                    quota_mb=int(row.get("quota", 0) or 0) // (1024 * 1024),
                )
            )
        return out

    def get_mailbox_usage(self, address: str) -> Optional[MailboxUsage]:
        try:
            data = self._request(
                "GET", f"/api/v1/get/mailbox/{address}", operation="get_mailbox_usage"
            )
        except NotFound:
            return None
        row = data[0] if isinstance(data, list) and data else data
        if not isinstance(row, dict) or not row.get("username"):
            return None
        return MailboxUsage(
            address=row["username"],
            used_mb=int(row.get("quota_used", 0) or 0) // (1024 * 1024),
            quota_mb=int(row.get("quota", 0) or 0) // (1024 * 1024),
            message_count=int(row["messages"]) if row.get("messages") is not None else None,
        )

    def get_last_login(self, address: str) -> Optional[str]:
        try:
            data = self._request(
                "GET", f"/api/v1/get/mailbox/{address}", operation="get_last_login"
            )
        except NotFound:
            return None
        row = data[0] if isinstance(data, list) and data else data
        if not isinstance(row, dict):
            return None
        value = row.get("last_imap_login") or row.get("last_pop3_login") or ""
        return str(value) or None

    # ── Aliases and forwarding ──────────────────────────────────────────────

    def _alias_id(self, address: str) -> Optional[int]:
        try:
            data = self._request(
                "GET", f"/api/v1/get/alias/{address}", operation="alias_lookup"
            )
        except NotFound:
            return None
        row = data[0] if isinstance(data, list) and data else data
        if isinstance(row, dict) and row.get("id"):
            return row["id"]
        return None

    def _set_alias(self, address: str, destinations: tuple[str, ...], active: bool,
                   *, operation: str) -> None:
        """Upsert one alias to exactly `destinations`."""
        goto = ",".join(destinations)
        existing = self._alias_id(address)
        if existing is not None:
            self._write(
                "/api/v1/edit/alias",
                {"items": [existing], "attr": {"goto": goto, "active": "1" if active else "0"}},
                operation=f"{operation}.update",
            )
            return
        self._write(
            "/api/v1/add/alias",
            {"address": address, "goto": goto, "active": "1" if active else "0"},
            operation=f"{operation}.create",
        )

    def ensure_alias(self, spec: AliasSpec) -> None:
        self._set_alias(spec.address, spec.destinations, spec.active, operation="ensure_alias")

    def delete_alias(self, address: str) -> None:
        existing = self._alias_id(address)
        if existing is None:
            logger.info("Mail engine had no such alias to delete — treating as done")
            return
        try:
            data = self._request(
                "POST", "/api/v1/delete/alias", json=[existing],
                operation="delete_alias",
            )
            self._raise_for_body(data, operation="delete_alias")
        except NotFound:
            logger.info("Mail engine alias vanished before deletion — treating as done")

    def ensure_forwarding(self, spec: ForwardingSpec) -> None:
        """
        Apply the caller's final destination set verbatim.

        No rule-set or keep-copy logic here: `apps.forwarding.services` resolved
        all of that before building the spec.
        """
        if spec.is_empty:
            self.delete_alias(spec.mailbox_address)
            return
        self._set_alias(
            spec.mailbox_address, spec.destinations, True, operation="ensure_forwarding"
        )

    # ── DKIM — public material only (DEC-007r) ──────────────────────────────

    #: Response fields that carry, or could carry, private key material. The
    #: engine's DKIM read returns a `privkey` field on every call — empty under
    #: a default configuration, but populated if an operator ever sets
    #: SHOW_DKIM_PRIV_KEYS. Verified by reading the engine's own
    #: functions.dkim.inc.php at 2026-07b.
    _PRIVATE_DKIM_FIELDS = ("privkey", "private_key", "priv_key", "key")

    def _dkim_info(self, domain_name: str, payload) -> Optional[DkimKeyInfo]:
        """
        Build public DKIM material, discarding anything private.

        DEC-007r says the private key never crosses this boundary. That is not
        left to the engine's configuration: the read path strips private fields
        unconditionally, so a mailcow instance running with
        SHOW_DKIM_PRIV_KEYS=y cannot leak through MateMail. DkimKeyInfo has no
        field capable of carrying one either, which is the second line of
        defence.
        """
        row = payload[0] if isinstance(payload, list) and payload else payload
        if not isinstance(row, dict):
            return None

        # Drop private material before anything else touches the row, and say
        # so loudly — a populated privkey means the engine is misconfigured.
        for field in self._PRIVATE_DKIM_FIELDS:
            if row.get(field):
                logger.error(
                    "Mail engine returned private DKIM material in %r for %s — "
                    "discarded. Set SHOW_DKIM_PRIV_KEYS=n on the engine.",
                    field, domain_name,
                )
            row.pop(field, None)

        public_key = row.get("pubkey") or row.get("dkim_txt") or ""
        if not public_key:
            return None
        selector = row.get("dkim_selector") or "mm1"
        return DkimKeyInfo(
            selector=selector,
            public_key=public_key,
            dns_record_name=f"{selector}._domainkey.{domain_name}",
            dns_record_value=row.get("dkim_txt") or f"v=DKIM1;k=rsa;p={public_key}",
        )

    def get_dkim_public_key(self, domain_name: str) -> Optional[DkimKeyInfo]:
        try:
            data = self._request(
                "GET", f"/api/v1/get/dkim/{domain_name}", operation="get_dkim_public_key"
            )
        except NotFound:
            return None
        return self._dkim_info(domain_name, data)

    def rotate_dkim_key(self, domain_name: str, selector: str = "") -> DkimKeyInfo:
        """
        Ask the engine to generate a fresh keypair. The private half stays inside
        the engine — we read back only the public record.
        """
        selector = selector or "mm1"
        self._write(
            "/api/v1/add/dkim",
            {"domains": domain_name, "dkim_selector": selector, "key_size": 2048},
            operation="rotate_dkim_key",
        )
        info = self.get_dkim_public_key(domain_name)
        if info is None:
            raise MailEngineError(
                "engine reported no DKIM key after rotation", operation="rotate_dkim_key"
            )
        return info

    # ── Queue and quarantine ────────────────────────────────────────────────

    def get_queue_status(self) -> list[dict]:
        data = self._request("GET", "/api/v1/get/mailq/all", operation="get_queue_status")
        return data if isinstance(data, list) else []

    def get_quarantine_items(self) -> list[dict]:
        data = self._request(
            "GET", "/api/v1/get/quarantine/all", operation="get_quarantine_items"
        )
        return data if isinstance(data, list) else []

    def cancel_queue_message(self, engine_message_id: str) -> None:
        """
        Drop one queued message.

        The body is a bare array of queue ids. The engine's router assigns the
        whole request body to $_POST['items'] and then calls
        `mailq('delete', array('qid' => $items))` — so `{"id": [...]}`, which
        this previously sent, arrived as a nested dict under `qid` and matched
        no queue id at all. Verified in json_api.php and functions.mailq.inc.php
        at 2026-07b.
        """
        try:
            data = self._request(
                "POST", "/api/v1/delete/mailq", json=[engine_message_id],
                operation="cancel_queue_message",
            )
            self._raise_for_body(data, operation="cancel_queue_message")
        except NotFound:
            logger.info("Queued message already gone — treating cancel as done")

    def release_quarantine_item(self, engine_message_id: str) -> None:
        """
        Not available at the pinned engine version.

        This previously POSTed to `/api/v1/set/quarantine/release`, which does
        not exist in mailcow 2026-07b — its API defines only
        `GET /api/v1/get/quarantine/all` and
        `POST /api/v1/edit/quarantine_notification`. A call would have 404'd,
        been classified as NotFound, and been swallowed by the handler below as
        "already actioned": the operator would have seen a success and the
        message would have stayed in quarantine.

        Raising is the honest behaviour. Releasing quarantined mail is a P5
        concern (quarantine is an empty shell today — nothing writes
        QuarantineMessage), and when it is built it needs either a newer engine
        API or a direct Rspamd path, decided then rather than guessed now.
        """
        raise EngineCapabilityMissing(
            "mailcow 2026-07b exposes no quarantine release endpoint",
            operation="release_quarantine_item",
        )

    # ── Health ──────────────────────────────────────────────────────────────

    def check_health(self) -> EngineHealth:
        """Never raises: health checks report, they do not fail the caller."""
        try:
            self._request(
                "GET", "/api/v1/get/status/containers", operation="check_health"
            )
            return EngineHealth(reachable=True, detail="ok")
        except MailEngineError as exc:
            return EngineHealth(reachable=False, detail=exc.log_message)
