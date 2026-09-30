from email.message import EmailMessage

from django.test import SimpleTestCase

from apps.postbox import mime


class InlineImageParsingTest(SimpleTestCase):
    def test_cid_image_without_filename_is_kept_as_an_inline_part(self):
        message = EmailMessage()
        message["From"] = "Zoho Team <noreply@example.com>"
        message["To"] = "reader@example.net"
        message["Subject"] = "Inline image"
        message.set_content("Plain fallback")
        message.add_alternative(
            '<html><body><p>Hello</p><img src="cid:brand-logo@example"></body></html>',
            subtype="html",
        )
        html_part = message.get_payload()[1]
        html_part.add_related(
            b"not-a-real-png-but-enough-for-mime-parsing",
            maintype="image",
            subtype="png",
            cid="<brand-logo@example>",
            disposition="inline",
        )

        parsed = mime.parse_message(message.as_bytes())

        image = next(
            part for part in parsed.attachments
            if part.content_id == "brand-logo@example"
        )
        self.assertEqual(image.content_type, "image/png")
        self.assertTrue(image.inline)
        self.assertIn('src="cid:brand-logo@example"', parsed.html)

    def test_unquoted_remote_image_is_blocked_and_reported(self):
        cleaned, blocked = mime.sanitize_html(
            "<p>Hello</p><img src=https://cdn.example.com/logo.png>"
        )

        self.assertTrue(blocked)
        self.assertNotIn("cdn.example.com", cleaned)

    def test_scheme_relative_remote_image_is_blocked_and_reported(self):
        cleaned, blocked = mime.sanitize_html(
            '<p>Hello</p><img src="//cdn.example.com/logo.png">'
        )

        self.assertTrue(blocked)
        self.assertNotIn("cdn.example.com", cleaned)
