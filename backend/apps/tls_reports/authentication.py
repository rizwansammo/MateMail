"""RFC 8460: accept SMTP TLS reports only with valid reporting-domain DKIM.

The fetched full message, not any untrusted Authentication-Results header, is
cryptographically verified against public DNS. No raw message/header data is
persisted or logged, and malformed/unavailable keys fail closed.
"""
import re
from email.utils import getaddresses

import dkim

from .parser import HOST_RE, InvalidTlsReport

MAX_DKIM_SIGNATURES = 4
_DOMAIN_TAG = re.compile(r"(?:^|;)\s*d\s*=\s*([a-z0-9.-]+)\s*(?=;|$)", re.IGNORECASE)


def verified_report_sender(raw: bytes, message) -> bool:
    """Require valid DKIM by the report's From domain or its parent domain."""
    from_headers = message.get_all("From", [])
    if len(from_headers) != 1:
        return False
    senders = getaddresses([str(from_headers[0])])
    if len(senders) != 1 or senders[0][1].count("@") != 1:
        return False
    from_domain = senders[0][1].rsplit("@", 1)[1].lower().rstrip(".")
    if not HOST_RE.fullmatch(from_domain):
        return False

    signature_headers = message.get_all("DKIM-Signature", [])
    if not 1 <= len(signature_headers) <= MAX_DKIM_SIGNATURES:
        return False
    for index, value in enumerate(signature_headers):
        match = _DOMAIN_TAG.search(str(value).replace("\r", "").replace("\n", ""))
        if not match:
            continue
        signing_domain = match.group(1).lower().rstrip(".")
        if not HOST_RE.fullmatch(signing_domain):
            continue
        # Signed From may be a subdomain of the signing/reporting domain.
        if from_domain != signing_domain and not from_domain.endswith("." + signing_domain):
            continue
        try:
            if dkim.DKIM(raw).verify(idx=index, timeout=5):
                return True
        except Exception:
            # Treat DNS timeouts, invalid signatures and malformed headers as
            # untrusted reports. Never put externally supplied text in logs.
            continue
    return False


def require_authenticated_report(raw: bytes, message):
    if not verified_report_sender(raw, message):
        raise InvalidTlsReport("TLS-RPT DKIM authentication failed")
