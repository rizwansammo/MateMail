"""
Signature rendering and message assembly — the one place either happens.

WHY THIS MODULE EXISTS
    A signature reaches a recipient through four things that all have to agree:
    what the settings UI stored, what kind of content it is, what the plain-text
    alternative says, and how the MIME tree is shaped. Before this module those
    decisions were spread across a serializer, a view and a React component, and
    they disagreed — which is how a full HTML document ended up appended to a
    plain-text body and delivered as visible markup.

    Both callers use `apply()`: PostBox's composer and the Connected Apps send
    endpoint. A second implementation for integrations would mean SalesHub
    could send a differently-shaped message from the same stored signature.

THE RULE THAT WAS BEING BROKEN
    If a message has an HTML alternative, that alternative must contain the
    BODY as well as the signature. The old code did:

        html = f"{html}<br><br>{signature.html}" if html else signature.html

    and PostBox's composer never sends `html`, so the false branch always ran
    and the HTML part became the signature alone. A recipient whose client
    prefers HTML — which is all of them — would have seen a signature with no
    message above it. The plain-text part still had the body, which is the only
    reason this was not noticed sooner.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings

from . import mime
from .models import MailSignature, SignatureKind

#: Separates the message from the signature in both alternatives.
#:
#: A blank line and nothing else. No `-- ` sigdash: Gmail and Outlook both
#: collapse everything after it into a hidden quote block, which would hide a
#: signature somebody deliberately designed.
_HTML_GAP = "<br><br>"
_TEXT_GAP = "\n\n"


@dataclass
class Rendered:
    """A signature as the two things a message needs, plus anything inline."""

    html: str = ""
    text: str = ""
    #: (cid, content_type, payload) for images the HTML references by `cid:`.
    related: list[tuple[str, str, bytes]] = field(default_factory=list)


def render(signature: MailSignature) -> Rendered:
    """
    One stored signature, as HTML and as text.

    Branches on `kind`, never on which column happens to be non-empty. Guessing
    from content is what produced the original bug: HTML sitting in `text`
    looked exactly like a plain-text signature to every reader of the row.
    """
    if signature.kind == SignatureKind.IMAGE:
        return _render_image(signature)

    if signature.kind == SignatureKind.HTML:
        html = signature.html or ""
        # `text` is generated on save, but a row written before that existed —
        # or by a direct API caller — may not have one. Deriving it here costs
        # nothing and beats sending an empty plain-text alternative.
        return Rendered(html=html, text=signature.text or mime.html_to_text(html))

    # Plain text. The HTML form is the escaped text, so that a message which
    # has an HTML alternative for some other reason still shows the signature
    # rather than dropping it.
    text = signature.text or ""
    return Rendered(html=mime.text_to_html(text), text=text)


def _render_image(signature: MailSignature) -> Rendered:
    """
    An image signature, as an inline `cid:` reference.

    NOT a data: URL. A base64 data URL inside message HTML is stripped by
    Gmail and Outlook, inflates every message by a third, and cannot be
    deduplicated — the image would ride along again on every reply in the
    thread. `cid:` is what mail clients actually implement for this.

    The alt text is the plain-text alternative too: a signature that is only an
    image has no words otherwise, and a reader with images disabled would get a
    blank space where a name should be.
    """
    payload = bytes(signature.image_data or b"")
    if not payload:
        return Rendered()

    alt = signature.image_alt or signature.name or "Signature"
    # New per render, so two messages never share a Content-ID. A repeated cid
    # across messages lets a client cache the wrong image into a later one.
    cid = mime.opaque_id()

    html = (
        f'<img src="cid:{cid}" alt="{mime.escape_attribute(alt)}" '
        f'style="max-width:100%;height:auto" />'
    )
    return Rendered(
        html=html,
        text=alt,
        related=[(cid, signature.image_content_type or "image/png", payload)],
    )


def apply(
    *,
    text: str,
    html: str,
    signature: MailSignature | None,
) -> tuple[str, str, list[tuple[str, str, bytes]]]:
    """
    Combine a composed body with a signature. Returns (text, html, related).

    The contract, and the thing the tests pin:

      * The text alternative is always body + signature text.
      * If there is any HTML at all, the HTML alternative is body + signature
        HTML — never the signature alone. When the composer gave us only plain
        text, the body is converted with `text_to_html` first.
      * A plain-text signature on a plain-text body produces NO html, so the
        message stays a simple text/plain rather than gaining an alternative
        that says nothing extra.

    Applied exactly once, here. The composer shows a preview but never sends
    the signature as part of the body, so there is nothing to double up.
    """
    body_text = text or ""
    body_html = html or ""

    if signature is None:
        return body_text, body_html, []

    rendered = render(signature)
    if not rendered.html and not rendered.text:
        return body_text, body_html, []

    # ── plain-text alternative ──────────────────────────────────────────────
    if rendered.text:
        out_text = f"{body_text}{_TEXT_GAP}{rendered.text}" if body_text else rendered.text
    else:
        out_text = body_text

    # ── HTML alternative ────────────────────────────────────────────────────
    needs_html = bool(body_html) or signature.kind in (
        SignatureKind.HTML,
        SignatureKind.IMAGE,
    )

    if not needs_html:
        # Text signature, text body: nothing here needs markup.
        return out_text, "", []

    # THE FIX. The body is included unconditionally — converted from text when
    # the composer did not produce HTML — so the alternative can never be the
    # signature by itself.
    rendered_body = body_html or mime.text_to_html(body_text)
    if rendered_body and rendered.html:
        out_html = f"{rendered_body}{_HTML_GAP}{rendered.html}"
    else:
        out_html = rendered_body or rendered.html

    return out_text, out_html, list(rendered.related)


def for_mailbox(mailbox, signature_id) -> MailSignature | None:
    """
    Look up a signature the mailbox owns, or None.

    Scoped to the mailbox, not merely fetched by id: a signature id is a UUID a
    caller supplies, and an unscoped lookup would let one mailbox stamp another
    mailbox's signature — including its logo and job title — onto its own mail.
    """
    if not signature_id:
        return None
    return MailSignature.objects.for_mailbox(mailbox).filter(pk=signature_id).first()


def default_for(mailbox, *, replying: bool) -> MailSignature | None:
    """The signature that should be preselected for a new message or a reply."""
    field_name = "use_for_replies" if replying else "use_for_new"
    return (
        MailSignature.objects.for_mailbox(mailbox)
        .filter(**{field_name: True})
        .first()
    )


def image_limits() -> tuple[int, int]:
    """(max bytes, max pixels per side)."""
    return (
        settings.POSTBOX_SIGNATURE_IMAGE_KB * 1024,
        settings.POSTBOX_SIGNATURE_IMAGE_MAX_PX,
    )
