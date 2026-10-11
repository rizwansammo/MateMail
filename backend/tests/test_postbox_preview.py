"""Bounded, non-mutating PostBox list snippets and protected folder/label workflows."""
from unittest import mock

from django.test import SimpleTestCase

from apps.postbox import imap
from apps.postbox.views_mail import _summary_payload


class PostBoxPreviewParsingTests(SimpleTestCase):
    def test_plain_text_and_quoted_printable(self):
        self.assertEqual(
            imap._preview_from_partial(b"Hello=20World=21\nNext line",
                                       "text/plain; charset=utf-8", "quoted-printable"),
            "Hello World! Next line",
        )

    def test_multipart_prefers_plain_text(self):
        raw = (
            b'--abc\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n'
            b'Hello from the first MIME part.\r\n'
            b'--abc\r\nContent-Type: text/html\r\n\r\n<p>Other</p>\r\n'
        )
        value = imap._preview_from_partial(
            raw, 'multipart/alternative; boundary="abc"', '7bit'
        )
        self.assertEqual(value, "Hello from the first MIME part.")

    def test_html_never_shows_script_or_markup(self):
        result = imap._preview_from_partial(
            b'<style>secret-style</style><p>Hello <b>world</b></p>'
            b'<script>secret-script</script><p>Readable text.</p>',
            'text/html; charset=utf-8', '7bit',
        )
        self.assertIn("Hello world", result)
        self.assertIn("Readable text.", result)
        self.assertNotIn("secret-script", result)
        self.assertNotIn("<p>", result)

    def test_plain_reply_excludes_quoted_sender_history(self):
        raw = (
            "Hi Rizwan,\nYes, your signature is working.\nThanks.\n"
            "Regards,\nTeam NetaMate\n"
            "On Sun, Oct 11, 2026 at 10:13 AM NetaMate Solutions "
            "<contact@netamate.com> wrote:\n"
            "> Hello, can you check the signature?\n"
        ).encode("utf-8")
        snippet = imap._preview_from_partial(raw, "text/plain; charset=utf-8", "7bit")
        self.assertIn("Yes, your signature is working.", snippet)
        self.assertNotIn("On Sun", snippet)
        self.assertNotIn("contact@netamate.com", snippet)

    def test_quote_lines_are_not_part_of_snippet(self):
        raw = b"Fresh response.\n> Previous long email\n> Older reply\n"
        self.assertEqual(imap._preview_from_partial(raw, "text/plain", "7bit"), "Fresh response.")

    def test_html_blockquote_is_not_part_of_snippet(self):
        raw = (b"<p>Here is my new answer.</p>"
               b"<blockquote>From: old@example.com<br>Old text</blockquote>")
        snippet = imap._preview_from_partial(raw, "text/html; charset=utf-8", "7bit")
        self.assertEqual(snippet, "Here is my new answer.")

    def test_outlook_quote_header_does_not_leak(self):
        raw = (b"Yes, everything works.\n-----Original Message-----\n"
               b"From: Someone <old@example.com>\nTo: Me\nOld message")
        snippet = imap._preview_from_partial(raw, "text/plain; charset=utf-8", "7bit")
        self.assertEqual(snippet, "Yes, everything works.")

    def test_non_reply_keeps_own_body(self):
        raw = b"We discussed the roadmap on Monday. Please send updates."
        self.assertEqual(imap._preview_from_partial(raw, "text/plain", "7bit"),
                         "We discussed the roadmap on Monday. Please send updates.")

    def test_non_text_and_empty_payload_have_no_snippet(self):
        self.assertEqual(imap._preview_from_partial(b"", "text/plain", "7bit"), "")
        self.assertEqual(imap._preview_from_partial(
            b"binary-bits", "application/octet-stream", "7bit"), "")

    def test_batched_uid_fetch_peeks_only_partial_bytes(self):
        fake = mock.Mock()
        fake.uid.return_value = (
            "OK", [
                (b'1 (UID 11 BODY[TEXT]<0> {17}', b"Visible first row"),
                b")",
                (b'2 (UID 12 BODY[TEXT]<0> {18}', b"Visible second row"),
                b")",
            ],
        )
        conn = imap.MailboxConnection(fake, "test@example.invalid")
        rows = [
            imap.MessageSummary(uid=11, uid_validity=7, folder="INBOX"),
            imap.MessageSummary(uid=12, uid_validity=7, folder="INBOX"),
        ]
        conn.fetch_previews(rows)
        fake.uid.assert_called_once_with(
            "FETCH", "11,12", "(UID BODY.PEEK[TEXT]<0.2048>)"
        )
        self.assertEqual([row.preview for row in rows],
                         ["Visible first row", "Visible second row"])

    def test_failed_preview_is_not_a_failed_mailbox_list(self):
        fake = mock.Mock()
        fake.uid.return_value = ("NO", [])
        conn = imap.MailboxConnection(fake, "test@example.invalid")
        row = imap.MessageSummary(uid=11, uid_validity=7, folder="INBOX")
        conn.fetch_previews([row])
        self.assertEqual(row.preview, "")

    def test_preview_does_not_leak_internal_mime_headers_into_api(self):
        item = imap.MessageSummary(
            uid=33, uid_validity=88, folder="INBOX", subject="Example",
            preview="A bounded preview", mime_type="multipart/alternative",
            transfer_encoding="quoted-printable",
        )
        data = _summary_payload(item)
        self.assertEqual(data["preview"], "A bounded preview")
        self.assertNotIn("mime_type", data)
        self.assertNotIn("transfer_encoding", data)

    def test_visible_page_is_hard_bounded_to_100(self):
        fake = mock.Mock()
        fake.uid.return_value = ("OK", [])
        conn = imap.MailboxConnection(fake, "test@example.invalid")
        rows = [imap.MessageSummary(uid=n + 1, uid_validity=1, folder="INBOX")
                for n in range(110)]
        conn.fetch_previews(rows)
        selected = fake.uid.call_args.args[1].split(",")
        self.assertEqual(len(selected), 100)
        self.assertEqual(selected[0], "1")
        self.assertEqual(selected[-1], "100")
