"""
Adapter factory — returns the configured MailEngineAdapter singleton.

Configured via MAIL_ENGINE_ADAPTER in settings:
  "stub"    → StubAdapter    (default; safe for local dev and CI)
  "mailcow" → MailcowAdapter (production)
"""
import threading
from django.conf import settings
from .adapter import MailEngineAdapter

_lock = threading.Lock()
_adapter: MailEngineAdapter | None = None


def get_adapter() -> MailEngineAdapter:
    global _adapter
    if _adapter is not None:
        return _adapter
    with _lock:
        if _adapter is not None:
            return _adapter
        name = getattr(settings, "MAIL_ENGINE_ADAPTER", "stub").lower()
        if name == "mailcow":
            from .mailcow_adapter import MailcowAdapter
            _adapter = MailcowAdapter()
        else:
            from .stub_adapter import StubAdapter
            _adapter = StubAdapter()
    return _adapter


def reset_adapter() -> None:
    """Force re-initialisation — useful in tests."""
    global _adapter
    with _lock:
        _adapter = None
