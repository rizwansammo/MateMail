"""Verify the mandatory RFC 8460 report-domain and report-submitter headers.

These headers are untrusted until the complete MIME message has passed DKIM.
Only the authenticated JSON and its header metadata are checked; raw reports,
IP addresses and sender-provided text are never persisted or logged.
"""
import json
from email.utils import getaddresses

from .parser import HOST_RE, InvalidTlsReport


def _required_domain(message, name: str) -> str:
    values = message.get_all(name, [])
    if len(values) != 1:
        raise InvalidTlsReport("Missing or duplicate TLS report header")
    domain = str(values[0]).strip().lower().rstrip(".")
    if not HOST_RE.fullmatch(domain):
        raise InvalidTlsReport("Invalid TLS report header domain")
    return domain


def _contact_domain(json_bytes: bytes) -> str:
    # Called after the size-bounded, duplicate-key-rejecting JSON parser has
    # accepted this document.
    contact = json.loads(json_bytes)["contact-info"]
    if not isinstance(contact, str) or len(contact) > 320:
        raise InvalidTlsReport("Invalid report contact")
    value = contact.strip()
    if value.lower().startswith("mailto:"):
        value = value[7:]
    parsed = getaddresses([value])
    if len(parsed) != 1 or parsed[0][1].count("@") != 1:
        raise InvalidTlsReport("Invalid report contact email")
    domain = parsed[0][1].rsplit("@", 1)[1].lower().rstrip(".")
    if not HOST_RE.fullmatch(domain):
        raise InvalidTlsReport("Invalid report contact domain")
    return domain


def validate_report_headers(message, json_bytes: bytes, policies: tuple):
    reported_domain = _required_domain(message, "TLS-Report-Domain")
    submitter = _required_domain(message, "TLS-Report-Submitter")
    if submitter != _contact_domain(json_bytes):
        raise InvalidTlsReport("TLS report submitter does not match contact")
    if any(p.domain != reported_domain for p in policies):
        raise InvalidTlsReport("TLS report domain does not match JSON policy")
