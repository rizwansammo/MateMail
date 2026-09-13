"""
Adapter factory — returns the configured MailEngineAdapter singleton.

Configured via MAIL_ENGINE_ADAPTER in settings:
  "stub"    → StubAdapter                (default; safe for local dev and CI)
  "mailcow" → MailcowAdapter             (production)
  "native"  → NativeMailEngineAdapter    (NE2: implemented and tested, NOT selected)

"native" is selectable but is not production. mailcow remains the live engine
until NE5 authorises the swap; this entry exists so the adapter can be exercised
end to end without a settings patch that a future reader would mistake for a
migration having happened.
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
        elif name == "native":
            from .native_adapter import NativeMailEngineAdapter
            _adapter = NativeMailEngineAdapter()
        else:
            from .stub_adapter import StubAdapter
            _adapter = StubAdapter()
    return _adapter


def reset_adapter() -> None:
    """Force re-initialisation — useful in tests."""
    global _adapter
    with _lock:
        _adapter = None
