"""Strict RFC 7489/DMARC aggregate XML reader.

Untrusted reports are bounded by both compressed and decompressed size.
Entity declarations / external entities are rejected by defusedxml.
The XML is never stored as a blob or passed into an HTML renderer.
"""
from __future__ import annotations

import gzip
import hashlib
import ipaddress
import io
import re
import stat
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone

from defusedxml import ElementTree as SafeXML

MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
MAX_XML_BYTES = 2 * 1024 * 1024
MAX_DOCUMENTS = 8
MAX_RECORDS = 1500
MAX_COUNT = 10_000_000_000
MAX_WINDOW = 31 * 86400


class InvalidReport(ValueError):
    """Expected validation failure: never log raw XML or attachment bytes."""


@dataclass(frozen=True)
class ParsedRow:
    source_ip: str
    count: int
    disposition: str
    spf_result: str
    dkim_result: str


@dataclass(frozen=True)
class ParsedReport:
    digest: str
    policy_domain: str
    reporter: str
    report_identifier: str
    period_start: datetime
    period_end: datetime
    rows: tuple[ParsedRow, ...]
    count: int
    spf_pass: int
    dkim_pass: int
    dmarc_pass: int


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(parent, key):
    return [element for element in parent if _local(element.tag) == key]


def _single(parent, name, *, required=True):
    choices = _children(parent, name) if parent is not None else []
    if len(choices) != 1:
        if len(choices) == 0 and not required:
            return None
        raise InvalidReport("Missing or duplicate DMARC XML element")
    return choices[0]


def _text(node, name, *, limit=255):
    element = _single(node, name)
    value = (element.text or "").strip()
    if not value or len(value) > limit:
        raise InvalidReport("Missing or oversized DMARC XML value")
    return value


def _domain(value):
    value = value.rstrip(".").lower()
    if not (1 <= len(value) <= 253):
        raise InvalidReport("Invalid policy domain")
    labels = value.split(".")
    if len(labels) < 2 or any(
        not (1 <= len(label) <= 63)
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
        for label in labels
    ):
        raise InvalidReport("Invalid policy domain")
    return value


def _date(value):
    try:
        ts = int(value)
        if ts < 946684800 or ts > 4102444800:
            raise ValueError
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, OverflowError, OSError) as exc:
        raise InvalidReport("Invalid aggregate report timestamp") from exc


def parse_xml(raw: bytes) -> ParsedReport:
    if not raw or len(raw) > MAX_XML_BYTES:
        raise InvalidReport("XML payload is missing or oversized")
    try:
        root = SafeXML.fromstring(raw, forbid_dtd=True, forbid_entities=True,
                                  forbid_external=True)
    except Exception as exc:
        raise InvalidReport("Rejected unsafe or malformed XML") from exc
    if _local(root.tag) != "feedback":
        raise InvalidReport("Not a DMARC aggregate report")
    meta = _single(root, "report_metadata")
    policy = _single(root, "policy_published")
    reporter = _text(meta, "org_name")
    report_id = _text(meta, "report_id")
    domain = _domain(_text(policy, "domain"))
    window = _single(meta, "date_range")
    start = _date(_text(window, "begin", limit=12))
    end = _date(_text(window, "end", limit=12))
    if end < start or (end - start).total_seconds() > MAX_WINDOW:
        raise InvalidReport("Invalid report date range")
    elements = _children(root, "record")
    if not elements or len(elements) > MAX_RECORDS:
        raise InvalidReport("Empty or excessive report records")
    rows = []
    for node in elements:
        row = _single(node, "row")
        evaluated = _single(row, "policy_evaluated")
        try:
            source = str(ipaddress.ip_address(_text(row, "source_ip", limit=45)))
            count = int(_text(row, "count", limit=20))
        except (ValueError, OverflowError) as exc:
            raise InvalidReport("Invalid source IP or message count") from exc
        if count < 1 or count > MAX_COUNT:
            raise InvalidReport("Invalid record count")
        disposition = _text(evaluated, "disposition", limit=16).lower()
        spf = _text(evaluated, "spf", limit=32).lower()
        dkim = _text(evaluated, "dkim", limit=32).lower()
        if disposition not in ("none", "quarantine", "reject"):
            raise InvalidReport("Unexpected DMARC disposition")
        if spf not in ("pass", "fail") or dkim not in ("pass", "fail"):
            raise InvalidReport("Unexpected DMARC evaluated result")
        rows.append(ParsedRow(source, count, disposition, spf, dkim))
    total = sum(row.count for row in rows)
    if total > MAX_COUNT * MAX_RECORDS:
        raise InvalidReport("Oversized aggregate count")
    return ParsedReport(
        digest=hashlib.sha256(raw).hexdigest(),
        policy_domain=domain,
        reporter=reporter,
        report_identifier=report_id,
        period_start=start,
        period_end=end,
        rows=tuple(rows),
        count=total,
        spf_pass=sum(r.count for r in rows if r.spf_result == "pass"),
        dkim_pass=sum(r.count for r in rows if r.dkim_result == "pass"),
        dmarc_pass=sum(r.count for r in rows if r.spf_result == "pass" or r.dkim_result == "pass"),
    )


def extract_xml_documents(payload: bytes, filename: str = "", content_type: str = "") -> list[bytes]:
    """Return bounded XML document bytes, without ever extracting archive paths."""
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise InvalidReport("Attachment is too large")
    name = (filename or "").lower()
    ctype = (content_type or "").lower().split(";")[0].strip()
    if name.endswith(".zip") or ctype == "application/zip":
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                files = archive.infolist()
                if not files or len(files) > MAX_DOCUMENTS:
                    raise InvalidReport("Unsupported archive member count")
                documents = []
                for info in files:
                    if (
                        info.is_dir() or info.flag_bits & 1
                        or stat.S_ISLNK(info.external_attr >> 16)
                        or info.filename.startswith("/")
                        or ".." in info.filename.split("/")
                        or not info.filename.lower().endswith(".xml")
                        or info.file_size > MAX_XML_BYTES
                        or info.compress_size == 0
                        or info.file_size > info.compress_size * 100
                    ):
                        raise InvalidReport("Unsafe DMARC archive member")
                    with archive.open(info) as stream:
                        doc = stream.read(MAX_XML_BYTES + 1)
                    if len(doc) > MAX_XML_BYTES:
                        raise InvalidReport("Decompressed XML is too large")
                    documents.append(doc)
                return documents
        except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
            raise InvalidReport("Invalid ZIP DMARC report") from exc
    if name.endswith(".gz") or ctype in ("application/gzip", "application/x-gzip"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
                doc = stream.read(MAX_XML_BYTES + 1)
        except (OSError, EOFError) as exc:
            raise InvalidReport("Invalid GZIP DMARC report") from exc
        if len(doc) > MAX_XML_BYTES:
            raise InvalidReport("GZIP expanded too far")
        return [doc]
    if name.endswith(".xml") or ctype in ("application/xml", "text/xml"):
        if len(payload) > MAX_XML_BYTES:
            raise InvalidReport("Raw XML too large")
        return [payload]
    raise InvalidReport("Not a supported DMARC attachment")
