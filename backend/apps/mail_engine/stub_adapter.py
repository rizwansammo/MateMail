"""
Stub adapter for local development and testing.

Returns success for every operation without contacting any external service.
Set MAIL_ENGINE_ADAPTER=stub in .env to use this adapter.
"""
import logging
from typing import Optional

from .adapter import MailEngineAdapter, ProvisionResult

logger = logging.getLogger(__name__)


class StubAdapter(MailEngineAdapter):
    """
    No-op adapter that returns ProvisionResult(success=True) for every call.
    Safe to run without a real mail engine — used in local dev and CI.
    """

    def _ok(self, label: str, **kwargs) -> ProvisionResult:
        detail = " ".join(f"{k}={v}" for k, v in kwargs.items())
        logger.debug("[stub] %s %s", label, detail)
        return ProvisionResult(success=True, message=f"[stub] {label}")

    def provision_domain(self, domain) -> ProvisionResult:
        return self._ok("provision_domain", domain=domain.domain)

    def suspend_domain(self, domain) -> ProvisionResult:
        return self._ok("suspend_domain", domain=domain.domain)

    def delete_domain(self, domain) -> ProvisionResult:
        return self._ok("delete_domain", domain=domain.domain)

    def provision_mailbox(self, mailbox, password: str = "") -> ProvisionResult:
        return self._ok("provision_mailbox", email=mailbox.email)

    def disable_mailbox(self, mailbox) -> ProvisionResult:
        return self._ok("disable_mailbox", email=mailbox.email)

    def enable_mailbox(self, mailbox) -> ProvisionResult:
        return self._ok("enable_mailbox", email=mailbox.email)

    def delete_mailbox(self, mailbox) -> ProvisionResult:
        return self._ok("delete_mailbox", email=mailbox.email)

    def update_mailbox_password(self, mailbox, new_password: str) -> ProvisionResult:
        return self._ok("update_mailbox_password", email=mailbox.email)

    def update_mailbox_quota(self, mailbox, quota_mb: int) -> ProvisionResult:
        return self._ok("update_mailbox_quota", email=mailbox.email, quota=quota_mb)

    def provision_alias(self, alias) -> ProvisionResult:
        return self._ok("provision_alias", address=alias.source_address)

    def delete_alias(self, alias) -> ProvisionResult:
        return self._ok("delete_alias", address=alias.source_address)

    def update_alias_active(self, alias, active: bool) -> ProvisionResult:
        return self._ok("update_alias_active", address=alias.source_address, active=active)

    def provision_forwarding(self, rule) -> ProvisionResult:
        return self._ok("provision_forwarding", mailbox=rule.source_mailbox.email)

    def delete_forwarding(self, rule) -> ProvisionResult:
        return self._ok("delete_forwarding", mailbox=rule.source_mailbox.email)

    def get_dkim_public_key(self, domain_name: str) -> Optional[str]:
        return None

    def get_queue_status(self) -> list:
        return []

    def get_quarantine_items(self) -> list:
        return []

    def cancel_queue_message(self, engine_message_id: str) -> ProvisionResult:
        return self._ok("cancel_queue_message", id=engine_message_id)

    def release_quarantine_item(self, engine_message_id: str) -> ProvisionResult:
        return self._ok("release_quarantine_item", id=engine_message_id)

    def ping(self) -> bool:
        return True
