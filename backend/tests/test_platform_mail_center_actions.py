"""Phase 6C Operations sender and IMAP draft security regression tests."""
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.platform_admin.models import PlatformAuditLog
from apps.postbox import mime
from tests.factories import FAST_PASSWORD_HASHERS, auth_client, disable_throttling, make_tenant, make_user

SEND = "/api/platform/mail-center/send/"
DRAFTS = "/api/platform/mail-center/drafts/"
DELETE = "/api/platform/mail-center/drafts/7/?uid_validity=42"
KEY = "test-operations-2026-001"


class FakeOperationsIMAP:
    def __init__(self):
        self.events = []
        self.raw_messages = []
        self.folders = [
            SimpleNamespace(name="INBOX", role="inbox", selectable=True),
            SimpleNamespace(name="Sent", role="sent", selectable=True),
            SimpleNamespace(name="Drafts", role="drafts", selectable=True),
        ]
        self.original = mime.build_message(
            from_address="customer@example.test",
            to=["operations@system.matemail.pro"],
            subject="Question",
            text="Please answer.",
        ).as_bytes()

    def list_folders(self):
        self.events.append("list")
        return self.folders

    def select(self, folder, readonly=False):
        self.events.append(("select", folder, readonly))
        return SimpleNamespace(uid_validity=42)

    def fetch_summaries(self, uids):
        self.events.append(("fetch_summaries", tuple(uids)))
        if uids == [9]:
            return [SimpleNamespace(uid=9, size=len(self.original), draft=False)]
        if uids == [7]:
            return [SimpleNamespace(uid=7, size=128, draft=True)]
        return []

    def fetch_raw(self, uid):
        assert uid == 9
        self.events.append(("fetch_raw", uid))
        return self.original

    def append(self, folder, raw, flags=""):
        self.events.append(("append", folder, flags))
        self.raw_messages.append(raw)
        return 42, 100

    def delete_permanently(self, uids):
        self.events.append(("delete", tuple(uids)))


@override_settings(
    PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
    PLATFORM_MAIL_CENTER_ENABLED=True,
    PLATFORM_MAIL_CENTER_SEND_ENABLED=True,
    POSTBOX_MASTER_PASSWORD="ci-only",
    EMAIL_HOST="smtp.example.test",
)
class OperationsSendDraftTests(TestCase):
    def setUp(self):
        disable_throttling(self)
        self.admin = make_user("ops-action-admin@example.test", is_platform_admin=True)
        self.client = auth_client(self.admin)
        user = make_user("ops-action-customer@example.test")
        tenant = make_tenant(user, slug="ops-actions-tenant")
        self.customer = auth_client(user, tenant)
        self.conn = FakeOperationsIMAP()
        self.opened = []

        @contextmanager
        def opener(address):
            self.opened.append(address)
            yield self.conn

        for target, side in (
            ("apps.platform_admin.mail_center_actions.imap.open_mailbox", opener),
            ("apps.platform_admin.mail_center_actions.sending.submit", None),
        ):
            patcher = mock.patch(target, side_effect=side) if side else mock.patch(target)
            stub = patcher.start()
            self.addCleanup(patcher.stop)
            if "sending.submit" in target:
                self.submit = stub

        limits = mock.patch("apps.platform_admin.mail_center_actions.ratelimit.hit",
                            return_value=SimpleNamespace(allowed=True))
        self.limit = limits.start()
        self.addCleanup(limits.stop)
        claim = mock.patch("apps.platform_admin.mail_center_actions.ratelimit.claim_once", return_value=True)
        self.claim = claim.start()
        self.addCleanup(claim.stop)

    def payload(self, **changes):
        return dict({"to": ["recipient@example.test"], "subject": "Testing",
                     "text": "One safe message", "idempotency_key": KEY}, **changes)

    def test_never_accepts_unapproved_sender_or_tenant(self):
        for path, method in ((SEND, "post"), (DRAFTS, "post"), (DELETE, "delete")):
            for client in (self.customer, APIClient()):
                req = getattr(client, method)
                response = req(path, self.payload(), format="json") if method == "post" else req(path)
                self.assertIn(response.status_code, (401, 403))
        for forged in (
            {"from_address": "security@system.matemail.pro"},
            {"mailbox": "owner@customer.example"},
            {"html": "<script>alert(1)</script>"},
            {"attachments": [{"data": "AAAA"}]},
            {"to": ["foo@example.test"] * 11},
        ):
            self.assertEqual(
                self.client.post(SEND, {**self.payload(), **forged}, format="json").status_code,
                400,
            )
        self.assertEqual(self.opened, [])
        self.submit.assert_not_called()

    def test_default_off_and_separate_outbound_gate(self):
        with override_settings(PLATFORM_MAIL_CENTER_SEND_ENABLED=False):
            self.assertEqual(self.client.post(SEND, self.payload(), format="json").status_code, 503)
            self.assertEqual(self.client.post(DRAFTS, {}, format="json").status_code, 503)
            self.assertEqual(self.client.delete(DELETE).status_code, 503)
        with override_settings(PLATFORM_MAIL_CENTER_ENABLED=False):
            self.assertEqual(self.client.post(SEND, self.payload(), format="json").status_code, 503)
        self.submit.assert_not_called()
        self.assertEqual(self.opened, [])

    def test_plain_text_send_uses_only_operations_identity_and_sent_after_smtp(self):
        response = self.client.post(SEND, self.payload(), format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["sent"])
        self.assertTrue(response.data["filed_in_sent"])
        self.assertEqual(response.data["from_address"], "operations@system.matemail.pro")
        self.assertEqual(self.opened, ["operations@system.matemail.pro"])
        self.assertEqual(self.conn.events[-1], ("append", "Sent", "\\Seen"))
        message = self.submit.call_args.args[0]
        self.assertEqual(message["From"], "operations@system.matemail.pro")
        self.assertNotIn("Bcc", message)
        self.assertEqual(self.submit.call_args.kwargs["envelope_from"], "operations@system.matemail.pro")
        self.assertEqual(self.submit.call_args.kwargs["mailbox"].email, "operations@system.matemail.pro")
        self.assertEqual(self.submit.call_args.kwargs["recipients"], ["recipient@example.test"])
        self.assertEqual(PlatformAuditLog.objects.filter(action="mail_center.send_attempt").count(), 1)
        self.assertEqual(PlatformAuditLog.objects.filter(action="mail_center.sent").count(), 1)
        self.assertNotIn("recipient@example.test", str(PlatformAuditLog.objects.values_list("metadata", flat=True)))

    def test_bcc_is_envelope_only(self):
        body = self.payload()
        body["bcc"] = ["private@example.test"]
        response = self.client.post(SEND, body, format="json")
        self.assertEqual(response.status_code, 200)
        msg = self.submit.call_args.args[0]
        self.assertNotIn("Bcc", msg)
        self.assertIn("private@example.test", self.submit.call_args.kwargs["recipients"])

    def test_send_deduplication_and_limit_fail_closed(self):
        self.claim.side_effect = [True, False]
        self.assertEqual(self.client.post(SEND, self.payload(), format="json").status_code, 200)
        self.assertEqual(self.client.post(SEND, self.payload(), format="json").status_code, 409)
        self.assertEqual(self.submit.call_count, 1)
        self.limit.return_value = SimpleNamespace(allowed=False)
        self.assertEqual(self.client.post(SEND, self.payload(idempotency_key="another-send-identifier-2026"), format="json").status_code, 429)
        self.assertEqual(self.submit.call_count, 1)

    def test_audit_failure_prevents_irreversible_submission(self):
        with mock.patch("apps.platform_admin.mail_center_actions.record_platform_action",
                        side_effect=RuntimeError("audit offline")):
            with self.assertRaises(RuntimeError):
                self.client.post(SEND, self.payload(), format="json")
        self.submit.assert_not_called()

    def test_sent_failure_not_reported_as_send_failure(self):
        self.conn.append = mock.Mock(side_effect=RuntimeError("no Sent folder storage"))
        response = self.client.post(SEND, self.payload(), format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["sent"])
        self.assertFalse(response.data["filed_in_sent"])
        self.submit.assert_called_once()

    def test_reply_recipients_and_thread_are_from_actual_message(self):
        body = self.payload(to=[], subject="", reply={
            "uid": 9, "uid_validity": 42, "folder": "INBOX", "mode": "reply",
        })
        result = self.client.post(SEND, body, format="json")
        self.assertEqual(result.status_code, 200, result.data)
        self.assertIn(("select", "INBOX", True), self.conn.events)
        self.assertEqual(self.submit.call_args.kwargs["recipients"], ["customer@example.test"])
        message = self.submit.call_args.args[0]
        self.assertEqual(message["Subject"], "Re: Question")
        self.assertEqual(message["In-Reply-To"], BytesParser(policy=policy.default).parsebytes(self.conn.original)["Message-ID"])

    def test_stale_reply_or_arbitrary_mailbox_does_not_send(self):
        for reply in (
            {"uid": 9, "uid_validity": 41, "folder": "INBOX", "mode": "reply"},
            {"uid": 9, "uid_validity": 42, "folder": "Hidden", "mode": "reply"},
        ):
            result = self.client.post(SEND, self.payload(to=[], reply=reply), format="json")
            self.assertIn(result.status_code, (400, 404, 409))
        self.submit.assert_not_called()

    def test_save_replace_draft_writes_new_before_removing_old(self):
        data = {"to": ["x@example.test"], "bcc": ["private@example.test"],
                "subject": "Draft", "text": "Notes"}
        response = self.client.post(DRAFTS, data, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["uid_validity"], 42)
        self.assertFalse(response.data["previous_removed"])
        self.assertIn(("append", "Drafts", "\\Draft \\Seen"), self.conn.events)
        saved = BytesParser(policy=policy.default).parsebytes(self.conn.raw_messages[0])
        self.assertIn("private@example.test", str(saved["Bcc"]))
        self.assertEqual(str(saved["From"]), "operations@system.matemail.pro")
        self.conn.events.clear()
        replaced = self.client.post(
            DRAFTS, {**data, "draft": {"uid": 7, "uid_validity": 42}},
            format="json",
        )
        self.assertEqual(replaced.status_code, 200, replaced.data)
        self.assertTrue(replaced.data["previous_removed"])
        self.assertLess(
            self.conn.events.index(("append", "Drafts", "\\Draft \\Seen")),
            self.conn.events.index(("delete", (7,))),
        )

    def test_stale_draft_ref_for_save_and_delete_never_mutates(self):
        stale = {"uid": 7, "uid_validity": 41}
        self.assertEqual(
            self.client.post(DRAFTS, {"draft": stale, "text": "x"}, format="json").status_code,
            409,
        )
        self.assertEqual(self.client.delete("/api/platform/mail-center/drafts/7/?uid_validity=41").status_code, 409)
        self.assertFalse(any(e[0] in ("append", "delete") for e in self.conn.events if isinstance(e, tuple)))
        self.assertEqual(self.client.delete("/api/platform/mail-center/drafts/7/").status_code, 400)

    def test_delete_only_verified_draft(self):
        response = self.client.delete(DELETE)
        self.assertEqual(response.status_code, 204)
        self.assertIn(("select", "Drafts", False), self.conn.events)
        self.assertIn(("delete", (7,)), self.conn.events)
        self.assertEqual(PlatformAuditLog.objects.filter(action="mail_center.draft_deleted").count(), 1)

    def test_send_saved_draft_deletes_only_after_smtp_acceptance(self):
        body = self.payload(draft={"uid": 7, "uid_validity": 42})
        response = self.client.post(SEND, body, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIn(("delete", (7,)), self.conn.events)
        self.assertEqual(self.submit.call_count, 1)

    def test_non_mutating_methods_refused(self):
        self.assertEqual(self.client.get(SEND).status_code, 405)
        self.assertEqual(self.client.get(DRAFTS).status_code, 405)
        self.assertEqual(self.client.post("/api/platform/mail-center/drafts/7/", {}, format="json").status_code, 405)
        self.assertEqual(self.opened, [])
