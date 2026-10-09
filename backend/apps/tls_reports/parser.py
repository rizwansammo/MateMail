"""Bounded, strict RFC 8460 TLS-RPT JSON/GZIP parser. Never retain raw payloads."""
import gzip
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

MAX_ARCHIVE = 4 * 1024 * 1024
MAX_DOCUMENT = 2 * 1024 * 1024
MAX_PARTS = 8
MAX_POLICIES = 32
MAX_DETAILS = 256
MAX_COUNT = 10_000_000_000
HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
KIND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class InvalidTlsReport(ValueError):
    """Expected rejection; never log untrusted input."""


@dataclass(frozen=True)
class ParsedPolicy:
    digest: str
    domain: str
    reporter: str
    report_id: str
    kind: str
    start: datetime
    end: datetime
    successes: int
    failures: int
    buckets: tuple[tuple[str, int], ...]


def _object(value):
    if not isinstance(value, dict):
        raise InvalidTlsReport("Expected object")
    return value


def _string(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise InvalidTlsReport("Invalid text field")
    return value.strip()


def _count(value):
    if type(value) is not int or not 0 <= value <= MAX_COUNT:
        raise InvalidTlsReport("Invalid session count")
    return value


def _date(value):
    try:
        ts = datetime.fromisoformat(_string(value, 48).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            raise ValueError
        ts = ts.astimezone(timezone.utc)
        if not 2000 <= ts.year <= 2100:
            raise ValueError
        return ts
    except (ValueError, OverflowError) as exc:
        raise InvalidTlsReport("Invalid date") from exc


def _unique(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise InvalidTlsReport("Duplicate field")
        result[k] = v
    return result


def _nonfinite(_):
    raise InvalidTlsReport("Nonfinite value")


def parse_json(raw: bytes) -> tuple[ParsedPolicy, ...]:
    if not raw or len(raw) > MAX_DOCUMENT:
        raise InvalidTlsReport("Document exceeds size cap")
    try:
        root = _object(json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                                  parse_constant=_nonfinite))
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise InvalidTlsReport("Malformed TLS-RPT JSON") from exc
    reporter = _string(root.get("organization-name"), 253)
    report_id = _string(root.get("report-id"), 255)
    window = _object(root.get("date-range"))
    start = _date(window.get("start-datetime"))
    end = _date(window.get("end-datetime"))
    if end < start or (end - start).total_seconds() > 31 * 86400:
        raise InvalidTlsReport("Invalid date range")
    policies = root.get("policies")
    if not isinstance(policies, list) or not 1 <= len(policies) <= MAX_POLICIES:
        raise InvalidTlsReport("Invalid policy count")
    parsed = []
    for index, item in enumerate(policies):
        policy = _object(_object(item).get("policy"))
        domain = _string(policy.get("policy-domain"), 253).lower().rstrip(".")
        if not HOST_RE.fullmatch(domain):
            raise InvalidTlsReport("Invalid policy domain")
        kind = _string(policy.get("policy-type"), 32).lower()
        if kind not in ("sts", "tlsa", "no-policy-found"):
            raise InvalidTlsReport("Invalid policy type")
        summary = _object(item.get("summary"))
        successes = _count(summary.get("total-successful-session-count"))
        failures = _count(summary.get("total-failure-session-count"))
        details = item.get("failure-details", [])
        if not isinstance(details, list) or len(details) > MAX_DETAILS:
            raise InvalidTlsReport("Too many failure details")
        counts = {}
        for entry in details:
            entry = _object(entry)
            name = _string(entry.get("result-type"), 64)
            if not KIND_RE.fullmatch(name):
                raise InvalidTlsReport("Invalid failure type")
            count = _count(entry.get("failed-session-count"))
            counts[name] = counts.get(name, 0) + count
            if counts[name] > MAX_COUNT:
                raise InvalidTlsReport("Excessive failure count")
        if sum(counts.values()) > failures:
            raise InvalidTlsReport("Failure details exceed summary")
        parsed.append(ParsedPolicy(
            hashlib.sha256(raw + b"\x00" + str(index).encode()).hexdigest(),
            domain, reporter, report_id, kind, start, end, successes, failures,
            tuple(sorted(counts.items())),
        ))
    return tuple(parsed)


def unpack(payload: bytes, filename: str, content_type: str) -> bytes:
    if not payload or len(payload) > MAX_ARCHIVE:
        raise InvalidTlsReport("Attachment exceeds size cap")
    name = filename.lower()
    ctype = content_type.lower().split(";", 1)[0].strip()
    zipped = name.endswith(".json.gz") or ctype in (
        "application/tlsrpt+gzip", "application/gzip", "application/x-gzip")
    plain = name.endswith(".json") or ctype == "application/tlsrpt+json"
    if zipped:
        if name and not name.endswith(".json.gz"):
            raise InvalidTlsReport("Invalid compressed attachment name")
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
                result = stream.read(MAX_DOCUMENT + 1)
        except (OSError, EOFError, ValueError) as exc:
            raise InvalidTlsReport("Invalid GZIP report") from exc
    elif plain:
        result = payload
    else:
        raise InvalidTlsReport("Unsupported MIME attachment")
    if len(result) > MAX_DOCUMENT:
        raise InvalidTlsReport("Decompressed report exceeds cap")
    return result
