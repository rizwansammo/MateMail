"""Mail Engine adapter factory.

Production uses the MateMail Native Engine. The stub adapter remains available
for local development and CI where no mail infrastructure exists.
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
        name = getattr(settings, "MAIL_ENGINE_ADAPTER", "stub").lower().strip()
        if name == "native":
            from .native_adapter import NativeMailEngineAdapter
            _adapter = NativeMailEngineAdapter()
        elif name == "stub":
            from .stub_adapter import StubAdapter
            _adapter = StubAdapter()
        else:
            # An UNRECOGNISED name is a configuration error, not a reason to
            # pick the stub.
            #
            # The stub accepts every operation and reports success without an
            # engine, so falling back to it would turn a typo — `natve`,
            # `Native `, a stale value — into a deployment that provisions
            # nothing and says everything worked. Customers would see mailboxes
            # that do not exist. Refusing here is the only safe answer, and it
            # fails at the first call rather than silently forever.
            from django.core.exceptions import ImproperlyConfigured
            raise ImproperlyConfigured(
                f"MAIL_ENGINE_ADAPTER={name!r} is not a known adapter. "
                f"Valid values: 'stub', 'native'."
            )
    return _adapter


def reset_adapter() -> None:
    """Force re-initialisation — useful in tests."""
    global _adapter
    with _lock:
        _adapter = None
