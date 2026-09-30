"""Regression coverage for serious PostBox reader/rendering issues."""

from email.message import EmailMessage

from django.test import SimpleTestCase

from apps.postbox import mime


class PostBoxMimeRenderingRegressionTest(SimpleTestCase):
    def test_inline_cid_image_without_filename_is_preserved_as_inline_part(self):
        message = EmailMessage()
        message["From"] = "sender@example.net"
        message["To"] = "alice@example.com"
        message["Subject"] = "CID image"
        message.set_content("Fallback")
        message.add_alternative(
            '<html><body><p>Hello</p><img src="cid:logo@example.net"></body></html>',
            subtype="html",
        )
        html_part = message.get_payload()[1]
        html_part.add_related(
            b"\x89PNG\r\n\x1a\nnot-a-real-image-but-valid-for-parser",
            maintype="image",
            subtype="png",
            cid="<logo@example.net>",
            disposition="inline",
        )

        parsed = mime.parse_message(message.as_bytes())

        self.assertEqual(1, len(parsed.attachments))
        image = parsed.attachments[0]
        self.assertEqual("image/png", image.content_type)
        self.assertEqual("logo@example.net", image.content_id)
        self.assertTrue(image.inline)
        self.assertIn("cid:logo@example.net", parsed.html)
        self.assertFalse(parsed.has_remote_images)

    def test_content_id_alone_marks_non_text_part_inline(self):
        message = EmailMessage()
        message["From"] = "sender@example.net"
        message["To"] = "alice@example.com"
        message.set_content("Fallback")
        message.add_alternative('<img src="cid:bare@example.net">', subtype="html")
        html_part = message.get_payload()[1]
        html_part.add_related(
            b"gif",
            maintype="image",
            subtype="gif",
            cid="<bare@example.net>",
        )
        related = html_part.get_payload()[1]
        if "Content-Disposition" in related:
            del related["Content-Disposition"]

        parsed = mime.parse_message(message.as_bytes())

        image = parsed.attachments[0]
        self.assertTrue(image.inline)
        self.assertEqual("bare@example.net", image.content_id)

    def test_protocol_relative_and_unquoted_remote_images_are_blocked(self):
        for html in (
            '<img src="//images.example.net/open.png">',
            '<img src=https://images.example.net/open.png>',
            '<table background="//images.example.net/bg.png"><tr><td>x</td></tr></table>',
        ):
            cleaned, blocked = mime.sanitize_html(
                html,
                load_remote_images=False,
            )
            self.assertTrue(blocked, html)
            self.assertNotIn("images.example.net", cleaned)
            self.assertNotIn('src=""', cleaned)

