"""
Mail engine adapter interface.

The MailcowAdapter (Phase 6) implements this interface. No other code
should call the mail engine API directly.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class ProvisionResult:
    success: bool
    message: str
    raw: Optional[dict] = None


class MailEngineAdapter(ABC):
    @abstractmethod
    def provision_domain(self, domain) -> ProvisionResult: ...

    @abstractmethod
    def suspend_domain(self, domain) -> ProvisionResult: ...

    @abstractmethod
    def delete_domain(self, domain) -> ProvisionResult: ...

    @abstractmethod
    def provision_mailbox(self, mailbox, password: str = "") -> ProvisionResult: ...

    @abstractmethod
    def disable_mailbox(self, mailbox) -> ProvisionResult: ...

    @abstractmethod
    def enable_mailbox(self, mailbox) -> ProvisionResult: ...

    @abstractmethod
    def delete_mailbox(self, mailbox) -> ProvisionResult: ...

    @abstractmethod
    def update_mailbox_password(self, mailbox, new_password: str) -> ProvisionResult: ...

    @abstractmethod
    def update_mailbox_quota(self, mailbox, quota_mb: int) -> ProvisionResult: ...

    @abstractmethod
    def provision_alias(self, alias) -> ProvisionResult: ...

    @abstractmethod
    def delete_alias(self, alias) -> ProvisionResult: ...

    @abstractmethod
    def update_alias_active(self, alias, active: bool) -> ProvisionResult: ...

    @abstractmethod
    def provision_forwarding(self, rule) -> ProvisionResult: ...

    @abstractmethod
    def delete_forwarding(self, rule) -> ProvisionResult: ...

    @abstractmethod
    def get_dkim_public_key(self, domain_name: str) -> Optional[str]: ...

    @abstractmethod
    def get_queue_status(self) -> list: ...

    @abstractmethod
    def get_quarantine_items(self) -> list: ...

    @abstractmethod
    def cancel_queue_message(self, engine_message_id: str) -> ProvisionResult: ...

    @abstractmethod
    def release_quarantine_item(self, engine_message_id: str) -> ProvisionResult: ...

    def ping(self) -> bool:
        return False
