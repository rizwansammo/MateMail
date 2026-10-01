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
import html as html_module
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
    # `background` as well as `background-color`: real signatures use the
    # shorthand, and allowing only the longhand silently flattened every one of
    # them. Values are filtered below, so a colour passes and `url(https://…)`
    # or `url(javascript:…)` does not.
    "background", "background-color", "border", "border-bottom", "border-collapse",
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
    #: A saved draft's chosen signature (DRAFT_SIGNATURE_HEADER), unverified:
    #: only the message detail for Drafts reads it, and only after checking
    #: the mailbox owns that signature.
    draft_signature_id: str = ""
    #: True for PostBox-created editable draft/scheduled source messages.
    draft_state: bool = False
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
    # Present only where the stored message carries the header — in practice
    # PostBox's own drafts. Callers decide whether to expose it.
    parsed.bcc = _addresses(message, "Bcc")
    parsed.draft_signature_id = clean_header(
        str(message.get(DRAFT_SIGNATURE_HEADER, "") or "")
    )
    parsed.draft_state = (
        clean_header(str(message.get(DRAFT_STATE_HEADER, "") or "")) == "1"
    )

    text_parts: list[str] = []
    html_parts: list[str] = []

    for index, part in enumerate(message.walk()):
        if part.get_content_maintype() == "multipart":
            continue

        disposition = (part.get_content_disposition() or "").lower()
        filename = part.get_filename()
        content_type = part.get_content_type()
        content_id = (part.get("Content-ID", "") or "").strip("<>")

        # Inline MIME images are often referenced only by Content-ID and carry
        # no filename. Treat them as message parts we can serve through the
        # authenticated preview endpoint; otherwise the sanitised HTML keeps
        # src="cid:..." and a normal browser can only show a broken-image icon.
        non_body_part = content_type not in ("text/plain", "text/html")
        if disposition == "attachment" or (
            non_body_part and (filename or disposition == "inline" or content_id)
        ):
            parsed.attachments.append(
                Attachment(
                    part_id=str(index),
                    filename=safe_filename(
                        decode_header_value(filename or "")
                        or f"attachment-{index}"
                    ),
                    content_type=content_type,
                    size=len(part.get_payload(decode=True) or b""),
                    inline=disposition == "inline" or bool(content_id),
                    content_id=content_id,
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
    r"""(?P<attr>\b(?:src|background|srcset)\s*=\s*)(?:(?P<quote>["'])(?P<quoted>\s*(?:https?:)?//[^"']*)(?P=quote)|(?P<bare>\s*(?:https?:)?//[^\s>]+))""",
    re.IGNORECASE,
)
_CSS_REMOTE_URL = re.compile(
    r"url\s*\(\s*['\"]?\s*(?:https?:)?//[^)'\"]+['\"]?\s*\)",
    re.IGNORECASE,
)


def _strip_remote_references(html: str) -> tuple[str, bool]:
    """
    Replace remote references with nothing, and say whether any were found.

    Real marketing mail is not consistent HTML: some generators emit unquoted
    src attributes and some use scheme-relative //cdn.example URLs. Both are
    remote requests and must trigger the same privacy banner as https:// URLs.
    """
    found = False

    def drop(match: re.Match) -> str:
        nonlocal found
        found = True
        # Remove the source-bearing attribute entirely. Keeping src=""
        # creates a broken-image glyph and can resolve against the current page.
        return ""

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


#: Tags whose TEXT CONTENT must go with them, not just the tag.
#:
#: nh3 removes a disallowed tag but keeps the text inside it, which is right
#: for <b>bold</b> and catastrophic for <title>. Its default for this is
#: {"script", "style"}; everything else here is a document-header element
#: whose content is metadata and must never be read out loud in an email.
SIGNATURE_DROP_CONTENT = {"script", "style", "title", "head", "noscript"}

#: The document wrapper a pasted signature often arrives in.
_DOCTYPE = re.compile(r"(?is)<!doctype[^>]*>")
_HEAD_BLOCK = re.compile(r"(?is)<head\b[^>]*>.*?</head\s*>")
_BODY_BLOCK = re.compile(r"(?is)<body\b[^>]*>(?P<inner>.*?)</body\s*>")
_HTML_OPEN = re.compile(r"(?is)</?html\b[^>]*>")


def extract_body_fragment(html: str) -> str:
    """
    The meaningful part of a pasted HTML document.

    People paste what their designer gave them, which is a whole document:
    doctype, <head> with <meta>, <title> and <style>, then <body>. None of
    the wrapper belongs in a signature, and a signature is a FRAGMENT — it is
    inserted into a message that already has its own document structure.

    <head> is removed whole rather than left to the tag sanitiser, because
    the sanitiser keeps inner text and <title> would become visible words.
    Then, if there is a <body>, its contents are the signature; otherwise the
    input was already a fragment and is returned as-is.

    This is a pre-pass, NOT the security boundary. `sanitize_signature` still
    runs the allow-list afterwards — a regex that believes it understands
    HTML is exactly how sanitisers get bypassed, so this one only has to be
    conservative, never complete.
    """
    if not html:
        return ""

    fragment = _DOCTYPE.sub("", html)
    fragment = _HEAD_BLOCK.sub("", fragment)

    match = _BODY_BLOCK.search(fragment)
    if match:
        fragment = match.group("inner")
    else:
        # No <body> pair — drop any stray <html> tags so they cannot survive
        # as an unknown element.
        fragment = _HTML_OPEN.sub("", fragment)

    return fragment.strip()


def sanitize_signature(html: str) -> str:
    """
    A signature is authored by the mailbox owner, and still sanitised.

    Self-authored is not the same as trusted: the editor is in a browser, the
    value round-trips through an API, and this HTML is injected into every
    message the mailbox sends. Remote images are permitted here — the owner
    chose them, and it is their own logo.

    The document wrapper is removed first so <title> cannot arrive as text,
    then the ordinary allow-list runs. Order matters: extraction is a
    convenience, the allow-list is the boundary.
    """
    fragment = extract_body_fragment(html or "")
    if not fragment:
        return ""

    cleaned = nh3.clean(
        fragment,
        tags=ALLOWED_TAGS,
        attributes={k: set(v) for k, v in ALLOWED_ATTRIBUTES.items()},
        url_schemes=ALLOWED_URL_SCHEMES,
        strip_comments=True,
        link_rel="noopener noreferrer nofollow",
        clean_content_tags=SIGNATURE_DROP_CONTENT,
    )
    return _filter_inline_styles(cleaned).strip()


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

#: The signature chosen for a saved draft. Written only on drafts, whose body
#: is stored WITHOUT the signature so the draft can be reopened and edited
#: with the same choice; sending rebuilds the message from the composer's
#: fields, applies the signature once and never carries this header.
DRAFT_SIGNATURE_HEADER = "X-PostBox-Signature-Id"
DRAFT_STATE_HEADER = "X-PostBox-Draft-State"

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
    related: list[tuple[str, str, bytes]] | None = None,
    message_id: str = "",
    keep_bcc: bool = False,
    draft_signature_id: str = "",
) -> EmailMessage:
    """
    An RFC-compliant message.

    `multipart/alternative` when both bodies exist, so a plain-text reader gets
    something readable rather than a wall of markup — and because a message
    with only an HTML part scores worse with every spam filter there is.

    `related` holds inline images as (cid, content_type, payload). They are
    attached to the HTML PART, producing

        multipart/alternative
          text/plain
          multipart/related
            text/html
            image/...   Content-ID: <cid>

    which is what makes `<img src="cid:...">` render inline. Attaching them
    to the message instead would make them ordinary attachments, and the
    signature would show as a broken image beside a paperclip.

    `html` arrives already sanitised — the caller owns that, because only the
    caller knows whether it is assembling a body, a signature, or both.

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
    # Bcc is deliberately NOT written as a header on anything that is
    # submitted. It goes in the SMTP envelope only — a Bcc header would be
    # delivered to every recipient and disclose exactly the people it exists
    # to hide.
    #
    # A saved draft is the one exception (`keep_bcc`). It is never submitted
    # — sending rebuilds the message from the composer's fields — and the
    # mailbox's own Drafts copy is the only place its Bcc recipients exist.
    # Without the header, reopening a draft silently dropped them.
    if keep_bcc and bcc:
        message["Bcc"] = ", ".join(clean_header(a) for a in bcc)
    # Draft-only metadata. The explicit state marker is written even when
    # there is no Bcc and no signature, so Scheduled send can distinguish the
    # new editable source format from legacy already-finalised messages.
    if keep_bcc:
        message[DRAFT_STATE_HEADER] = "1"
    if draft_signature_id:
        message[DRAFT_SIGNATURE_HEADER] = clean_header(draft_signature_id)
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
        message.add_alternative(html, subtype="html")

        for cid, content_type, payload in related or []:
            maintype, _, subtype = (
                content_type or "application/octet-stream"
            ).partition("/")
            # `add_related` on the HTML part, not on `message`: that is what
            # creates multipart/related around the HTML rather than adding a
            # sibling attachment to the alternative.
            message.get_payload()[-1].add_related(
                payload,
                maintype=maintype or "image",
                subtype=subtype or "png",
                cid=f"<{cid}>",
                disposition="inline",
            )

    for filename, content_type, payload in attachments or []:
        maintype, _, subtype = (content_type or "application/octet-stream").partition("/")
        message.add_attachment(
            payload,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=safe_filename(filename),
        )

    return message


def escape_attribute(value: str) -> str:
    """Text safe to place inside a double-quoted HTML attribute."""
    return html_module.escape(value or "", quote=True)


def html_to_text(html: str) -> str:
    """
    Public name for the HTML-to-text fallback.

    The private `_html_to_text` already existed for the composer; signatures
    need the same conversion, and reaching across modules for an underscore
    name is how a helper quietly becomes two slightly different helpers.
    """
    return _html_to_text(html)


def text_to_html(text: str) -> str:
    """
    A plain-text body as an HTML fragment.

    Needed because PostBox's composer is a plain textarea, so the only body
    it produces is text — while an HTML signature forces the message to have
    an HTML alternative. Without this the HTML part would contain the
    signature and nothing else, and a recipient whose client prefers HTML
    would see a signature with no message above it.

    Escaped first, then newlines become <br>. Escaping second would turn the
    <br> tags we just inserted into visible text.
    """
    if not text:
        return ""
    escaped = html_module.escape(text, quote=False)
    return escaped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def _html_to_text(html: str) -> str:
    """
    A readable plain-text alternative when the composer only produced HTML.

    Not a renderer — it keeps block structure and link targets, which is what
    makes the fallback usable rather than a run-on paragraph.
    """
    if not html:
        return ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    # The newline that follows the tag in the SOURCE is consumed with it.
    # Without that, `<br>` at the end of a line in a nicely formatted
    # signature becomes two newlines, and every line of the plain-text
    # fallback ends up separated by a blank line.
    text = re.sub(r"(?i)<br\s*/?>[ \t]*\r?\n?", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>[ \t]*\r?\n?", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "  • ", text)
    text = re.sub(r'(?is)<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r"\2 <\1>", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
            .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))

    # Indentation in the SOURCE is formatting for whoever edits the HTML; it
    # is not part of the message. Left in, every line of a pasted signature
    # arrives indented and separated by blank lines.
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(lines)

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
