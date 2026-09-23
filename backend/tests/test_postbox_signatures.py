"""
Signatures: what gets stored, and what the recipient actually receives.

THE PRODUCTION BUG THIS PINS
    A full HTML signature was pasted into PostBox Settings. The only content
    box there was bound to `text`, so it was stored as plain text and appended
    verbatim to a plain-text body. Gmail displayed `<!doctype html>` and the
    whole source to the recipient.

    Three separate defects produced and surrounded that:

      1. no HTML mode in the UI — covered by the serializer/`kind` tests here
         and by the settings page rewrite;
      2. `html = signature.html if not html`, which made the HTML alternative
         the signature ALONE with no message above it — `BodyAndSignatureTest`;
      3. `<title>` surviving sanitisation as visible words —
         `DocumentWrapperTest`.

    Every test below asserts on the assembled MIME, not on an intermediate
    string, because the MIME is the only thing a recipient sees.
"""
from email import message_from_bytes
from email.policy import default as default_policy

from django.test import TestCase, override_settings

from apps.postbox import mime, signatures
from apps.postbox.models import MailSignature, SignatureKind
from tests.factories import make_domain, make_mailbox, make_tenant, make_user

BODY = "Hello,\nThis is our PostBox email test.\nThanks,"

FULL_DOCUMENT = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Rizwan Signature</title>
  <style>body { color: red } .sig { font-weight: bold }</style>
</head>
<body>
  <div class="sig">
    <strong>Rizwan Sammo</strong><br>
    Chief Business Officer<br>
    NetaMate Solutions
  </div>
</body>
</html>"""

#: The smallest real PNG: 1x1, transparent. Used so the upload path is
#: exercised with bytes that genuinely are a PNG rather than a string we
#: asserted was one.
PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6360000002000100ffff0300000600"
    "0557bfabd40000000049454e44ae426082"
)


class SignatureTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        owner = make_user("owner@acme.example")
        cls.tenant = make_tenant(owner)
        cls.domain = make_domain(cls.tenant, "acme.example")
        cls.mailbox = make_mailbox(cls.tenant, cls.domain, local_part="alice")

    def make_signature(self, **kwargs):
        kwargs.setdefault("mailbox", self.mailbox)
        kwargs.setdefault("name", "Test")
        return MailSignature.objects.create(**kwargs)

    def assemble(self, signature, *, text=BODY, html=""):
        """The message a recipient would get, parsed back from bytes."""
        out_text, out_html, related = signatures.apply(
            text=text, html=html, signature=signature
        )
        message = mime.build_message(
            from_address="alice@acme.example",
            to=["someone@elsewhere.example"],
            subject="Test",
            text=out_text,
            html=out_html,
            related=related,
        )
        return message_from_bytes(message.as_bytes(), policy=default_policy)

    @staticmethod
    def part(parsed, content_type):
        for part in parsed.walk():
            if part.get_content_type() == content_type:
                return part
        return None

    def body_of(self, parsed, content_type):
        part = self.part(parsed, content_type)
        self.assertIsNotNone(part, f"no {content_type} part")
        return part.get_content()


class DocumentWrapperTest(SignatureTestBase):
    """A pasted document must not arrive as words."""

    def test_title_does_not_become_visible_text(self):
        """
        The regression. nh3 removes a disallowed TAG but keeps its TEXT, and
        its `clean_content_tags` defaults to {script, style} — so <style> was
        already handled and <title> was not. "Rizwan Signature" appeared at the
        top of the signature in every message.
        """
        cleaned = mime.sanitize_signature(FULL_DOCUMENT)
        self.assertNotIn("Rizwan Signature", cleaned)

    def test_style_rules_do_not_become_visible_text(self):
        cleaned = mime.sanitize_signature(FULL_DOCUMENT)
        self.assertNotIn("color: red", cleaned)
        self.assertNotIn("font-weight: bold", cleaned)

    def test_document_structure_is_gone(self):
        cleaned = mime.sanitize_signature(FULL_DOCUMENT).lower()
        for marker in ("<!doctype", "<html", "<head", "<meta", "<body", "<title"):
            self.assertNotIn(marker, cleaned, f"{marker} survived")

    def test_the_actual_signature_survives(self):
        """Stripping the wrapper must not strip the content."""
        cleaned = mime.sanitize_signature(FULL_DOCUMENT)
        self.assertIn("Rizwan Sammo", cleaned)
        self.assertIn("Chief Business Officer", cleaned)
        self.assertIn("NetaMate Solutions", cleaned)
        self.assertIn("<strong>", cleaned)

    def test_a_plain_fragment_is_unharmed(self):
        """Most signatures are not documents; they must pass through."""
        cleaned = mime.sanitize_signature("<strong>Riz</strong><br>CBO")
        self.assertEqual("<strong>Riz</strong><br>CBO", cleaned)

    def test_malformed_html_does_not_raise(self):
        for broken in (
            "<div><strong>unclosed",
            "<<<>>>",
            "<body><p>no head</body></html>",
            "<!doctype html><html><head><title>x",
            "",
            "<div style=>x</div>",
        ):
            mime.sanitize_signature(broken)  # must not raise


class BodyAndSignatureTest(SignatureTestBase):
    """
    The HTML alternative must contain the message, not only the signature.
    """

    def test_html_signature_keeps_the_body_in_the_html_part(self):
        """
        The defect: `html = signature.html if not html`. PostBox's composer
        only sends plain text, so the false branch always ran and the HTML part
        became the signature by itself. Every client prefers HTML, so the
        recipient would have seen a signature and no message.
        """
        signature = self.make_signature(
            kind=SignatureKind.HTML,
            html=mime.sanitize_signature(FULL_DOCUMENT),
            text="Rizwan Sammo\nChief Business Officer",
        )
        parsed = self.assemble(signature)

        html = self.body_of(parsed, "text/html")
        self.assertIn("This is our PostBox email test.", html)
        self.assertIn("Rizwan Sammo", html)
        # Body before signature, not the other way round.
        self.assertLess(html.index("PostBox email test"), html.index("Rizwan Sammo"))

    def test_the_body_is_escaped_not_injected(self):
        """A body is text. It must not be able to introduce markup."""
        signature = self.make_signature(
            kind=SignatureKind.HTML, html="<strong>Riz</strong>", text="Riz"
        )
        parsed = self.assemble(signature, text="1 < 2 & <script>alert(1)</script>")

        html = self.body_of(parsed, "text/html")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("1 &lt; 2 &amp;", html)

    def test_newlines_become_line_breaks(self):
        signature = self.make_signature(
            kind=SignatureKind.HTML, html="<strong>Riz</strong>", text="Riz"
        )
        html = self.body_of(self.assemble(signature), "text/html")
        self.assertIn("Hello,<br>This is our PostBox email test.", html)

    def test_plain_text_alternative_has_body_then_signature(self):
        signature = self.make_signature(
            kind=SignatureKind.HTML,
            html="<strong>Rizwan Sammo</strong><br>Chief Business Officer",
            text="Rizwan Sammo\nChief Business Officer",
        )
        text = self.body_of(self.assemble(signature), "text/plain")
        self.assertIn("This is our PostBox email test.", text)
        self.assertIn("Rizwan Sammo", text)
        self.assertLess(text.index("PostBox email test"), text.index("Rizwan Sammo"))

    def test_raw_markup_never_appears_in_the_text_alternative(self):
        """What the recipient actually reported seeing."""
        signature = self.make_signature(
            kind=SignatureKind.HTML,
            html=mime.sanitize_signature(FULL_DOCUMENT),
            text=mime.html_to_text(mime.sanitize_signature(FULL_DOCUMENT)),
        )
        text = self.body_of(self.assemble(signature), "text/plain")
        for marker in ("<!doctype", "<html", "<head", "<style", "<div", "<strong"):
            self.assertNotIn(marker, text.lower(), f"{marker} leaked into text/plain")
        self.assertIn("Rizwan Sammo", text)

    def test_a_derived_fallback_is_readable(self):
        """§5's example, end to end."""
        html = "<strong>Rizwan Sammo</strong><br>Chief Business Officer<br>NetaMate Solutions"
        self.assertEqual(
            "Rizwan Sammo\nChief Business Officer\nNetaMate Solutions",
            mime.html_to_text(html),
        )


class PlainTextSignatureTest(SignatureTestBase):
    def test_text_signature_on_text_body_stays_plain(self):
        """
        No HTML alternative invented for a message that has no HTML in it.
        An alternative whose two halves say the same thing is noise, and a
        needless HTML part costs deliverability.
        """
        signature = self.make_signature(kind=SignatureKind.TEXT, text="Riz\nCBO")
        parsed = self.assemble(signature)

        self.assertEqual("text/plain", parsed.get_content_type())
        self.assertIsNone(self.part(parsed, "text/html"))
        body = parsed.get_content()
        self.assertIn("PostBox email test", body)
        self.assertIn("Riz\nCBO", body)

    def test_markup_typed_into_text_mode_is_text(self):
        """
        §2: pasting `<p>Hello</p>` into Plain Text mode is intentionally text.
        It must survive to the recipient as those characters, and it must not
        become markup in the HTML alternative either.
        """
        signature = self.make_signature(kind=SignatureKind.TEXT, text="<p>Hello</p>")
        parsed = self.assemble(signature, text=BODY, html="<p>body</p>")

        self.assertIn("<p>Hello</p>", self.body_of(parsed, "text/plain"))
        html = self.body_of(parsed, "text/html")
        self.assertIn("&lt;p&gt;Hello&lt;/p&gt;", html)

    def test_text_signature_joins_an_html_body_when_one_exists(self):
        signature = self.make_signature(kind=SignatureKind.TEXT, text="Riz")
        html = self.body_of(self.assemble(signature, html="<p>Body</p>"), "text/html")
        self.assertIn("<p>Body</p>", html)
        self.assertIn("Riz", html)


class ImageSignatureTest(SignatureTestBase):
    def setUp(self):
        self.signature = self.make_signature(
            kind=SignatureKind.IMAGE,
            image_data=PNG_1PX,
            image_content_type="image/png",
            image_alt="NetaMate Solutions logo",
            text="NetaMate Solutions logo",
        )

    def test_mime_structure_is_alternative_over_related(self):
        """
        §6's required shape. `add_related` has to be called on the HTML PART;
        calling it on the message would make the image an ordinary attachment
        and the signature would render as a broken-image box beside a paperclip.
        """
        parsed = self.assemble(self.signature)
        self.assertEqual("multipart/alternative", parsed.get_content_type())

        kinds = [p.get_content_type() for p in parsed.iter_parts()]
        self.assertIn("text/plain", kinds)
        self.assertIn("multipart/related", kinds)

        related = self.part(parsed, "multipart/related")
        inner = [p.get_content_type() for p in related.iter_parts()]
        self.assertEqual(["text/html", "image/png"], inner)

    def test_html_references_the_image_by_cid(self):
        parsed = self.assemble(self.signature)
        html = self.body_of(parsed, "text/html")
        image = self.part(parsed, "image/png")

        cid = image["Content-ID"].strip("<>")
        self.assertIn(f'src="cid:{cid}"', html)

    def test_image_is_inline_not_an_attachment(self):
        image = self.part(self.assemble(self.signature), "image/png")
        self.assertEqual("inline", image.get_content_disposition())

    def test_alt_text_is_present_and_escaped(self):
        self.signature.image_alt = 'Acme "Ltd" & Co <logo>'
        self.signature.save()
        html = self.body_of(self.assemble(self.signature), "text/html")
        self.assertIn("&lt;logo&gt;", html)
        self.assertNotIn("<logo>", html)

    def test_alt_text_is_the_plain_text_alternative(self):
        """A picture has no words. Without alt, the text part loses the name."""
        text = self.body_of(self.assemble(self.signature), "text/plain")
        self.assertIn("NetaMate Solutions logo", text)

    def test_the_body_is_still_in_the_html_part(self):
        html = self.body_of(self.assemble(self.signature), "text/html")
        self.assertIn("This is our PostBox email test.", html)

    def test_no_data_url_is_emitted(self):
        """
        §6 forbids it, and so do Gmail and Outlook, which strip data: images.
        """
        html = self.body_of(self.assemble(self.signature), "text/html")
        self.assertNotIn("data:image", html)
        self.assertNotIn("base64,", html)

    def test_each_send_gets_a_fresh_content_id(self):
        """A reused cid lets a client cache one message's image into another."""
        first = self.part(self.assemble(self.signature), "image/png")["Content-ID"]
        second = self.part(self.assemble(self.signature), "image/png")["Content-ID"]
        self.assertNotEqual(first, second)

    def test_an_image_signature_with_no_bytes_adds_nothing(self):
        empty = self.make_signature(
            kind=SignatureKind.IMAGE, name="Empty", image_alt="x", text="x"
        )
        out_text, out_html, related = signatures.apply(
            text=BODY, html="", signature=empty
        )
        self.assertEqual([], related)
        self.assertEqual(BODY, out_text)


class SecurityTest(SignatureTestBase):
    """The allow-list must not have regressed."""

    def test_script_is_removed(self):
        cleaned = mime.sanitize_signature("<div>Hi<script>alert(1)</script></div>")
        self.assertNotIn("<script", cleaned)
        self.assertNotIn("alert(1)", cleaned)

    def test_event_handlers_are_removed(self):
        cleaned = mime.sanitize_signature('<div onclick="steal()">Hi</div>')
        self.assertNotIn("onclick", cleaned)
        self.assertIn("Hi", cleaned)

    def test_javascript_urls_are_removed(self):
        cleaned = mime.sanitize_signature('<a href="javascript:alert(1)">Click</a>')
        self.assertNotIn("javascript:", cleaned)

    def test_embedding_tags_are_removed(self):
        for tag in (
            '<iframe src="https://evil.example"></iframe>',
            '<object data="x.swf"></object>',
            '<embed src="x.swf">',
            '<form action="https://evil.example"><input name="p"></form>',
        ):
            cleaned = mime.sanitize_signature(f"<div>{tag}</div>")
            for marker in ("<iframe", "<object", "<embed", "<form", "<input"):
                self.assertNotIn(marker, cleaned.lower())

    def test_overlay_css_is_removed(self):
        """
        `position: fixed` with a z-index is how a signature would cover the
        application's own interface in the reader's window.
        """
        cleaned = mime.sanitize_signature(
            '<div style="position:fixed;top:0;left:0;z-index:9999;color:red">Hi</div>'
        )
        for prop in ("position", "z-index", "top", "left"):
            self.assertNotIn(prop, cleaned)
        self.assertIn("color", cleaned)

    def test_safe_inline_css_survives(self):
        cleaned = mime.sanitize_signature(
            '<div style="color:#333;font-size:12px;font-weight:bold">Riz</div>'
        )
        self.assertIn("color", cleaned)
        self.assertIn("font-size", cleaned)

    def test_table_layout_survives(self):
        """Real signatures are tables. Removing them would break every one."""
        cleaned = mime.sanitize_signature(
            '<table cellpadding="0" cellspacing="0"><tr>'
            '<td valign="top"><strong>Riz</strong></td></tr></table>'
        )
        for marker in ("<table", "<tr", "<td", "cellpadding", "valign"):
            self.assertIn(marker, cleaned)

    def test_links_survive_with_a_forced_rel(self):
        cleaned = mime.sanitize_signature('<a href="https://netamate.com">Site</a>')
        self.assertIn('href="https://netamate.com"', cleaned)
        self.assertIn("noopener", cleaned)

    def test_remote_https_images_are_kept_in_signatures(self):
        """
        Deliberate and different from message bodies: this is the owner's own
        logo, chosen by them, not a tracking pixel from a stranger.
        """
        cleaned = mime.sanitize_signature('<img src="https://cdn.example/logo.png" alt="L">')
        self.assertIn("https://cdn.example/logo.png", cleaned)

    def test_data_urls_are_refused(self):
        cleaned = mime.sanitize_signature(
            '<img src="data:image/svg+xml;base64,PHN2Zz48c2NyaXB0Lz48L3N2Zz4=" alt="x">'
        )
        self.assertNotIn("data:", cleaned)


class MailboxIsolationTest(SignatureTestBase):
    def test_another_mailboxes_signature_is_not_found(self):
        """
        A signature id is a UUID supplied by the caller. An unscoped lookup
        would let one mailbox stamp another's logo and job title on its mail.
        """
        other_mailbox = make_mailbox(self.tenant, self.domain, local_part="bob")
        theirs = MailSignature.objects.create(
            mailbox=other_mailbox, name="Bob", kind=SignatureKind.TEXT, text="Bob"
        )
        self.assertIsNone(signatures.for_mailbox(self.mailbox, theirs.id))
        self.assertIsNotNone(signatures.for_mailbox(other_mailbox, theirs.id))

    def test_no_signature_selected_changes_nothing(self):
        out_text, out_html, related = signatures.apply(
            text=BODY, html="", signature=None
        )
        self.assertEqual(BODY, out_text)
        self.assertEqual("", out_html)
        self.assertEqual([], related)

    def test_applied_exactly_once(self):
        """
        The composer shows a preview but never submits the signature as body
        text. Assembling twice from the same payload must not accumulate.
        """
        signature = self.make_signature(kind=SignatureKind.TEXT, text="SIGNATURE")
        first, _, _ = signatures.apply(text=BODY, html="", signature=signature)
        self.assertEqual(1, first.count("SIGNATURE"))

        # Re-running on the ORIGINAL payload, as a re-saved draft does.
        second, _, _ = signatures.apply(text=BODY, html="", signature=signature)
        self.assertEqual(1, second.count("SIGNATURE"))
        self.assertEqual(first, second)


class DefaultSelectionTest(SignatureTestBase):
    def test_new_and_reply_defaults_are_separate(self):
        new = self.make_signature(
            name="New", kind=SignatureKind.TEXT, text="N", use_for_new=True
        )
        reply = self.make_signature(
            name="Reply", kind=SignatureKind.TEXT, text="R", use_for_replies=True
        )
        self.assertEqual(new, signatures.default_for(self.mailbox, replying=False))
        self.assertEqual(reply, signatures.default_for(self.mailbox, replying=True))

    def test_no_default_is_not_an_error(self):
        self.assertIsNone(signatures.default_for(self.mailbox, replying=False))
        self.assertIsNone(signatures.default_for(self.mailbox, replying=True))


@override_settings(POSTBOX_SIGNATURE_IMAGE_KB=256, POSTBOX_SIGNATURE_IMAGE_MAX_PX=1200)
class ImageValidationTest(SignatureTestBase):
    """The upload is checked as bytes, not as a declared content type."""

    def test_magic_bytes_decide_the_type(self):
        from apps.postbox.views_settings import _sniff_image

        self.assertEqual(("image/png", "png"), _sniff_image(PNG_1PX))
        self.assertEqual(("image/jpeg", "jpg"), _sniff_image(b"\xff\xd8\xff\xe0rest"))
        self.assertEqual(("image/gif", "gif"), _sniff_image(b"GIF89a....."))
        self.assertEqual(
            ("image/webp", "webp"), _sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
        )

    def test_a_declared_type_cannot_launder_a_non_image(self):
        """
        `Content-Type: image/png` on an HTML file is a claim, not a fact. The
        bytes are what gets served back on the PostBox origin later.
        """
        from apps.postbox.views_settings import _sniff_image

        self.assertIsNone(_sniff_image(b"<html><script>alert(1)</script></html>"))
        self.assertIsNone(_sniff_image(b"<svg xmlns='http://www.w3.org/2000/svg'/>"))
        self.assertIsNone(_sniff_image(b"GIF"))
        self.assertIsNone(_sniff_image(b""))

    def test_svg_is_not_an_accepted_image(self):
        """A document that can carry script is not an image signature."""
        from apps.postbox.views_settings import _sniff_image

        self.assertIsNone(_sniff_image(b"<?xml version='1.0'?><svg><script/></svg>"))

    def test_png_dimensions_are_read_without_decoding(self):
        from apps.postbox.views_settings import _image_size

        self.assertEqual((1, 1), _image_size(PNG_1PX))

    def test_unreadable_dimensions_do_not_raise(self):
        from apps.postbox.views_settings import _image_size

        for payload in (b"", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF"):
            self.assertIsNone(_image_size(payload))


class BackwardCompatibilityTest(SignatureTestBase):
    """
    Callers written before `kind` existed must keep working.

    Found by breaking it: the first version of this change defaulted a missing
    `kind` to text and then cleared `html`, so an API client that posted HTML
    without naming a kind had it silently discarded — the same class of bug as
    the one being fixed, introduced by the fix.
    """

    def serializer(self, data, instance=None):
        from apps.postbox.serializers import SignatureSerializer

        serializer = SignatureSerializer(instance, data=data, partial=instance is not None)
        serializer.is_valid(raise_exception=True)
        return serializer

    def test_html_without_a_kind_is_treated_as_html(self):
        serializer = self.serializer({"name": "Legacy", "html": "<p>Hi</p>"})
        signature = serializer.save(mailbox=self.mailbox)

        self.assertEqual(SignatureKind.HTML, signature.kind)
        self.assertEqual("<p>Hi</p>", signature.html)

    def test_an_explicit_text_kind_still_wins_over_html(self):
        """A caller that contradicts itself gets the answer it asked for."""
        serializer = self.serializer(
            {"name": "Contradiction", "kind": "text", "text": "Plain", "html": "<p>Hi</p>"}
        )
        signature = serializer.save(mailbox=self.mailbox)

        self.assertEqual(SignatureKind.TEXT, signature.kind)
        self.assertEqual("", signature.html)
        self.assertEqual("Plain", signature.text)

    def test_a_supplied_text_is_not_overwritten_by_the_derived_one(self):
        """The optional advanced override in §5."""
        serializer = self.serializer(
            {"name": "Override", "html": "<p>Rendered</p>", "text": "Hand written"}
        )
        signature = serializer.save(mailbox=self.mailbox)
        self.assertEqual("Hand written", signature.text)

    def test_patching_an_unrelated_field_keeps_the_kind(self):
        signature = self.make_signature(
            kind=SignatureKind.HTML, html="<p>Hi</p>", text="Hi"
        )
        updated = self.serializer({"use_for_new": True}, instance=signature).save()

        self.assertEqual(SignatureKind.HTML, updated.kind)
        self.assertEqual("<p>Hi</p>", updated.html)

    def test_text_only_caller_is_unchanged(self):
        serializer = self.serializer({"name": "Old", "text": "Riz\nCBO"})
        signature = serializer.save(mailbox=self.mailbox)

        self.assertEqual(SignatureKind.TEXT, signature.kind)
        self.assertEqual("Riz\nCBO", signature.text)


class RealSignatureRenderingTest(SignatureTestBase):
    """
    The signature that exposed this: a table layout with panel backgrounds, a
    remote logo, links and coloured text.
    """

    RAW = (
        '<table cellpadding="0" cellspacing="0" border="0" '
        'style="border-collapse:collapse;width:100%;max-width:620px;'
        'background:#f8fafc;border:1px solid #e5e7eb;'
        'font-family:Arial,Helvetica,sans-serif;">'
        "<tr><td style=\"padding:18px;\">"
        '<table style="background:#0A0B0D;width:96px;height:96px;"><tr>'
        '<td align="center" valign="middle" style="padding:12px;">'
        '<img src="https://netamate.com/static/img/logo.png" width="72" '
        'alt="NetaMate Solutions" '
        'style="display:block;width:72px;height:auto;border:0;">'
        "</td></tr></table>"
        '<div style="font-size:16px;font-weight:700;color:#111827;">Rizwan Sammo</div>'
        '<div style="color:#0B4FE0;letter-spacing:0.8px;text-transform:uppercase;">'
        "Chief Business Officer</div>"
        '<a href="mailto:rizwan@netamate.com" style="color:#4b5563;">rizwan@netamate.com</a>'
        '<a href="https://netamate.com" style="color:#4b5563;">netamate.com</a>'
        "</td></tr></table>"
    )

    def test_panel_backgrounds_survive(self):
        """
        The defect. `background-color` was allowed and `background` was not, so
        every signature written the way real signatures are written lost its
        panel colours — in the SENT MESSAGE as well as the preview.
        """
        cleaned = mime.sanitize_signature(self.RAW)
        self.assertIn("background:#f8fafc", cleaned)
        self.assertIn("background:#0A0B0D", cleaned)

    def test_the_remote_logo_survives(self):
        cleaned = mime.sanitize_signature(self.RAW)
        self.assertIn("https://netamate.com/static/img/logo.png", cleaned)
        self.assertIn('alt="NetaMate Solutions"', cleaned)

    def test_layout_and_typography_survive(self):
        cleaned = mime.sanitize_signature(self.RAW)
        for expected in (
            "<table", "<td", 'align="center"', 'valign="middle"',
            "max-width:620px", "letter-spacing", "text-transform",
            "color:#111827", "font-family:Arial",
        ):
            self.assertIn(expected, cleaned, f"{expected} was stripped")

    def test_both_links_survive(self):
        cleaned = mime.sanitize_signature(self.RAW)
        self.assertIn("mailto:rizwan@netamate.com", cleaned)
        self.assertIn("https://netamate.com", cleaned)
        self.assertIn("noopener", cleaned)

    def test_it_reaches_the_sent_message_intact(self):
        """Allowing `background` must not have changed the MIME path."""
        signature = self.make_signature(
            kind=SignatureKind.HTML,
            html=mime.sanitize_signature(self.RAW),
            text=mime.html_to_text(mime.sanitize_signature(self.RAW)),
        )
        parsed = self.assemble(signature)
        html = self.body_of(parsed, "text/html")

        self.assertIn("This is our PostBox email test.", html)
        self.assertIn("background:#f8fafc", html)
        self.assertIn("https://netamate.com/static/img/logo.png", html)

        text = self.body_of(parsed, "text/plain")
        self.assertIn("Rizwan Sammo", text)
        self.assertNotIn("<table", text)


class BackgroundValueSafetyTest(SignatureTestBase):
    """Allowing the shorthand must not allow what it can carry."""

    def test_a_colour_passes(self):
        for value in ("#f8fafc", "rgb(248,250,252)", "#0A0B0D", "transparent"):
            cleaned = mime.sanitize_signature(f'<div style="background:{value}">x</div>')
            self.assertIn("background:", cleaned, value)

    def test_a_scripted_url_is_rejected(self):
        cleaned = mime.sanitize_signature(
            '<div style="background:url(javascript:alert(1))">x</div>'
        )
        self.assertNotIn("background", cleaned)
        self.assertNotIn("javascript", cleaned)

    def test_a_remote_css_image_is_rejected(self):
        """
        Deliberately different from an <img>. A CSS background image cannot be
        turned off by the reader's remote-image control, so it would be a
        tracking pixel the preference cannot reach.
        """
        cleaned = mime.sanitize_signature(
            '<div style="background:url(https://evil.example/p.png)">x</div>'
        )
        self.assertNotIn("evil.example", cleaned)

    def test_expression_and_import_are_rejected(self):
        for value in ("expression(alert(1))", "url('data:text/html,<script>')"):
            cleaned = mime.sanitize_signature(f'<div style="background:{value}">x</div>')
            self.assertNotIn("background", cleaned)

    def test_overlay_css_is_still_rejected_alongside_a_background(self):
        cleaned = mime.sanitize_signature(
            '<div style="background:#fff;position:fixed;z-index:9999">x</div>'
        )
        self.assertIn("background:#fff", cleaned)
        self.assertNotIn("position", cleaned)
        self.assertNotIn("z-index", cleaned)
