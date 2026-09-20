"""
Reading a message safely, and building one correctly.

THE THREAT
    Email HTML is the most hostile input MateMail renders. It arrives from
    anyone, it is authored to defeat filters, and it is displayed inside an
    application that holds a live session. Three separate things have to be
    true:

      1. No script may execute. Not `<script>`, not `onclick`, not
         `javascript:` in an href, not `<svg><animate>`.
      2. Nothing may reach the network without the reader asking. A remote
         image is a tracking pixel that confirms the address is live and hands
         over the reader's IP.
      3. The message may not style the application around it. An email that
         sets `position:fixed` over the whole viewport is a phishing surface.

WHY nh3 AND NOT A REGULAR EXPRESSION
    `nh3` binds Ammonia, which parses with html5ever — the same engine
    browsers use. The entire class of sanitiser bypass is the gap between what
    the filter thinks the markup says and what the browser thinks it says, and
    only a real parser closes it. A regex-based "sanitiser" is not a
    sanitiser; it is a filter that has not met its counterexample yet.

    It is an ALLOW-list: unlisted tags and attributes are dropped rather than
    unlisted-dangerous ones being blocked, so a tag invented after this code
    was written is removed by default.
"""
from __future__ import annotations

import email
import email.policy
import email.utils
import logging
import mimetypes
import re
import uuid
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.message import EmailMessage

import nh3

logger = logging.getLogger(__name__)

#: Everything a legitimate message needs to express itself, and nothing that
#: executes, navigates, embeds or submits.
ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "cite", "code", "col",
    "colgroup", "dd", "div", "dl", "dt", "em", "figcaption", "figure", "h1",
    "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "li", "ol", "p", "pre",
    "q", "s", "small", "span", "strong", "sub", "sup", "table", "tbody", "td",
    "tfoot", "th", "thead", "tr", "u", "ul",
}

ALLOWED_ATTRIBUTES = {
    # `rel` is deliberately NOT allowed: `link_rel` below sets it, and nh3
    # refuses (loudly) to do both. Forcing our own rel is the correct
    # behaviour anyway — a message must not choose its own link policy.
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan", "align", "valign"},
    "th": {"colspan", "rowspan", "align", "valign"},
    "table": {"width", "cellpadding", "cellspacing", "border", "align"},
    "col": {"width", "span"},
    "colgroup": {"width", "span"},
    "*": {"style", "class", "dir", "lang"},
}

#: `style` survives because real mail is unreadable without it, but it is
#: filtered separately below. `data:` is not here: a data URL can carry an SVG
#: with script in it, and `cid:` covers every legitimate inline image.
ALLOWED_URL_SCHEMES = {"http", "https", "mailto", "cid", "tel"}

#: CSS properties an email may set. An allow-list again, and deliberately
#: missing `position`, `z-index`, `top`, `left`, `transform` and anything else
#: that lets content escape its box and overlay the application.
ALLOWED_CSS = {
    "background-color", "border", "border-bottom", "border-collapse",
    "border-color", "border-left", "border-radius", "border-right",
    "border-style", "border-top", "border-width", "color", "display",
    "font-family", "font-size", "font-style", "font-weight", "height",
    "letter-spacing", "line-height", "list-style-type", "margin",
    "margin-bottom", "margin-left", "margin-right", "margin-top", "max-width",
    "padding", "padding-bottom", "padding-left", "padding-right",
    "padding-top", "text-align", "text-decoration", "text-transform",
    "vertical-align", "white-space", "width", "word-break", "word-wrap",
}

_STYLE_DECL = re.compile(r"([a-zA-Z-]+)\s*:\s*([^;]+)")
_DANGEROUS_CSS_VALUE = re.compile(
    r"expression\s*\(|url\s*\(\s*['\"]?\s*(?!cid:)[a-z]+:|javascript:|@import|behaviou?r\s*:",
    re.IGNORECASE,
)


@dataclass
class Attachment:
    part_id: str
    filename: str
    content_type: str
    size: int
    inline: bool = False
    content_id: str = ""


@dataclass
class ParsedMessage:
    subject: str = ""
    from_name: str = ""
    from_address: str = ""
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    reply_to: str = ""
    date: str = ""
    message_id: str = ""
    in_reply_to: str = ""
    references: list[str] = field(default_factory=list)
    text: str = ""
    html: str = ""
    #: True when sanitisation removed at least one remote reference, so the UI
    #: can offer "display remote images" honestly rather than always showing it.
    has_remote_images: bool = False
    attachments: list[Attachment] = field(default_factory=list)


def decode_header_value(value: str) -> str:
    """RFC 2047 encoded words, tolerantly. A malformed header is not fatal."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def parse_message(raw: bytes, *, load_remote_images: bool = False) -> ParsedMessage:
    """Parse a full RFC 5322 message into something safe to render."""
    message = email.message_from_bytes(raw, policy=email.policy.default)

    parsed = ParsedMessage(
        subject=decode_header_value(message.get("Subject", "")),
        date=message.get("Date", ""),
        message_id=message.get("Message-ID", ""),
        in_reply_to=message.get("In-Reply-To", ""),
        references=(message.get("References", "") or "").split(),
        reply_to=email.utils.parseaddr(message.get("Reply-To", ""))[1],
    )
    parsed.from_name, parsed.from_address = email.utils.parseaddr(
        decode_header_value(message.get("From", ""))
    )
    parsed.to = _addresses(message, "To")
    parsed.cc = _addresses(message, "Cc")

    text_parts: list[str] = []
    html_parts: list[str] = []

    for index, part in enumerate(message.walk()):
        if part.get_content_maintype() == "multipart":
            continue

        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        content_type = part.get_content_type()

        if disposition == "attachment" or (filename and content_type not in
                                           ("text/plain", "text/html")):
            parsed.attachments.append(
                Attachment(
                    part_id=str(index),
                    filename=safe_filename(decode_header_value(filename or "")
                                           or f"attachment-{index}"),
                    content_type=content_type,
                    size=len(part.get_payload(decode=True) or b""),
                    inline=disposition == "inline",
                    content_id=(part.get("Content-ID", "") or "").strip("<>"),
                )
            )
            continue

        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            decoded = payload.decode(charset, "replace")
        except LookupError:
            decoded = payload.decode("utf-8", "replace")

        if content_type == "text/plain":
            text_parts.append(decoded)
        elif content_type == "text/html":
            html_parts.append(decoded)

    parsed.text = "\n".join(text_parts).strip()

    if html_parts:
        cleaned, blocked = sanitize_html(
            "\n".join(html_parts), load_remote_images=load_remote_images
        )
        parsed.html = cleaned
        parsed.has_remote_images = blocked

    # A message with no readable body at all still has to render as something,
    # and an empty reader looks like a bug rather than an empty message.
    if not parsed.text and not parsed.html:
        parsed.text = ""

    return parsed


def _addresses(message, header: str) -> list[str]:
    return [
        address
        for _, address in email.utils.getaddresses([message.get(header, "") or ""])
        if address
    ]


def sanitize_html(html: str, *, load_remote_images: bool = False) -> tuple[str, bool]:
    """
    Clean message HTML. Returns (html, remote_content_was_blocked).

    Remote images are removed rather than rewritten to a proxy. A proxy would
    hide the reader's IP but still confirm to the sender that the message was
    opened, which is most of what a tracking pixel is for. Removing them means
    "blocked" is the truth.
    """
    if not html:
        return "", False

    blocked = False

    if not load_remote_images:
        html, blocked = _strip_remote_references(html)

    cleaned = nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes={k: set(v) for k, v in ALLOWED_ATTRIBUTES.items()},
        url_schemes=ALLOWED_URL_SCHEMES,
        strip_comments=True,
        link_rel="noopener noreferrer nofollow",
    )

    return _filter_inline_styles(cleaned), blocked


_REMOTE_SRC = re.compile(
    r"""(?P<attr>\b(?:src|background|srcset)\s*=\s*)(?P<quote>["'])(?P<url>\s*https?://[^"']*)(?P=quote)""",
    re.IGNORECASE,
)
_CSS_REMOTE_URL = re.compile(r"url\s*\(\s*['\"]?\s*https?://[^)'\"]+['\"]?\s*\)", re.IGNORECASE)


def _strip_remote_references(html: str) -> tuple[str, bool]:
    """Replace remote references with nothing, and say whether any were found."""
    found = False

    def drop(match: re.Match) -> str:
        nonlocal found
        found = True
        return f'{match.group("attr")}{match.group("quote")}{match.group("quote")}'

    html = _REMOTE_SRC.sub(drop, html)

    if _CSS_REMOTE_URL.search(html):
        found = True
        html = _CSS_REMOTE_URL.sub("none", html)

    return html, found


def _filter_inline_styles(html: str) -> str:
    """
    Keep only allow-listed CSS declarations.

    nh3 permits the `style` attribute through but does not understand CSS, so
    this is where `position:fixed`, `url(javascript:...)` and `expression()`
    are removed. Without it an email could cover the PostBox interface with its
    own content.
    """

    def clean_attribute(match: re.Match) -> str:
        declarations = []
        for name, value in _STYLE_DECL.findall(match.group(1)):
            prop = name.strip().lower()
            if prop not in ALLOWED_CSS:
                continue
            if _DANGEROUS_CSS_VALUE.search(value):
                continue
            declarations.append(f"{prop}:{value.strip()}")
        if not declarations:
            return ""
        return 'style="' + ";".join(declarations) + '"'

    return re.sub(r'style\s*=\s*"([^"]*)"', clean_attribute, html, flags=re.IGNORECASE)


def sanitize_signature(html: str) -> str:
    """
    A signature is authored by the mailbox owner, and still sanitised.

    Self-authored is not the same as trusted: the editor is in a browser, the
    value round-trips through an API, and this HTML is injected into every
    message the mailbox sends. Remote images are permitted here — the owner
    chose them, and it is their own logo.
    """
    cleaned, _ = sanitize_html(html or "", load_remote_images=True)
    return cleaned


_UNSAFE_FILENAME = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|]")


def safe_filename(name: str) -> str:
    """
    A filename that cannot escape a directory or name a device.

    A filename in a message is attacker-controlled text, never a path. Path
    separators, traversal, control characters and the Windows device names are
    all removed — the result is used in a Content-Disposition header and never
    to open a file, but defending in one place only is how the second place
    gets missed.
    """
    name = (name or "").strip().replace("\r", "").replace("\n", "")
    name = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    name = _UNSAFE_FILENAME.sub("_", name)
    name = name.lstrip(". ")
    if not name:
        name = "attachment"
    if name.split(".")[0].upper() in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        name = f"_{name}"
    return name[:200]


def extract_attachment(raw: bytes, part_id: str) -> tuple[str, str, bytes]:
    """Return (filename, content_type, payload) for one part of a message."""
    message = email.message_from_bytes(raw, policy=email.policy.default)
    for index, part in enumerate(message.walk()):
        if str(index) != part_id:
            continue
        payload = part.get_payload(decode=True) or b""
        filename = safe_filename(
            decode_header_value(part.get_filename() or "") or f"attachment-{index}"
        )
        return filename, part.get_content_type() or "application/octet-stream", payload
    raise KeyError(part_id)


# ── building ────────────────────────────────────────────────────────────────

_HEADER_INJECTION = re.compile(r"[\r\n]")


def clean_header(value: str) -> str:
    """
    Strip CR and LF from a header value.

    Header injection: a newline in a subject or a recipient ends the header and
    starts a new one, which is how an attacker adds `Bcc:` to somebody else's
    message. Python's email package encodes most of this correctly now, but the
    value also reaches logs and the IMAP layer, so it is cleaned at the door.
    """
    return _HEADER_INJECTION.sub(" ", value or "").strip()


def build_message(
    *,
    from_address: str,
    from_name: str = "",
    to: list[str],
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    subject: str = "",
    text: str = "",
    html: str = "",
    in_reply_to: str = "",
    references: list[str] | None = None,
    attachments: list[tuple[str, str, bytes]] | None = None,
    message_id: str = "",
) -> EmailMessage:
    """
    An RFC-compliant message.

    `multipart/alternative` when both bodies exist, so a plain-text reader gets
    something readable rather than a wall of markup — and because a message
    with only an HTML part scores worse with every spam filter there is.

    The Message-ID is generated here and kept: the same value is submitted to
    Postfix and appended to Sent, so the copy in Sent is genuinely the message
    that was sent rather than a lookalike, and replies thread against it.
    """
    message = EmailMessage()

    message["From"] = (
        email.utils.formataddr((clean_header(from_name), from_address))
        if from_name else from_address
    )
    message["To"] = ", ".join(clean_header(a) for a in to)
    if cc:
        message["Cc"] = ", ".join(clean_header(a) for a in cc)
    # Bcc is deliberately NOT written as a header. It goes in the SMTP envelope
    # only — a Bcc header would be delivered to every recipient and disclose
    # exactly the people it exists to hide.
    message["Subject"] = clean_header(subject)
    message["Date"] = email.utils.formatdate(localtime=True)
    message["Message-ID"] = message_id or email.utils.make_msgid(
        domain=from_address.rsplit("@", 1)[-1] or None
    )

    if in_reply_to:
        message["In-Reply-To"] = clean_header(in_reply_to)
        chain = list(references or [])
        if in_reply_to not in chain:
            chain.append(in_reply_to)
        if chain:
            message["References"] = " ".join(chain)

    body_text = text or _html_to_text(html)
    message.set_content(body_text)
    if html:
        message.add_alternative(sanitize_signature(html), subtype="html")

    for filename, content_type, payload in attachments or []:
        maintype, _, subtype = (content_type or "application/octet-stream").partition("/")
        message.add_attachment(
            payload,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=safe_filename(filename),
        )

    return message


def _html_to_text(html: str) -> str:
    """
    A readable plain-text alternative when the composer only produced HTML.

    Not a renderer — it keeps block structure and link targets, which is what
    makes the fallback usable rather than a run-on paragraph.
    """
    if not html:
        return ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "  • ", text)
    text = re.sub(r'(?is)<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r"\2 <\1>", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
            .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def quote_for_reply(parsed: ParsedMessage) -> tuple[str, str]:
    """The quoted original, as (text, html)."""
    who = parsed.from_name or parsed.from_address
    header = f"On {parsed.date}, {who} wrote:"

    text = header + "\n" + "\n".join(
        f"> {line}" for line in (parsed.text or _html_to_text(parsed.html)).splitlines()
    )

    body = parsed.html or f"<pre>{_escape(parsed.text)}</pre>"
    html = (
        f"<p>{_escape(header)}</p>"
        f'<blockquote style="margin:0 0 0 12px;padding-left:12px;'
        f'border-left:2px solid #ccc">{body}</blockquote>'
    )
    return text, html


def forward_body(parsed: ParsedMessage) -> tuple[str, str]:
    """The forwarded original, as (text, html), with the standard separator."""
    lines = [
        "---------- Forwarded message ----------",
        f"From: {parsed.from_name} <{parsed.from_address}>".strip(),
        f"Date: {parsed.date}",
        f"Subject: {parsed.subject}",
        f"To: {', '.join(parsed.to)}",
    ]
    if parsed.cc:
        lines.append(f"Cc: {', '.join(parsed.cc)}")

    text = "\n".join(lines) + "\n\n" + (parsed.text or _html_to_text(parsed.html))
    html = (
        "<p>" + "<br>".join(_escape(line) for line in lines) + "</p>"
        + (parsed.html or f"<pre>{_escape(parsed.text)}</pre>")
    )
    return text, html


def _escape(value: str) -> str:
    return (
        (value or "")
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def reply_recipients(
    parsed: ParsedMessage, own_identities: set[str], reply_all: bool
) -> tuple[list[str], list[str]]:
    """
    Who a reply goes to. Returns (to, cc).

    Reply-To wins over From when it is present and parses — that is the entire
    purpose of the header, and ignoring it sends replies to a no-reply address.

    Reply All removes the mailbox's own identities. Without that, every reply
    in a thread adds the sender to their own Cc list and the header grows
    without bound.
    """
    primary = parsed.reply_to or parsed.from_address
    to = [primary] if primary else []

    if not reply_all:
        return _dedupe(to, own_identities), []

    cc_candidates = [a for a in (*parsed.to, *parsed.cc) if a]
    to_final = _dedupe(to, own_identities)
    cc_final = _dedupe(cc_candidates, own_identities | {a.lower() for a in to_final})
    return to_final, cc_final


def _dedupe(addresses: list[str], exclude: set[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for address in addresses:
        key = (address or "").strip().lower()
        if not key or key in seen or key in exclude:
            continue
        seen.add(key)
        result.append(address.strip())
    return result


def guess_content_type(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def new_message_id(domain: str) -> str:
    return email.utils.make_msgid(domain=domain or None)


def opaque_id() -> str:
    return uuid.uuid4().hex
