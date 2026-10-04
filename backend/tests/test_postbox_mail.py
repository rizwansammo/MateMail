"""
The parts of PostBox where a bug is dangerous rather than merely wrong:
HTML sanitisation, attachment filenames, sender authorisation, header
injection, and scheduled-send idempotency.
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.domains.models import Domain
from apps.mailboxes.models import Mailbox, MailboxStatus
from apps.postbox import mime, sending
from apps.postbox.models import ScheduledMessage
from apps.postbox.sieve import compile_rules
from tests.factories import FAST_PASSWORD_HASHERS, make_tenant, make_user


class HtmlSanitisationTest(TestCase):
    """
    Email HTML is the most hostile input MateMail renders.

    Each case below is a real technique, not a hypothetical: they are the
    payloads that defeat naive filters, which is why the sanitiser is a real
    HTML parser rather than a set of replacements.
    """

    def clean(self, html: str) -> str:
        cleaned, _ = mime.sanitize_html(html, load_remote_images=True)
        return cleaned

    def test_script_tags_are_removed(self):
        cleaned = self.clean('<p>hi</p><script>fetch("/api/postbox/auth/me/")</script>')
        self.assertNotIn("script", cleaned.lower())
        self.assertIn("hi", cleaned)

    def test_event_handlers_are_removed(self):
        for payload in (
            '<img src="x" onerror="alert(1)">',
            '<div onclick="alert(1)">click</div>',
            '<body onload="alert(1)">',
            '<svg onload="alert(1)"></svg>',
        ):
            with self.subTest(payload=payload):
                cleaned = self.clean(payload)
                self.assertNotIn("onerror", cleaned.lower())
                self.assertNotIn("onclick", cleaned.lower())
                self.assertNotIn("onload", cleaned.lower())

    def test_javascript_urls_are_removed(self):
        for payload in (
            '<a href="javascript:alert(1)">x</a>',
            '<a href="JaVaScRiPt:alert(1)">x</a>',
            '<a href="&#106;avascript:alert(1)">x</a>',
        ):
            with self.subTest(payload=payload):
                self.assertNotIn("javascript", self.clean(payload).lower())

    def test_data_urls_are_removed(self):
        """A data: URL can carry an SVG document containing script."""
        cleaned = self.clean('<img src="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=">')
        self.assertNotIn("data:", cleaned)

    def test_frames_objects_and_forms_are_removed(self):
        for tag in ("iframe", "object", "embed", "form", "input", "button", "base"):
            with self.subTest(tag=tag):
                self.assertNotIn(f"<{tag}", self.clean(f"<{tag}></{tag}>").lower())

    def test_css_cannot_escape_the_reader(self):
        """
        An email that positions itself over the application is a phishing
        surface. `position` is not on the CSS allow-list at all.
        """
        cleaned = self.clean(
            '<div style="position:fixed;top:0;left:0;width:100%;'
            'height:100%;z-index:9999;color:red">overlay</div>'
        )
        self.assertNotIn("position", cleaned)
        self.assertNotIn("z-index", cleaned)
        # ...while an ordinary declaration survives, or mail is unreadable.
        self.assertIn("color", cleaned)

    def test_css_expressions_and_remote_urls_are_removed(self):
        cleaned = self.clean(
            '<div style="width:expression(alert(1));'
            'background:url(https://tracker.test/p.gif)">x</div>'
        )
        self.assertNotIn("expression", cleaned)
        self.assertNotIn("tracker.test", cleaned)

    def test_links_are_given_safe_rel(self):
        cleaned = self.clean('<a href="https://example.test">x</a>')
        self.assertIn("noopener", cleaned)
        self.assertIn("noreferrer", cleaned)

    # ── remote content ──────────────────────────────────────────────────────

    def test_remote_images_are_blocked_by_default(self):
        cleaned, blocked = mime.sanitize_html(
            '<img src="https://tracker.test/pixel.gif">', load_remote_images=False
        )
        self.assertNotIn("tracker.test", cleaned)
        self.assertTrue(blocked)

    def test_remote_images_load_when_the_reader_asks(self):
        cleaned, blocked = mime.sanitize_html(
            '<img src="https://cdn.test/logo.png">', load_remote_images=True
        )
        self.assertIn("cdn.test", cleaned)
        self.assertFalse(blocked)

    def test_a_message_without_remote_content_does_not_claim_blocking(self):
        """Otherwise every message offers a button that would do nothing."""
        _, blocked = mime.sanitize_html("<p>plain</p>", load_remote_images=False)
        self.assertFalse(blocked)

    def test_inline_cid_images_survive_blocking(self):
        """They are part of the message, not a remote reference."""
        cleaned, _ = mime.sanitize_html(
            '<img src="cid:logo@example">', load_remote_images=False
        )
        self.assertIn("cid:", cleaned)


class AttachmentFilenameTest(TestCase):
    def test_path_traversal_is_neutralised(self):
        for payload in (
            "../../../../etc/passwd",
            "..\\..\\windows\\system32\\config\\sam",
            "/etc/shadow",
            "....//....//etc/hosts",
        ):
            with self.subTest(payload=payload):
                safe = mime.safe_filename(payload)
                self.assertNotIn("/", safe)
                self.assertNotIn("\\", safe)
                self.assertFalse(safe.startswith("."))

    def test_header_injection_via_filename_is_neutralised(self):
        safe = mime.safe_filename('evil"\r\nContent-Type: text/html\r\n\r\n<script>')
        self.assertNotIn("\r", safe)
        self.assertNotIn("\n", safe)
        self.assertNotIn('"', safe)

    def test_windows_device_names_are_defused(self):
        for name in ("CON", "PRN.txt", "NUL", "COM1.doc"):
            with self.subTest(name=name):
                self.assertTrue(mime.safe_filename(name).startswith("_"))

    def test_an_empty_name_still_produces_something(self):
        self.assertTrue(mime.safe_filename(""))
        self.assertTrue(mime.safe_filename("   "))


class HeaderInjectionTest(TestCase):
    def test_newlines_cannot_be_smuggled_into_a_subject(self):
        """
        A newline in a header value ends the header and begins another — that
        is how an attacker adds `Bcc:` to somebody else's message.

        What must be true is that the value becomes ONE header line. The words
        survive as text, which is harmless: a subject reading
        "Hello Bcc: attacker@evil.test" is a strange subject, not an injected
        header. The test therefore checks for the newline and for the absence
        of a real Bcc header, not for the absence of the words.
        """
        message = mime.build_message(
            from_address="a@acme.test",
            to=["b@acme.test"],
            subject="Hello\r\nBcc: attacker@evil.test",
            text="body",
        )
        subject = str(message["Subject"])
        self.assertNotIn("\r", subject)
        self.assertNotIn("\n", subject)
        self.assertIsNone(message["Bcc"])

        # And the rendered message must not contain a Bcc header either, which
        # is what an injection would actually have produced.
        rendered = message.as_string()
        self.assertNotRegex(rendered, r"(?im)^Bcc:")

    def test_bcc_is_never_written_as_a_header(self):
        """It would be delivered to everyone, disclosing exactly who it hides."""
        message = mime.build_message(
            from_address="a@acme.test",
            to=["b@acme.test"],
            bcc=["hidden@acme.test"],
            subject="x",
            text="body",
        )
        self.assertIsNone(message["Bcc"])
        self.assertNotIn("hidden@acme.test", message.as_string())

    def test_bcc_still_receives_through_the_envelope(self):
        recipients = sending.envelope_recipients(
            ["b@acme.test"], ["c@acme.test"], ["hidden@acme.test"]
        )
        self.assertIn("hidden@acme.test", recipients)

    def test_recipients_are_deduplicated(self):
        recipients = sending.envelope_recipients(
            ["a@x.test", "A@X.test"], ["a@x.test"], []
        )
        self.assertEqual(1, len(recipients))

    def test_a_message_carries_a_message_id_and_date(self):
        message = mime.build_message(
            from_address="a@acme.test", to=["b@acme.test"], subject="x", text="y"
        )
        self.assertTrue(message["Message-ID"])
        self.assertTrue(message["Date"])

    def test_a_reply_carries_threading_headers(self):
        message = mime.build_message(
            from_address="a@acme.test", to=["b@acme.test"], subject="Re: x",
            text="y", in_reply_to="<original@acme.test>",
            references=["<older@acme.test>"],
        )
        self.assertEqual("<original@acme.test>", message["In-Reply-To"])
        self.assertIn("<older@acme.test>", message["References"])
        self.assertIn("<original@acme.test>", message["References"])


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class SenderAuthorisationTest(TestCase):
    def setUp(self):
        self.owner = make_user("owner@acme.test")
        self.tenant = make_tenant(self.owner, name="Acme", slug="acme")
        self.domain = Domain.objects.create(
            tenant=self.tenant, domain="acme.test", status="active",
            ownership_status="verified", mail_engine_provisioned=True,
        )
        self.alice = Mailbox.objects.create(
            tenant=self.tenant, domain=self.domain, local_part="alice",
            email="alice@acme.test", status=MailboxStatus.ACTIVE,
            mail_engine_provisioned=True,
        )

    def test_a_mailbox_may_send_as_itself(self):
        identity = sending.assert_may_send_as(self.alice, "alice@acme.test")
        self.assertTrue(identity.is_primary)

    def test_a_mailbox_may_not_send_as_a_colleague(self):
        with self.assertRaises(sending.SendFailed):
            sending.assert_may_send_as(self.alice, "bob@acme.test")

    def test_a_mailbox_may_not_send_as_an_arbitrary_domain(self):
        for address in ("ceo@victim.test", "alice@acme.test.evil.test", "postmaster@acme.test"):
            with self.subTest(address=address):
                with self.assertRaises(sending.SendFailed):
                    sending.assert_may_send_as(self.alice, address)

    def test_an_active_alias_becomes_a_permitted_identity(self):
        from apps.aliases.models import Alias

        Alias.objects.create(
            tenant=self.tenant, domain=self.domain,
            source_address="sales@acme.test", destination_mailbox=self.alice,
            status="active",
        )
        identity = sending.assert_may_send_as(self.alice, "sales@acme.test")
        self.assertEqual("alias", identity.kind)

    def test_a_disabled_alias_is_not_an_identity(self):
        from apps.aliases.models import Alias

        Alias.objects.create(
            tenant=self.tenant, domain=self.domain,
            source_address="old@acme.test", destination_mailbox=self.alice,
            status="disabled",
        )
        with self.assertRaises(sending.SendFailed):
            sending.assert_may_send_as(self.alice, "old@acme.test")

    def test_a_suspended_organization_cannot_send(self):
        self.tenant.status = "suspended"
        self.tenant.save(update_fields=["status"])
        with self.assertRaises(sending.SendFailed):
            sending.assert_organization_may_send(self.alice)

    def test_the_outbound_kill_switch_stops_sending(self):
        self.tenant.outbound_disabled = True
        self.tenant.save(update_fields=["outbound_disabled"])
        with self.assertRaises(sending.SendFailed):
            sending.assert_organization_may_send(self.alice)

    @override_settings(
        EMAIL_HOST="mx.matemail.online",
        EMAIL_PORT=587,
        EMAIL_USE_TLS=True,
        EMAIL_HOST_USER="noreply@mail.matemail.online",
        EMAIL_HOST_PASSWORD="platform-secret",
        POSTBOX_MASTER_USER="postbox",
        POSTBOX_MASTER_PASSWORD="postbox-master-secret",
        POSTBOX_MASTER_SEPARATOR="*",
    )
    def test_submission_authenticates_as_the_mailbox_not_platform_sender(self):
        message = mime.build_message(
            from_address=self.alice.email,
            to=["recipient@example.test"],
            subject="x",
            text="body",
        )
        smtp = mock.MagicMock()
        context = mock.MagicMock()
        context.__enter__.return_value = smtp

        with mock.patch("apps.postbox.sending.smtplib.SMTP", return_value=context):
            sending.submit(
                message,
                mailbox=self.alice,
                envelope_from=self.alice.email,
                recipients=["recipient@example.test"],
            )

        smtp.login.assert_called_once_with(
            "alice@acme.test*postbox", "postbox-master-secret"
        )
        self.assertNotEqual(
            smtp.login.call_args.args[0], "noreply@mail.matemail.online"
        )
        smtp.send_message.assert_called_once_with(
            message,
            from_addr="alice@acme.test",
            to_addrs=["recipient@example.test"],
        )

    @override_settings(
        EMAIL_HOST="mx.matemail.online",
        POSTBOX_MASTER_PASSWORD="",
    )
    def test_submission_fails_closed_without_postbox_master_credential(self):
        message = mime.build_message(
            from_address=self.alice.email,
            to=["recipient@example.test"],
            subject="x",
            text="body",
        )
        with mock.patch("apps.postbox.sending.smtplib.SMTP") as smtp:
            with self.assertRaises(sending.SendFailed):
                sending.submit(
                    message,
                    mailbox=self.alice,
                    envelope_from=self.alice.email,
                    recipients=["recipient@example.test"],
                )
        smtp.assert_not_called()


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS)
class ScheduledSendIdempotencyTest(TestCase):
    """
    A retry must never send twice.

    Celery retries on its own schedule, and a worker can die between "Postfix
    accepted it" and "the row was updated". The conditional claim is what makes
    that survivable.
    """

    def setUp(self):
        self.owner = make_user("owner@acme.test")
        self.tenant = make_tenant(self.owner, name="Acme", slug="acme")
        self.domain = Domain.objects.create(
            tenant=self.tenant, domain="acme.test", status="active",
            ownership_status="verified", mail_engine_provisioned=True,
        )
        self.alice = Mailbox.objects.create(
            tenant=self.tenant, domain=self.domain, local_part="alice",
            email="alice@acme.test", status=MailboxStatus.ACTIVE,
            mail_engine_provisioned=True,
        )
        self.row = ScheduledMessage.objects.create(
            mailbox=self.alice, uid_validity=1, uid=7,
            subject="Later", recipients="b@acme.test",
            scheduled_at=timezone.now() - timedelta(minutes=1),
        )

    def test_claim_succeeds_exactly_once(self):
        self.assertTrue(self.row.claim())
        second = ScheduledMessage.objects.get(pk=self.row.pk)
        self.assertFalse(second.claim())

    def test_claim_counts_the_attempt(self):
        self.row.claim()
        self.row.refresh_from_db()
        self.assertEqual(1, self.row.attempts)
        self.assertEqual(ScheduledMessage.State.SENDING, self.row.state)

    def test_a_cancelled_message_cannot_be_claimed(self):
        self.row.state = ScheduledMessage.State.CANCELLED
        self.row.save(update_fields=["state"])
        self.assertFalse(ScheduledMessage.objects.get(pk=self.row.pk).claim())

    def test_the_task_submits_once_even_when_run_twice(self):
        from apps.postbox import tasks

        raw = mime.build_message(
            from_address="alice@acme.test", to=["b@acme.test"],
            subject="Later", text="body",
        ).as_bytes()

        connection = mock.MagicMock()
        connection.select.return_value = mock.Mock(uid_validity=1)
        connection.fetch_raw.return_value = raw
        connection.list_folders.return_value = []

        with mock.patch("apps.postbox.imap.open_mailbox") as opener, \
             mock.patch("apps.postbox.sending.submit") as submit:
            opener.return_value.__enter__.return_value = connection

            first = tasks.send_scheduled_message(str(self.row.id))
            second = tasks.send_scheduled_message(str(self.row.id))

        self.assertEqual("sent", first)
        self.assertEqual("skipped", second)
        self.assertEqual(1, submit.call_count)

    def test_a_failure_is_recorded_rather_than_retried(self):
        from apps.postbox import tasks

        with mock.patch("apps.postbox.imap.open_mailbox", side_effect=RuntimeError("boom")):
            outcome = tasks.send_scheduled_message(str(self.row.id))

        self.row.refresh_from_db()
        self.assertEqual("failed", outcome)
        self.assertEqual(ScheduledMessage.State.FAILED, self.row.state)
        self.assertTrue(self.row.last_error)


class SieveCompilationTest(TestCase):
    """Generated Sieve, since the browser never supplies any."""

    def rule(self, **kwargs):
        from apps.postbox.models import MailRule

        defaults = dict(
            name="Rule", enabled=True, field="from", match="contains",
            value="newsletter@x.test", action="move", action_folder="News",
            stop_processing=False, position=0, pk=None,
        )
        defaults.update(kwargs)
        return mock.Mock(**defaults)

    def test_a_move_rule_compiles_to_fileinto(self):
        script = compile_rules([self.rule()], None, valid_folders={"News"})
        self.assertIn('fileinto "News";', script.body)
        self.assertIn('require ["fileinto"]', script.body)

    def test_a_rule_pointing_at_a_missing_folder_is_skipped(self):
        """`fileinto` into a missing mailbox is a runtime error that breaks
        every rule after it."""
        script = compile_rules([self.rule()], None, valid_folders={"Inbox"})
        self.assertNotIn("fileinto", script.body)

    def test_quotes_in_a_value_cannot_escape_the_string(self):
        """
        The attacker's text stays INSIDE the quoted string.

        The word "redirect" still appears — as characters in a value Sieve
        compares against, which is inert. What must not happen is that it
        becomes a statement, so the test checks the escaping directly and
        checks that no statement was produced.
        """
        rule = self.rule(value='evil"; redirect "attacker@evil.test"; #')
        script = compile_rules([rule], None, valid_folders={"News"})

        # Every quote from the value is escaped...
        self.assertIn('evil\\"', script.body)
        # ...so the only unescaped quotes are the ones this module wrote, and
        # `redirect` never begins a line.
        for line in script.body.splitlines():
            self.assertFalse(
                line.strip().startswith("redirect"),
                f"a redirect statement was generated: {line!r}",
            )

    def test_newlines_in_a_value_cannot_add_statements(self):
        """
        A newline would end the statement and let the rest be read as code.
        Control characters are stripped rather than escaped, so the value
        collapses to one line and cannot introduce a second statement.
        """
        rule = self.rule(value='x\nredirect "attacker@evil.test";')
        script = compile_rules([rule], None, valid_folders={"News"})

        condition = [l for l in script.body.splitlines() if l.startswith(("if header", "if address"))]
        self.assertEqual(1, len(condition), "the value broke the statement apart")
        self.assertNotIn("\n", condition[0])
        for line in script.body.splitlines():
            self.assertFalse(line.strip().startswith("redirect"))

    def test_there_is_no_forwarding_action_at_all(self):
        """
        The rule vocabulary has no redirect. Mail forwarding is an
        organization-administered setting precisely because it is how a
        compromised mailbox exfiltrates mail.
        """
        from apps.postbox.models import MailRule

        self.assertNotIn("redirect", {c[0] for c in MailRule.Action.choices})
        self.assertNotIn("forward", {c[0] for c in MailRule.Action.choices})

    def test_a_disabled_rule_is_not_compiled(self):
        script = compile_rules([self.rule(enabled=False)], None, valid_folders={"News"})
        self.assertNotIn("fileinto", script.body)

    def test_vacation_compiles_with_a_suppression_window(self):
        vacation = mock.Mock(
            is_active_now=True, subject="Away", message="Back Monday", repeat_days=7
        )
        script = compile_rules([], vacation, valid_folders=set())
        self.assertIn("vacation", script.body)
        self.assertIn(":days 7", script.body)
        self.assertIn('require ["vacation"]', script.body)

    def test_an_inactive_vacation_is_not_compiled(self):
        vacation = mock.Mock(is_active_now=False, message="Back Monday")
        script = compile_rules([], vacation, valid_folders=set())
        self.assertNotIn("vacation", script.body)


class ReplyRecipientTest(TestCase):
    def parsed(self, **kwargs):
        defaults = dict(
            from_address="sender@x.test", reply_to="",
            to=["me@acme.test", "other@y.test"], cc=["cc@z.test"],
        )
        defaults.update(kwargs)
        return mock.Mock(**defaults)

    def test_reply_goes_to_the_sender(self):
        to, cc = mime.reply_recipients(self.parsed(), {"me@acme.test"}, reply_all=False)
        self.assertEqual(["sender@x.test"], to)
        self.assertEqual([], cc)

    def test_reply_to_wins_over_from(self):
        """Ignoring it is how replies end up at a no-reply address."""
        to, _ = mime.reply_recipients(
            self.parsed(reply_to="list@x.test"), {"me@acme.test"}, reply_all=False
        )
        self.assertEqual(["list@x.test"], to)

    def test_reply_all_removes_my_own_identities(self):
        """Otherwise every reply adds the sender to their own Cc, forever."""
        to, cc = mime.reply_recipients(self.parsed(), {"me@acme.test"}, reply_all=True)
        self.assertNotIn("me@acme.test", to + cc)
        self.assertIn("other@y.test", cc)
        self.assertIn("cc@z.test", cc)

    def test_reply_all_deduplicates(self):
        parsed = self.parsed(to=["a@x.test", "A@X.test"], cc=["a@x.test"])
        _, cc = mime.reply_recipients(parsed, set(), reply_all=True)
        self.assertEqual(1, len([a for a in cc if a.lower() == "a@x.test"]))
