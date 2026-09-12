"""
Transactional email.

Two behaviours are asserted here, and the second matters more than the first.

Delivery works. And when it does *not* work, nobody is told that it did. Every
send used `fail_silently=True`, so a broken SMTP configuration produced
"Verification email sent." with nothing sent and no trace anywhere — the
customer waits at a verification wall they cannot pass, and the logs are clean.
"""
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings

from apps.accounts.mailer import (
    make_message_id,
    message_id_domain,
    send_transactional,
    transactional_email_configured,
    transactional_from_address,
)
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    TEST_PASSWORD,
    auth_client,
    disable_throttling,
    make_tenant,
    make_user,
)

LOCMEM_EMAIL = "django.core.mail.backends.locmem.EmailBackend"
SMTP_EMAIL = "django.core.mail.backends.smtp.EmailBackend"
LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "email-tests",
    }
}


@override_settings(EMAIL_BACKEND=LOCMEM_EMAIL)
class ConfigurationTest(TestCase):
    @override_settings(EMAIL_BACKEND=SMTP_EMAIL, EMAIL_HOST="")
    def test_unset_host_is_not_configured(self):
        self.assertFalse(transactional_email_configured())

    @override_settings(EMAIL_BACKEND=SMTP_EMAIL, EMAIL_HOST="localhost")
    def test_localhost_is_not_configured(self):
        """
        The old default. Nothing listens there, so every message was silently
        discarded — and if anything ever did listen it would be the Mail
        Engine, which is exactly where application mail must not go.
        """
        self.assertFalse(transactional_email_configured())

    @override_settings(EMAIL_BACKEND=SMTP_EMAIL, EMAIL_HOST="127.0.0.1")
    def test_loopback_is_not_configured(self):
        self.assertFalse(transactional_email_configured())

    @override_settings(EMAIL_BACKEND=SMTP_EMAIL, EMAIL_HOST="smtp.provider.example")
    def test_an_external_host_is_configured(self):
        self.assertTrue(transactional_email_configured())

    def test_the_console_backend_counts_as_configured(self):
        self.assertTrue(transactional_email_configured())

    def test_production_settings_do_not_default_to_localhost(self):
        """
        Asserted against the source, not the resolved value: whatever
        EMAIL_HOST happens to be set to in the environment running the tests
        says nothing about what a deployment that forgets the variable gets.
        The old default was "localhost", where nothing listens.
        """
        import pathlib

        import config.settings.prod as prod

        source = pathlib.Path(prod.__file__).read_text(encoding="utf-8")
        self.assertIn('EMAIL_HOST = env("EMAIL_HOST", default="")', source)
        self.assertNotIn('default="localhost"', source)

    def test_the_sender_names_matemail_and_not_the_engine(self):
        sender = transactional_from_address().lower()
        self.assertIn("matemail", sender)
        for leak in ("mailcow", "postfix", "dovecot", "rspamd"):
            self.assertNotIn(leak, sender)


@override_settings(EMAIL_BACKEND=LOCMEM_EMAIL)
class SendTest(TestCase):
    def setUp(self):
        mail.outbox = []

    def test_a_message_is_delivered(self):
        ok = send_transactional(
            subject="Hello", body="Body", to="someone@example.test", purpose="test"
        )
        self.assertTrue(ok)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Hello")

    @override_settings(EMAIL_BACKEND=SMTP_EMAIL, EMAIL_HOST="localhost")
    def test_an_unconfigured_deployment_reports_failure(self):
        with self.assertLogs("apps.accounts.mailer", level="ERROR") as logs:
            ok = send_transactional(
                subject="Hello", body="Body", to="someone@example.test", purpose="test"
            )
        self.assertFalse(ok)
        self.assertIn("EMAIL_HOST", "".join(logs.output))

    def test_a_provider_failure_reports_failure_and_is_logged(self):
        with mock.patch(
            "django.core.mail.EmailMultiAlternatives.send",
            side_effect=OSError("connection refused"),
        ):
            with self.assertLogs("apps.accounts.mailer", level="ERROR") as logs:
                ok = send_transactional(
                    subject="Hello", body="Body", to="a@example.test", purpose="test"
                )
        self.assertFalse(ok)
        self.assertIn("connection refused", "".join(logs.output))

    def test_a_provider_failure_does_not_raise(self):
        """A customer-facing view must not 500 because a provider is down."""
        with mock.patch(
            "django.core.mail.EmailMultiAlternatives.send",
            side_effect=OSError("boom"),
        ):
            self.assertFalse(
                send_transactional(
                    subject="s", body="b", to="a@example.test", purpose="test"
                )
            )

    def test_a_rejected_message_reports_failure(self):
        with mock.patch("django.core.mail.EmailMultiAlternatives.send", return_value=0):
            self.assertFalse(
                send_transactional(
                    subject="s", body="b", to="a@example.test", purpose="test"
                )
            )

    def test_no_recipients_is_not_a_send(self):
        self.assertFalse(send_transactional(subject="s", body="b", to=[], purpose="t"))


@override_settings(
    EMAIL_BACKEND=LOCMEM_EMAIL,
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES=LOCMEM_CACHE,
)
class EndpointHonestyTest(TestCase):
    def setUp(self):
        disable_throttling(self)
        mail.outbox = []
        self.user = make_user("mailer@example.test", verified=False)
        self.tenant = make_tenant(self.user, name="Mailer", slug="mailer")
        self.api = auth_client(self.user, self.tenant)

    def test_resend_verification_sends(self):
        res = self.api.post("/api/auth/resend-verification/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(mail.outbox), 1)

    def test_resend_verification_admits_failure(self):
        """
        The endpoint used to answer "Verification email sent." unconditionally.
        There is no existence oracle here — the caller is authenticated and
        already knows their own address — so the honest answer is available.
        """
        with mock.patch(
            "apps.accounts.views.send_transactional", return_value=False
        ):
            res = self.api.post("/api/auth/resend-verification/")
        self.assertEqual(res.status_code, 503)
        self.assertIn("could not send", res.data["detail"].lower())

    def test_password_reset_still_answers_identically_when_sending_fails(self):
        """
        Here the uniform response is the point: a different answer for a real
        address would tell an attacker which addresses are registered. The
        failure goes to the log, not to the caller.
        """
        with mock.patch(
            "apps.accounts.views.send_transactional", return_value=False
        ):
            real = self.client.post(
                "/api/auth/forgot-password/", {"email": self.user.email}
            )
            ghost = self.client.post(
                "/api/auth/forgot-password/", {"email": "nobody@example.test"}
            )
        self.assertEqual(real.status_code, ghost.status_code)
        self.assertEqual(real.data["detail"], ghost.data["detail"])

    def test_password_reset_email_is_sent_for_a_real_address(self):
        self.client.post("/api/auth/forgot-password/", {"email": self.user.email})
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("reset", mail.outbox[0].subject.lower())

    def test_no_email_is_sent_for_an_unknown_address(self):
        self.client.post("/api/auth/forgot-password/", {"email": "ghost@example.test"})
        self.assertEqual(len(mail.outbox), 0)

    def test_an_invite_reports_whether_the_email_went_out(self):
        verified = make_user("inviter@example.test")
        tenant = make_tenant(verified, name="Inv", slug="inv")
        api = auth_client(verified, tenant)

        with mock.patch("apps.teams.views.send_transactional", return_value=False):
            res = api.post(
                "/api/teams/invites/", {"email": "guest@example.test", "role": "admin"}
            )
        self.assertEqual(res.status_code, 201, "the invite itself must still be created")
        self.assertFalse(res.data["email_delivered"])
        self.assertIn("could not be sent", res.data["detail"])

    def test_an_invite_reports_success_normally(self):
        verified = make_user("inviter2@example.test")
        tenant = make_tenant(verified, name="Inv2", slug="inv2")
        res = auth_client(verified, tenant).post(
            "/api/teams/invites/", {"email": "guest2@example.test", "role": "admin"}
        )
        self.assertEqual(res.status_code, 201)
        self.assertTrue(res.data["email_delivered"])


@override_settings(
    EMAIL_BACKEND=LOCMEM_EMAIL,
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    CACHES=LOCMEM_CACHE,
)
class NoEngineLeakTest(TestCase):
    """Customers must never learn what runs underneath from an email."""

    def setUp(self):
        disable_throttling(self)
        mail.outbox = []

    def test_messages_name_the_product_not_the_engine(self):
        user = make_user("leak@example.test", verified=False)
        make_tenant(user, name="Leak", slug="leak")
        auth_client(user).post("/api/auth/resend-verification/")

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        blob = f"{message.subject} {message.body} {message.from_email}".lower()
        self.assertIn("matemail", blob)
        for leak in ("mailcow", "postfix", "dovecot", "rspamd", "sogo"):
            self.assertNotIn(leak, blob)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="MateMail <noreply@mail.matemail.online>",
)
class MessageIdHygieneTest(TestCase):
    """
    The Message-ID header (P5, Stage 15).

    Django builds one from the LOCAL hostname when none is supplied. In a
    container that is the container id, so real delivered mail carried
    `<...@00a3f41e29d7>`: a public header advertising an internal identifier,
    different after every deploy, and not a resolvable domain — which some
    receivers treat as a spam signal and which makes threading unreliable.
    """

    def setUp(self):
        mail.outbox = []

    def test_the_message_id_is_rooted_at_our_sending_domain(self):
        send_transactional(subject="Subject", body="Body", to=["someone@example.test"])

        message_id = mail.outbox[0].extra_headers["Message-ID"]
        self.assertTrue(message_id.startswith("<"))
        self.assertTrue(message_id.endswith(">"))
        self.assertTrue(message_id.endswith("@mail.matemail.online>"), message_id)

    def test_it_never_carries_the_local_hostname(self):
        import socket

        send_transactional(subject="Subject", body="Body", to=["someone@example.test"])
        message_id = mail.outbox[0].extra_headers["Message-ID"]
        self.assertNotIn(socket.gethostname(), message_id)

    def test_every_message_gets_a_distinct_id(self):
        """
        A repeated Message-ID makes receivers silently drop the second message
        as a duplicate — so two password resets would become one.
        """
        for _ in range(25):
            send_transactional(subject="Subject", body="Body", to=["someone@example.test"])

        ids = [m.extra_headers["Message-ID"] for m in mail.outbox]
        self.assertEqual(len(set(ids)), len(ids))

    def test_the_domain_is_taken_from_the_from_address(self):
        self.assertEqual(
            message_id_domain("MateMail <noreply@mail.matemail.online>"),
            "mail.matemail.online",
        )
        self.assertEqual(message_id_domain("plain@example.test"), "example.test")

    def test_a_from_address_without_a_domain_falls_back_to_the_mail_hostname(self):
        with override_settings(MAIL_HOSTNAME="mx.matemail.online"):
            self.assertEqual(message_id_domain("not-an-address"), "mx.matemail.online")
