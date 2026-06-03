"""
Concrete mailcow REST API adapter.

All HTTP calls go through _post() / _delete() / _get() helpers that handle
error normalization. Never call the mailcow API from anywhere else.
"""
import logging
from typing import Optional

import requests
from django.conf import settings

from .adapter import MailEngineAdapter, ProvisionResult

logger = logging.getLogger(__name__)
_TIMEOUT = 10

# Terms that must never appear in mail_engine_error messages shown to tenant admins.
# The log receives the raw error; only the sanitized version reaches the DB/API.
_LEAKY_TERMS: dict[str, str] = {
    "mailcow":      "the mail server",
    "Postfix":      "SMTP",
    "Dovecot":      "IMAP",
    "Rspamd":       "spam filter",
    "MariaDB":      "the database",
    "MySQL":        "the database",
}


def _sanitize(msg: str) -> str:
    """Strip internal tool names from error messages before storing them."""
    for term, replacement in _LEAKY_TERMS.items():
        msg = msg.replace(term, replacement)
    return msg


def _parse_response(data) -> tuple[bool, str]:
    """Normalize mailcow's [{type, msg}] response list into (success, message)."""
    if isinstance(data, list) and data:
        item = data[0]
        success = item.get("type") in ("success", "info")
        return success, _sanitize(str(item.get("msg", "")))
    return True, "ok"


class MailcowAdapter(MailEngineAdapter):
    def __init__(self):
        base = getattr(settings, "MAIL_ENGINE_API_URL", "http://localhost:8080").rstrip("/")
        key = getattr(settings, "MAIL_ENGINE_API_KEY", "")
        self._base = base
        self._session = requests.Session()
        self._session.headers.update({
            "X-API-Key": key,
            "Content-Type": "application/json",
        })

    def _post(self, path: str, payload: dict) -> ProvisionResult:
        try:
            r = self._session.post(f"{self._base}{path}", json=payload, timeout=_TIMEOUT)
            r.raise_for_status()
            success, msg = _parse_response(r.json())
            return ProvisionResult(success=success, message=msg, raw=r.json())
        except requests.RequestException as exc:
            logger.error("mailcow POST %s failed: %s", path, exc)
            return ProvisionResult(success=False, message="Mail service connection error.")

    def _delete(self, path: str, items: list) -> ProvisionResult:
        try:
            r = self._session.delete(f"{self._base}{path}", json=items, timeout=_TIMEOUT)
            r.raise_for_status()
            success, msg = _parse_response(r.json())
            return ProvisionResult(success=success, message=msg, raw=r.json())
        except requests.RequestException as exc:
            logger.error("mailcow DELETE %s failed: %s", path, exc)
            return ProvisionResult(success=False, message="Mail service connection error.")

    def _get(self, path: str):
        try:
            r = self._session.get(f"{self._base}{path}", timeout=_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as exc:
            logger.error("mailcow GET %s failed: %s", path, exc)
            return None

    # ── Domain management ────────────────────────────────────────────────────

    def provision_domain(self, domain) -> ProvisionResult:
        result = self._post("/api/v1/add/domain", {
            "domain": domain.domain,
            "description": f"MateMail-managed",
            "aliases": 400,
            "mailboxes": 10,
            "defquota": 3072,
            "maxquota": 51200,
            "quota": 0,
            "active": "1",
            "relay_all_recipients": "0",
            "relay_unknown_only": "0",
        })
        if not result.success:
            return result

        # Push DKIM private key if available
        if domain.dkim_private_key:
            dkim = self._post("/api/v1/add/dkim", {
                "dkim_selector": domain.dkim_selector,
                "domain": domain.domain,
                "private_key": domain.dkim_private_key,
            })
            if not dkim.success:
                logger.warning("DKIM provision failed for %s: %s", domain.domain, dkim.message)
                return ProvisionResult(
                    success=True,
                    message=f"Domain provisioned; DKIM warning: {dkim.message}",
                    raw=result.raw,
                )

        return result

    def suspend_domain(self, domain) -> ProvisionResult:
        return self._post("/api/v1/edit/domain", {
            "items": [domain.domain],
            "attr": {"active": "0"},
        })

    def delete_domain(self, domain) -> ProvisionResult:
        return self._delete("/api/v1/delete/domain", [domain.domain])

    # ── Mailbox management ───────────────────────────────────────────────────

    def provision_mailbox(self, mailbox, password: str = "") -> ProvisionResult:
        if not password:
            return ProvisionResult(success=False, message="Password is required to provision a mailbox.")
        return self._post("/api/v1/add/mailbox", {
            "local_part": mailbox.local_part,
            "domain": mailbox.domain.domain,
            "name": mailbox.full_name,
            "quota": str(mailbox.quota_mb),
            "password": password,
            "password2": password,
            "active": "1",
            "force_pw_update": "0",
            "tls_enforce_in": "0",
            "tls_enforce_out": "0",
        })

    def disable_mailbox(self, mailbox) -> ProvisionResult:
        return self._post("/api/v1/edit/mailbox", {
            "items": [mailbox.email],
            "attr": {"active": "0"},
        })

    def enable_mailbox(self, mailbox) -> ProvisionResult:
        return self._post("/api/v1/edit/mailbox", {
            "items": [mailbox.email],
            "attr": {"active": "1"},
        })

    def delete_mailbox(self, mailbox) -> ProvisionResult:
        return self._delete("/api/v1/delete/mailbox", [mailbox.email])

    def update_mailbox_password(self, mailbox, new_password: str) -> ProvisionResult:
        return self._post("/api/v1/edit/mailbox", {
            "items": [mailbox.email],
            "attr": {"password": new_password, "password2": new_password},
        })

    def update_mailbox_quota(self, mailbox, quota_mb: int) -> ProvisionResult:
        return self._post("/api/v1/edit/mailbox", {
            "items": [mailbox.email],
            "attr": {"quota": str(quota_mb)},
        })

    # ── Aliases / Forwarding ─────────────────────────────────────────────────

    def provision_alias(self, alias) -> ProvisionResult:
        destination = (
            alias.destination_mailbox.email
            if alias.destination_mailbox
            else alias.destination_address
        )
        return self._post("/api/v1/add/alias", {
            "address": alias.source_address,
            "goto": destination,
            "active": "1" if alias.status == "active" else "0",
        })

    def delete_alias(self, alias) -> ProvisionResult:
        return self._delete("/api/v1/delete/alias", [alias.source_address])

    def update_alias_active(self, alias, active: bool) -> ProvisionResult:
        data = self._get(f"/api/v1/get/alias/{alias.source_address}")
        if not data or not data.get("id"):
            return self.provision_alias(alias)
        return self._post("/api/v1/edit/alias", {
            "items": [data["id"]],
            "attr": {"active": "1" if active else "0"},
        })

    def provision_forwarding(self, rule) -> ProvisionResult:
        from apps.forwarding.models import ForwardingRule, ForwardingStatus
        active_rules = list(
            ForwardingRule.objects
            .filter(source_mailbox=rule.source_mailbox, status=ForwardingStatus.ACTIVE)
            .select_related("source_mailbox")
        )
        if not active_rules:
            return ProvisionResult(success=True, message="No active forwarding rules.")

        goto_list = [r.destination_email for r in active_rules]
        source_email = rule.source_mailbox.email
        if any(r.keep_copy for r in active_rules) and source_email not in goto_list:
            goto_list.append(source_email)

        existing = self._get(f"/api/v1/get/alias/{source_email}")
        if existing and existing.get("id"):
            return self._post("/api/v1/edit/alias", {
                "items": [existing["id"]],
                "attr": {"goto": ",".join(goto_list), "active": "1"},
            })
        return self._post("/api/v1/add/alias", {
            "address": source_email,
            "goto": ",".join(goto_list),
            "active": "1",
        })

    def delete_forwarding(self, rule) -> ProvisionResult:
        from apps.forwarding.models import ForwardingRule, ForwardingStatus
        remaining = list(
            ForwardingRule.objects
            .filter(source_mailbox=rule.source_mailbox, status=ForwardingStatus.ACTIVE)
            .exclude(pk=rule.pk)
            .select_related("source_mailbox")
        )
        source_email = rule.source_mailbox.email
        existing = self._get(f"/api/v1/get/alias/{source_email}")
        if not existing or not existing.get("id"):
            return ProvisionResult(success=True, message="No forwarding alias to remove.")

        if not remaining:
            return self._delete("/api/v1/delete/alias", [source_email])

        goto_list = [r.destination_email for r in remaining]
        if any(r.keep_copy for r in remaining) and source_email not in goto_list:
            goto_list.append(source_email)

        return self._post("/api/v1/edit/alias", {
            "items": [existing["id"]],
            "attr": {"goto": ",".join(goto_list)},
        })

    # ── Utilities ────────────────────────────────────────────────────────────

    def get_dkim_public_key(self, domain_name: str) -> Optional[str]:
        data = self._get(f"/api/v1/get/dkim/{domain_name}")
        if data:
            return data.get("dkim_txt")
        return None

    def get_queue_status(self) -> list:
        data = self._get("/api/v1/get/mailq/")
        if isinstance(data, list):
            return data
        return []

    def get_quarantine_items(self) -> list:
        data = self._get("/api/v1/get/quarantine/all")
        if isinstance(data, list):
            return data
        return []

    def cancel_queue_message(self, engine_message_id: str) -> ProvisionResult:
        return self._post("/api/v1/delete/mailq/", {"id": [engine_message_id]})

    def release_quarantine_item(self, engine_message_id: str) -> ProvisionResult:
        return self._post("/api/v1/set/quarantine/release", {"item": engine_message_id, "action": "deliver"})

    def ping(self) -> bool:
        try:
            r = self._session.get(
                f"{self._base}/api/v1/get/status/containers", timeout=5
            )
            return r.ok
        except requests.RequestException:
            return False
