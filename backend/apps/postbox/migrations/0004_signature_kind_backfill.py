"""
Classify existing signatures, conservatively.

THE ROW THIS EXISTS FOR
    The settings UI bound its only content box to `text`, so somebody who
    pasted a designed HTML signature got it stored as plain text — and then
    appended verbatim to a plain-text body, which is how a recipient came to
    read `<!doctype html>` in their inbox.

    Production holds exactly one signature row and it is that one: ~3.5 KB
    beginning with a doctype, `html` empty, and set as the default for both new
    messages and replies.

WHY EVERYTHING IS FROZEN IN THIS FILE
    A migration is history. It has to produce the same result when it runs on a
    fresh database in three years as it did the day it was written, and the
    only way to guarantee that is to depend on nothing that can change.

    An earlier version of this file imported `sanitize_signature` and
    `html_to_text` from `apps.postbox.mime`. That is a trap: tighten the
    allow-list later and this migration quietly starts producing different
    HTML; rename either function and it stops working at all, on exactly the
    deployment that most needs it — a rebuild from zero.

    So the allow-lists and both conversions are copied here as literals. They
    are deliberately a SNAPSHOT of `mime.py` as of this migration and must NOT
    be kept in sync with it afterwards; divergence is the intended behaviour.

    The one external dependency left is `nh3`, pinned in requirements.txt. A
    sanitiser is not something to hand-roll from regexes — the whole attack
    surface is the gap between what a filter parses and what a browser parses —
    and a pinned third-party parser is the same class of dependency as
    `django.db` itself.

REVERSIBILITY — READ THIS BEFORE ROLLING BACK
    The reverse is BEST-EFFORT and does not restore the original bytes. It
    cannot: the forward pass sanitises the pasted document, and sanitising is
    lossy by design — the doctype, `<head>`, `<title>`, `<style>` blocks and
    any disallowed markup are gone and were never stored anywhere else.

    What the reverse does is put the SANITISED html back into `text` and set
    `kind` to 'text', which restores the shape of the old data (markup living
    in the text column) so pre-migration code behaves as it did. It restores
    the old BEHAVIOUR, not the old CONTENT.

    It is written this way rather than marked irreversible because blocking
    `migrate postbox 0002` outright would be worse during a rollback window,
    and because the lossy step has already happened by then — refusing to
    reverse would not un-lose anything.
"""
import re

from django.db import migrations

try:  # pragma: no cover - exercised by which branch the environment takes
    import nh3
except ImportError:  # pragma: no cover
    nh3 = None


# ── frozen copies of mime.py as of this migration ───────────────────────────
#
# Snapshot. Do not update these to match mime.py later — see the module
# docstring.

_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "cite", "code", "col",
    "colgroup", "dd", "div", "dl", "dt", "em", "figcaption", "figure", "h1",
    "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "li", "ol", "p", "pre",
    "q", "s", "small", "span", "strong", "sub", "sup", "table", "tbody", "td",
    "tfoot", "th", "thead", "tr", "u", "ul",
}

_ATTRIBUTES = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan", "align", "valign"},
    "th": {"colspan", "rowspan", "align", "valign"},
    "table": {"width", "cellpadding", "cellspacing", "border", "align"},
    "col": {"width", "span"},
    "colgroup": {"width", "span"},
    "*": {"style", "class", "dir", "lang"},
}

_URL_SCHEMES = {"http", "https", "mailto", "cid", "tel"}

_CSS = {
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

#: Tags whose text content goes with them. nh3 keeps the text inside a removed
#: tag by default, which is right for <b> and catastrophic for <title>.
_DROP_CONTENT = {"script", "style", "title", "head", "noscript"}

_DOCTYPE = re.compile(r"(?is)<!doctype[^>]*>")
_HEAD_BLOCK = re.compile(r"(?is)<head\b[^>]*>.*?</head\s*>")
_BODY_BLOCK = re.compile(r"(?is)<body\b[^>]*>(?P<inner>.*?)</body\s*>")
_HTML_OPEN = re.compile(r"(?is)</?html\b[^>]*>")
_STYLE_ATTR = re.compile(r'style\s*=\s*"([^"]*)"', re.IGNORECASE)

#: An unambiguous document wrapper. Anchored on the two things that cannot
#: appear by accident in prose.
_LOOKS_LIKE_DOCUMENT = re.compile(r"(?is)<!doctype\s+html|<html[\s>]")


def _filter_styles(html):
    """Drop CSS properties outside the frozen allow-list."""
    def clean(match):
        kept = []
        for declaration in match.group(1).split(";"):
            name, sep, value = declaration.partition(":")
            if sep and name.strip().lower() in _CSS:
                kept.append(f"{name.strip().lower()}:{value.strip()}")
        return f'style="{";".join(kept)}"' if kept else ""

    return _STYLE_ATTR.sub(clean, html)


def _extract_body(html):
    """The meaningful part of a pasted HTML document."""
    if not html:
        return ""
    fragment = _DOCTYPE.sub("", html)
    fragment = _HEAD_BLOCK.sub("", fragment)
    match = _BODY_BLOCK.search(fragment)
    if match:
        fragment = match.group("inner")
    else:
        fragment = _HTML_OPEN.sub("", fragment)
    return fragment.strip()


def _sanitize(html):
    """
    Frozen sanitiser.

    Returns "" when nh3 is unavailable. That is the safe direction: the row
    keeps `kind='text'` and its original content, and the owner converts it in
    Settings — better than storing HTML that was never cleaned.
    """
    fragment = _extract_body(html or "")
    if not fragment or nh3 is None:
        return ""
    cleaned = nh3.clean(
        fragment,
        tags=_TAGS,
        attributes={k: set(v) for k, v in _ATTRIBUTES.items()},
        url_schemes=_URL_SCHEMES,
        strip_comments=True,
        link_rel="noopener noreferrer nofollow",
        clean_content_tags=_DROP_CONTENT,
    )
    return _filter_styles(cleaned).strip()


def _to_text(html):
    """Frozen HTML-to-text, for the plain-text alternative."""
    if not html:
        return ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", html)
    text = re.sub(r"(?i)<br\s*/?>[ \t]*\r?\n?", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|li|h[1-6])>[ \t]*\r?\n?", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "  • ", text)
    text = re.sub(r'(?is)<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r"\2 <\1>", text)
    text = re.sub(r"(?s)<[^>]+>", "", text)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
            .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def classify(apps, schema_editor):
    """
    Give every existing signature an explicit kind.

    The document test is deliberately narrow. A signature legitimately reading

        Ops <ops@example.com>
        We ship 24/7 -- <3

    contains angle brackets and is plain text. Reinterpreting it as HTML would
    delete the address as an unknown element and change what the person sends,
    with no way for them to know. A doctype or an <html> tag is unambiguous; a
    stray `<` is not.

    Anything that does not match keeps `kind='text'` and its bytes untouched.
    """
    MailSignature = apps.get_model("postbox", "MailSignature")

    for signature in MailSignature.objects.all().iterator():
        text = signature.text or ""
        html = signature.html or ""

        if html:
            # An API client already populated `html`. Trust the explicit field
            # over a guess about `text`.
            signature.kind = "html"
            if not text:
                signature.text = _to_text(html)
            signature.save(update_fields=["kind", "text"])
            continue

        if _LOOKS_LIKE_DOCUMENT.search(text):
            cleaned = _sanitize(text)
            if cleaned:
                signature.kind = "html"
                signature.html = cleaned
                signature.text = _to_text(cleaned)
                signature.save(update_fields=["kind", "html", "text"])
                continue

        # Plain text, and left exactly as it is.


def unclassify(apps, schema_editor):
    """
    Best-effort reverse. Restores the SHAPE of the old data, not its content.

    The original pasted source is gone — `classify` sanitised it and nothing
    kept a copy — so this puts the sanitised HTML back into `text` and returns
    `kind` to 'text'. Pre-migration code then behaves as it did, reading markup
    out of the text column.

    Not byte-for-byte. See the module docstring.
    """
    MailSignature = apps.get_model("postbox", "MailSignature")

    for signature in MailSignature.objects.filter(kind="html").iterator():
        if signature.html:
            signature.text = signature.html
        signature.kind = "text"
        signature.html = ""
        signature.save(update_fields=["kind", "html", "text"])


class Migration(migrations.Migration):

    dependencies = [
        ("postbox", "0003_signature_kind_and_image"),
    ]

    operations = [
        migrations.RunPython(classify, unclassify),
    ]
