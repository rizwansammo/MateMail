"""
Native PostBox push, server half: registrations, the engine's reports, the
dispatch and the two providers.

The properties under test are the ones that must never regress: a
registration is one mailbox's and one session's; the provider token is never
read back; the engine's report is stored once and carries no content; nothing
on the ingest path talks to a provider; only active devices are pushed; and a
provider's "this device is gone" disables it while MateMail's own
misconfiguration never does.

No test touches FCM, WNS, Entra ID or Google: every HTTP request goes to a
fake session. All data is synthetic.
"""
import inspect
import json
import uuid
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from kombu.exceptions import OperationalError
from rest_framework.test import APIClient

from apps.domains.models import Domain
from apps.mailboxes.models import Mailbox, MailboxStatus
from apps.postbox import auth as postbox_auth
from apps.postbox import push, tasks, views_push
from apps.postbox.models import (
    PostBoxPushDevice,
    PostBoxPushEvent,
    PostBoxSession,
    PushPlatform,
    PushProvider,
    PushTokenType,
)
from apps.postbox.push_providers import FcmPushProvider, PushOutcome, WnsPushProvider
from tests.factories import FAST_PASSWORD_HASHERS, disable_throttling, make_tenant, make_user

LOGIN = "/api/postbox/auth/login/"
LOGOUT = "/api/postbox/auth/logout/"
DEVICES = "/api/postbox/devices/"
INGEST = "/api/internal/postbox/push-events/"

INGEST_SECRET = "test-only-push-ingest-secret"
FCM_TOKEN = "fcm-test-token:" + "A1b2_C3d4-" * 14
WNS_CHANNEL = "https://wns2-db5p.notify.windows.com/?token=AwYAAAB" + "Zx9" * 20

LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "postbox-push-tests",
    }
}


def make_mailbox(tenant, domain, local_part, *, status=MailboxStatus.ACTIVE):
    return Mailbox.objects.create(
        tenant=tenant,
        domain=domain,
        local_part=local_part,
        email=f"{local_part}@{domain.domain}",
        full_name=local_part.title(),
        status=status,
        mail_engine_provisioned=True,
    )


class FakeResponse:
    def __init__(self, status, body=None, headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeHttp:
    """Stands in for requests.Session. Records every POST; answers in order."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class PushTestCase(TestCase):
    """Two mailboxes in one organization, and a third in another."""

    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        disable_throttling(self)
        owner = make_user("owner@acme.test")
        self.tenant = make_tenant(owner, name="Acme", slug="acme")
        self.domain = Domain.objects.create(
            tenant=self.tenant, domain="acme.test", status="active",
            ownership_status="verified", mail_engine_provisioned=True,
        )
        self.alice = make_mailbox(self.tenant, self.domain, "alice")
        self.bob = make_mailbox(self.tenant, self.domain, "bob")

    def login_as(self, mailbox):
        """Sign in with Dovecot stubbed - this suite is about push, not IMAP."""
        with mock.patch("apps.postbox.auth.imap.authenticate", return_value=True), \
             mock.patch("apps.postbox.imap.open_mailbox"):
            client = APIClient()
            response = client.post(
                LOGIN, {"email": mailbox.email, "password": "synthetic"}, format="json"
            )
            assert response.status_code == 200, response.data
        return client, PostBoxSession.objects.filter(mailbox=mailbox).order_by("-created_at").first()

    def register(self, client, *, installation=None, platform="android", provider="fcm",
                 token=FCM_TOKEN, **extra):
        body = {
            "installation_id": str(installation or uuid.uuid4()),
            "platform": platform,
            "provider": provider,
            "token": token,
            **extra,
        }
        return client.post(DEVICES, body, format="json")

    def device(self, mailbox, session, *, provider=PushProvider.FCM, token=FCM_TOKEN,
               enabled=True):
        return PostBoxPushDevice.objects.create(
            mailbox=mailbox,
            session=session,
            installation_id=uuid.uuid4(),
            platform=PushPlatform.ANDROID if provider == PushProvider.FCM else PushPlatform.WINDOWS,
            provider=provider,
            token_type=(PushTokenType.REGISTRATION_TOKEN if provider == PushProvider.FCM
                        else PushTokenType.CHANNEL_URI),
            token=token,
            enabled=enabled,
        )

    def event(self, mailbox, *, uid=7):
        return PostBoxPushEvent.objects.create(
            event_id=uuid.uuid4(), mailbox=mailbox, folder="INBOX",
            uid_validity=1790364354, uid=uid,
        )


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class PushDeviceApiTest(PushTestCase):

    def test_an_android_installation_registers_with_fcm_and_never_sees_its_token_again(self):
        client, session = self.login_as(self.alice)
        response = self.register(client)
        self.assertEqual(201, response.status_code, response.data)
        self.assertEqual(
            {"id", "platform", "provider", "token_type", "enabled", "created_at",
             "updated_at", "last_seen_at"},
            set(response.data),
        )
        self.assertNotIn(FCM_TOKEN.encode(), response.content)
        self.assertNotIn(FCM_TOKEN.encode(), client.get(DEVICES).content)
        row = PostBoxPushDevice.objects.get(pk=response.data["id"])
        self.assertEqual((self.alice.pk, session.pk), (row.mailbox_id, row.session_id))
        self.assertEqual(PushTokenType.REGISTRATION_TOKEN, row.token_type)
        self.assertEqual(FCM_TOKEN, row.token)

    def test_a_windows_installation_registers_a_wns_channel(self):
        client, _ = self.login_as(self.alice)
        response = self.register(client, platform="windows", provider="wns", token=WNS_CHANNEL)
        self.assertEqual(201, response.status_code, response.data)
        self.assertEqual("channel_uri", response.data["token_type"])
        self.assertNotIn(b"notify.windows.com", response.content)

    def test_registering_again_refreshes_one_row_and_follows_the_current_session(self):
        first_client, first_session = self.login_as(self.alice)
        installation = uuid.uuid4()
        created = self.register(first_client, installation=installation)
        second_client, second_session = self.login_as(self.alice)
        refreshed = self.register(
            second_client, installation=installation, token=FCM_TOKEN + "refreshed"
        )
        self.assertEqual((201, 200), (created.status_code, refreshed.status_code))
        self.assertEqual(created.data["id"], refreshed.data["id"])
        row = PostBoxPushDevice.objects.get()
        self.assertEqual(FCM_TOKEN + "refreshed", row.token)
        self.assertEqual(second_session.pk, row.session_id)
        self.assertNotEqual(first_session.pk, row.session_id)

    def test_another_mailbox_cannot_list_or_delete_a_registration(self):
        alice_client, _ = self.login_as(self.alice)
        registered = self.register(alice_client).data["id"]
        bob_client, _ = self.login_as(self.bob)
        self.assertEqual([], bob_client.get(DEVICES).data["results"])
        self.assertEqual(404, bob_client.delete(f"{DEVICES}{registered}/").status_code)
        self.assertTrue(PostBoxPushDevice.objects.filter(pk=registered).exists())
        # And no session at all is no access at all: 403, as the client
        # contract in docs/POSTBOX_REMOTE_PUSH.md says (DRF sends no challenge).
        self.assertEqual(403, APIClient().get(DEVICES).status_code)
        self.assertEqual(403, self.register(APIClient()).status_code)

    def test_a_mailbox_deletes_its_own_registration(self):
        client, _ = self.login_as(self.alice)
        registered = self.register(client).data["id"]
        self.assertEqual(204, client.delete(f"{DEVICES}{registered}/").status_code)
        self.assertFalse(PostBoxPushDevice.objects.exists())
        self.assertEqual(404, client.delete(f"{DEVICES}{registered}/").status_code)

    def test_malformed_registrations_are_refused(self):
        client, _ = self.login_as(self.alice)
        cases = {
            "unknown platform": {"platform": "ios", "provider": "fcm"},
            "unknown provider": {"provider": "apns"},
            "android on wns": {"provider": "wns", "token": WNS_CHANNEL},
            "windows on fcm": {"platform": "windows", "provider": "fcm"},
            "plain-http channel": {"platform": "windows", "provider": "wns",
                                   "token": WNS_CHANNEL.replace("https", "http")},
            "foreign channel host": {"platform": "windows", "provider": "wns",
                                     "token": "https://notify.windows.com.evil.example/?token=x"},
            "channel with credentials": {"platform": "windows", "provider": "wns",
                                         "token": "https://u:p@db5p.notify.windows.com/?token=x"},
            "fid for wns": {"platform": "windows", "provider": "wns", "token": WNS_CHANNEL,
                            "token_type": "fid"},
            "token with whitespace": {"token": "bad token\n"},
            "empty token": {"token": ""},
            "oversized token": {"token": "A" * 4097},
        }
        for name, overrides in cases.items():
            with self.subTest(case=name):
                self.assertEqual(400, self.register(client, **overrides).status_code)
        response = client.post(DEVICES, {"installation_id": "not-a-uuid", "platform": "android",
                                         "provider": "fcm", "token": FCM_TOKEN}, format="json")
        self.assertEqual(400, response.status_code)
        self.assertFalse(PostBoxPushDevice.objects.exists())

    def test_revoked_and_expired_sessions_are_never_selected_for_push(self):
        active = PostBoxSession.issue(self.alice)[1]
        revoked = PostBoxSession.issue(self.alice)[1]
        revoked.revoke()
        expired = PostBoxSession.issue(self.alice)[1]
        PostBoxSession.objects.filter(pk=expired.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        keep = self.device(self.alice, active)
        self.device(self.alice, revoked, token=FCM_TOKEN + "r")
        self.device(self.alice, expired, token=FCM_TOKEN + "e")
        self.assertEqual([keep.pk], [d.pk for d in push.active_devices(self.alice)])

        postbox_auth.revoke_other_sessions(self.alice)   # "sign out everywhere"
        self.assertFalse(push.active_devices(self.alice).exists())

    def test_signing_out_or_revoking_deletes_the_registration_and_its_token(self):
        here, here_session = self.login_as(self.alice)
        self.register(here)
        there, _ = self.login_as(self.alice)
        self.register(there, token=FCM_TOKEN + "there")
        bob, _ = self.login_as(self.bob)
        self.register(bob, token=FCM_TOKEN + "bob")

        # A password change keeps this session and revokes the rest; only the
        # other session's registration goes.
        postbox_auth.revoke_other_sessions(self.alice, keep=here_session)
        self.assertEqual(
            [FCM_TOKEN], list(PostBoxPushDevice.objects.for_mailbox(self.alice)
                              .values_list("token", flat=True))
        )
        # Signing out takes this session's with it. Bob's is untouched.
        self.assertEqual(200, here.post(LOGOUT).status_code)
        self.assertFalse(PostBoxPushDevice.objects.for_mailbox(self.alice).exists())
        self.assertEqual([FCM_TOKEN + "bob"],
                         list(PostBoxPushDevice.objects.values_list("token", flat=True)))

    def test_one_installation_can_serve_several_mailboxes(self):
        installation = uuid.uuid4()
        alice_client, _ = self.login_as(self.alice)
        bob_client, _ = self.login_as(self.bob)
        a = self.register(alice_client, installation=installation).data["id"]
        b = self.register(bob_client, installation=installation).data["id"]
        self.assertNotEqual(a, b)
        self.assertEqual([a], [d["id"] for d in alice_client.get(DEVICES).data["results"]])
        self.assertEqual(204, alice_client.delete(f"{DEVICES}{a}/").status_code)
        self.assertTrue(PostBoxPushDevice.objects.filter(pk=b, mailbox=self.bob).exists())

    def test_a_mailbox_cannot_register_unlimited_devices(self):
        client, session = self.login_as(self.alice)
        for n in range(push.MAX_DEVICES_PER_MAILBOX):
            self.device(self.alice, session, token=f"{FCM_TOKEN}{n}")
        self.assertEqual(409, self.register(client).status_code)
        self.assertEqual(push.MAX_DEVICES_PER_MAILBOX, PostBoxPushDevice.objects.count())


@override_settings(POSTBOX_PUSH_INGEST_SECRET=INGEST_SECRET, PASSWORD_HASHERS=FAST_PASSWORD_HASHERS,
                   CACHES=LOCMEM_CACHE)
class PushIngestTest(PushTestCase):

    def report(self, secret=INGEST_SECRET, **overrides):
        body = {
            "event_id": str(uuid.uuid4()), "event": "new_mail", "mailbox": self.alice.email,
            "folder": "INBOX", "uid_validity": 1790364354, "uid": 7, **overrides,
        }
        headers = {"HTTP_X_POSTBOX_PUSH_SECRET": secret} if secret is not None else {}
        return self.client.post(INGEST, json.dumps(body), content_type="application/json",
                                **headers), body

    def test_the_ingest_is_internal_and_refuses_a_missing_wrong_or_unset_secret(self):
        self.assertTrue(reverse("postbox-push-ingest").startswith("/api/internal/"))
        self.assertIn("compare_digest", inspect.getsource(views_push._ingest_authorized))
        for secret in (None, "", "not-the-secret", "dev-only-internal-secret"):
            with self.subTest(secret=secret):
                self.assertEqual(403, self.report(secret=secret)[0].status_code)
        with override_settings(POSTBOX_PUSH_INGEST_SECRET=""):
            self.assertEqual(403, self.report(secret="")[0].status_code)
        self.assertFalse(PostBoxPushEvent.objects.exists())

    def test_a_delivery_is_stored_once_and_dispatched_after_commit(self):
        with mock.patch.object(tasks.dispatch_push_event, "delay") as delay, \
             self.captureOnCommitCallbacks(execute=True):
            response, body = self.report()
        self.assertEqual(202, response.status_code, response.data)
        self.assertEqual({"accepted": True, "event_id": body["event_id"], "duplicate": False},
                         response.data)
        event = PostBoxPushEvent.objects.get()
        self.assertEqual(
            (self.alice.pk, "INBOX", 1790364354, 7, PostBoxPushEvent.State.PENDING),
            (event.mailbox_id, event.folder, event.uid_validity, event.uid, event.state),
        )
        delay.assert_called_once_with(body["event_id"])

    def test_mailbox_changes_wake_web_clients_without_becoming_mobile_push_rows(self):
        with mock.patch("apps.postbox.views_push.realtime.publish") as publish, \
             mock.patch.object(tasks.dispatch_push_event, "delay") as delay:
            response, body = self.report(event="mailbox_changed")
        self.assertEqual(202, response.status_code, response.data)
        self.assertEqual(
            {"accepted": True, "event_id": body["event_id"], "duplicate": False},
            response.data,
        )
        self.assertFalse(PostBoxPushEvent.objects.exists())
        delay.assert_not_called()
        publish.assert_called_once_with(
            self.alice,
            event_id=body["event_id"],
            kind="mailbox_changed",
            folder="INBOX",
            uid_validity=1790364354,
            uid=7,
        )

    def test_the_same_event_reported_twice_is_one_event(self):
        event_id = str(uuid.uuid4())
        with mock.patch.object(tasks.dispatch_push_event, "delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                first, _ = self.report(event_id=event_id)
            with self.captureOnCommitCallbacks(execute=True):
                second, _ = self.report(event_id=event_id)
        self.assertEqual((202, 202), (first.status_code, second.status_code))
        self.assertTrue(second.data["duplicate"])
        self.assertEqual(1, PostBoxPushEvent.objects.count())
        self.assertEqual(1, delay.call_count)

    def test_an_unknown_or_suspended_mailbox_is_refused(self):
        self.assertEqual(404, self.report(mailbox="nobody@acme.test")[0].status_code)
        Mailbox.objects.filter(pk=self.bob.pk).update(status=MailboxStatus.SUSPENDED)
        self.assertEqual(404, self.report(mailbox=self.bob.email)[0].status_code)
        self.assertFalse(PostBoxPushEvent.objects.exists())

    def test_a_report_carrying_message_content_is_refused(self):
        for field in ("subject", "from", "sender", "snippet", "body", "html"):
            with self.subTest(field=field):
                self.assertEqual(400, self.report(**{field: "Synthetic content"})[0].status_code)
        self.assertEqual(400, self.report(uid=None)[0].status_code)       # half an identity
        self.assertEqual(400, self.report(event="message_read")[0].status_code)
        self.assertEqual(413, self.report(folder="x" * 5000)[0].status_code)
        self.assertFalse(PostBoxPushEvent.objects.exists())

    def test_ingest_never_talks_to_a_provider(self):
        with mock.patch("apps.postbox.push_providers.requests.Session.post") as network, \
             mock.patch("apps.postbox.push.provider_for") as provider_for, \
             mock.patch.object(tasks.dispatch_push_event, "delay"), \
             self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(202, self.report()[0].status_code)
        network.assert_not_called()
        provider_for.assert_not_called()

    def test_a_broker_outage_leaves_the_event_for_the_sweep(self):
        with mock.patch.object(tasks.dispatch_push_event, "delay",
                               side_effect=OperationalError("broker unreachable")), \
             self.captureOnCommitCallbacks(execute=True):
            response, body = self.report()
        self.assertEqual(202, response.status_code)
        self.assertEqual(PostBoxPushEvent.State.PENDING, PostBoxPushEvent.objects.get().state)

        stale = self.event(self.alice, uid=8)
        PostBoxPushEvent.objects.filter(pk=body["event_id"]).update(
            created_at=timezone.now() - timedelta(seconds=90))
        PostBoxPushEvent.objects.filter(pk=stale.pk).update(
            created_at=timezone.now() - push.EVENT_MAX_AGE - timedelta(minutes=1))
        with mock.patch.object(tasks.dispatch_push_event, "delay") as delay:
            self.assertEqual({"requeued": 1, "expired": 1}, push.sweep())
        delay.assert_called_once_with(body["event_id"])
        stale.refresh_from_db()
        self.assertEqual(PostBoxPushEvent.State.EXPIRED, stale.state)


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class PushDispatchTest(PushTestCase):

    def test_dispatch_fans_out_once_and_only_to_active_devices(self):
        live = PostBoxSession.issue(self.alice)[1]
        gone = PostBoxSession.issue(self.alice)[1]
        gone.revoke()
        fcm = self.device(self.alice, live)
        wns = self.device(self.alice, live, provider=PushProvider.WNS, token=WNS_CHANNEL)
        self.device(self.alice, gone, token=FCM_TOKEN + "revoked")
        self.device(self.alice, live, token=FCM_TOKEN + "disabled", enabled=False)
        event = self.event(self.alice)

        with mock.patch.object(tasks.send_push, "delay") as send:
            self.assertEqual("dispatched", push.dispatch(event.event_id))
            self.assertEqual("claimed", push.dispatch(event.event_id))
        self.assertEqual(
            {(str(event.event_id), str(fcm.pk)), (str(event.event_id), str(wns.pk))},
            {c.args for c in send.call_args_list},
        )
        event.refresh_from_db()
        self.assertEqual((PostBoxPushEvent.State.DISPATCHED, 2), (event.state, event.devices))

        Mailbox.objects.filter(pk=self.bob.pk).update(status=MailboxStatus.SUSPENDED)
        self.device(self.bob, PostBoxSession.issue(self.bob)[1], token=FCM_TOKEN + "bob")
        with mock.patch.object(tasks.send_push, "delay") as send:
            self.assertEqual("mailbox_unavailable", push.dispatch(self.event(self.bob).event_id))
        send.assert_not_called()

    def test_the_session_and_mailbox_are_rechecked_at_send_time(self):
        # A send queued before the session was revoked finds no registration:
        # revoking deleted it.
        session = PostBoxSession.issue(self.alice)[1]
        device = self.device(self.alice, session)
        event = self.event(self.alice)
        session.revoke()
        with mock.patch("apps.postbox.push.provider_for") as provider_for:
            outcome = push.deliver(event.event_id, device.pk)
        self.assertEqual((PushOutcome.REJECTED, "gone"), (outcome.status, outcome.code))
        provider_for.assert_not_called()

        # One queued before the session expired finds it inactive.
        session = PostBoxSession.issue(self.alice)[1]
        device = self.device(self.alice, session, token=FCM_TOKEN + "expiring")
        PostBoxSession.objects.filter(pk=session.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        with mock.patch("apps.postbox.push.provider_for") as provider_for:
            outcome = push.deliver(event.event_id, device.pk)
        self.assertEqual((PushOutcome.REJECTED, "inactive"), (outcome.status, outcome.code))
        provider_for.assert_not_called()

        # So is the mailbox: a retry that runs after a suspension sends nothing.
        live = self.device(self.bob, PostBoxSession.issue(self.bob)[1], token=FCM_TOKEN + "bob")
        bob_event = self.event(self.bob)
        Mailbox.objects.filter(pk=self.bob.pk).update(status=MailboxStatus.SUSPENDED)
        with mock.patch("apps.postbox.push.provider_for") as provider_for:
            outcome = push.deliver(bob_event.event_id, live.pk)
        self.assertEqual((PushOutcome.REJECTED, "mailbox_unavailable"),
                         (outcome.status, outcome.code))
        provider_for.assert_not_called()


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class PushProviderTest(PushTestCase):

    def setUp(self):
        super().setUp()
        self.session = PostBoxSession.issue(self.alice)[1]
        self.event_row = self.event(self.alice)

    def fcm(self, *responses):
        http = FakeHttp(*responses)
        return http, FcmPushProvider(enabled=True, project_id="postbox-synthetic",
                                     credentials_file="", session=http,
                                     token_source=lambda refresh: "fcm-access-token")

    def wns(self, *responses):
        http = FakeHttp(*responses)
        return http, WnsPushProvider(enabled=True, tenant_id="synthetic-tenant",
                                     client_id="synthetic-app-id",
                                     client_secret="test-only-client-secret", session=http)

    def deliver_with(self, provider, device):
        with mock.patch("apps.postbox.push.provider_for", return_value=provider):
            return push.deliver(self.event_row.event_id, device.pk)

    def assert_no_mail_content(self, text):
        for forbidden in (self.alice.email, "alice", "Synthetic subject", "@acme.test"):
            self.assertNotIn(forbidden, text)

    def test_fcm_sends_a_data_only_message_of_opaque_identifiers(self):
        device = self.device(self.alice, self.session)
        http, fcm = self.fcm(FakeResponse(200, {"name": "projects/x/messages/1"}))
        outcome = self.deliver_with(fcm, device)
        self.assertEqual((PushOutcome.DELIVERED, "ok"), (outcome.status, outcome.code))

        url, kwargs = http.calls[0]
        self.assertEqual(
            "https://fcm.googleapis.com/v1/projects/postbox-synthetic/messages:send", url)
        self.assertEqual("Bearer fcm-access-token", kwargs["headers"]["Authorization"])
        message = kwargs["json"]["message"]
        self.assertEqual({"token", "data", "android"}, set(message))
        self.assertEqual(FCM_TOKEN, message["token"])
        self.assertEqual({"priority": "high", "ttl": "3600s"}, message["android"])
        self.assertEqual({
            "version": "1", "kind": "new_mail", "event_id": str(self.event_row.event_id),
            "device_registration_id": str(device.pk), "folder": "INBOX",
            "uid_validity": "1790364354", "uid": "7",
        }, message["data"])
        self.assertTrue(all(isinstance(v, str) for v in message["data"].values()))
        self.assert_no_mail_content(json.dumps(kwargs["json"]))
        device.refresh_from_db()
        self.assertIsNotNone(device.last_push_at)

        # An installation ID goes where FCM now wants it.
        fid = self.device(self.alice, self.session, token="cXRwZ3JhbnRlZFRva2Vu9")
        PostBoxPushDevice.objects.filter(pk=fid.pk).update(token_type=PushTokenType.FID)
        http, fcm = self.fcm(FakeResponse(200, {}))
        self.deliver_with(fcm, fid)
        self.assertEqual({"fid", "data", "android"}, set(http.calls[0][1]["json"]["message"]))

    def test_fcm_disables_dead_registrations_but_never_for_a_sender_mismatch(self):
        cases = [
            (FakeResponse(404, {"error": {"details": [{"errorCode": "UNREGISTERED"}]}}), False),
            (FakeResponse(400, {"error": {"details": [{"errorCode": "INVALID_ARGUMENT"}]}}), False),
            (FakeResponse(403, {"error": {"details": [{"errorCode": "SENDER_ID_MISMATCH"}]}}), True),
        ]
        for n, (answer, stays_enabled) in enumerate(cases):
            with self.subTest(status=answer.status_code):
                device = self.device(self.alice, self.session, token=f"{FCM_TOKEN}{n}")
                self.deliver_with(self.fcm(answer)[1], device)
                device.refresh_from_db()
                self.assertEqual(stays_enabled, device.enabled)
                self.assertTrue(device.last_error)

    def test_fcm_transient_failures_retry_a_bounded_number_of_times(self):
        _, fcm = self.fcm(FakeResponse(429, None, {"Retry-After": "120"}))
        outcome = fcm.send(self.device(self.alice, self.session), {"kind": "new_mail"})
        self.assertEqual((PushOutcome.RETRY, 120), (outcome.status, outcome.retry_after))
        self.assertEqual(PushOutcome.RETRY, self.fcm(FakeResponse(503))[1].send(
            self.device(self.alice, self.session, token=FCM_TOKEN + "x"), {}).status)
        self.assertEqual([30, 60, 120, 120, 900],
                         [push.retry_delay(0, None), push.retry_delay(1, None),
                          push.retry_delay(2, None), push.retry_delay(0, 120),
                          push.retry_delay(5, 99999)])

        device = self.device(self.alice, self.session, token=FCM_TOKEN + "retry")
        always_down = mock.Mock(send=mock.Mock(return_value=PushOutcome(PushOutcome.RETRY, "unavailable")))
        with mock.patch("apps.postbox.push.provider_for", return_value=always_down):
            result = tasks.send_push.apply(args=[str(self.event_row.event_id), str(device.pk)])
        self.assertEqual("unavailable", result.get())
        self.assertEqual(1 + tasks.send_push.max_retries, always_down.send.call_count)

    def test_wns_sends_a_raw_push_authenticated_with_entra_id(self):
        device = self.device(self.alice, self.session, provider=PushProvider.WNS, token=WNS_CHANNEL)
        token = FakeResponse(200, {"token_type": "Bearer", "expires_in": "86399",
                                   "access_token": "wns-access-token"})
        http, wns = self.wns(token, FakeResponse(200, None, {"X-WNS-Status": "received"}),
                             FakeResponse(200))
        outcome = self.deliver_with(wns, device)
        self.assertEqual((PushOutcome.DELIVERED, "received"), (outcome.status, outcome.code))

        token_url, token_call = http.calls[0]
        self.assertEqual(
            "https://login.microsoftonline.com/synthetic-tenant/oauth2/v2.0/token", token_url)
        self.assertEqual({"grant_type": "client_credentials", "client_id": "synthetic-app-id",
                          "client_secret": "test-only-client-secret",
                          "scope": "https://wns.windows.com/.default"}, token_call["data"])
        channel_url, push_call = http.calls[1]
        self.assertEqual(WNS_CHANNEL, channel_url)
        self.assertFalse(push_call["allow_redirects"])
        self.assertEqual({
            "Authorization": "Bearer wns-access-token",
            "Content-Type": "application/octet-stream",
            "X-WNS-Type": "wns/raw",
            "X-WNS-Cache-Policy": "cache",
            "X-WNS-TTL": "3600",
        }, push_call["headers"])
        payload = json.loads(push_call["data"])
        self.assertEqual({"version", "kind", "event_id", "device_registration_id", "folder",
                          "uid_validity", "uid"}, set(payload))
        self.assert_no_mail_content(push_call["data"].decode())

        # The token is reused, not fetched per push.
        wns.send(device, {"kind": "new_mail"})
        self.assertEqual(3, len(http.calls))

    def test_wns_disables_expired_or_unknown_channels_but_not_on_a_credential_problem(self):
        for n, (status, stays_enabled) in enumerate(((410, False), (404, False), (403, True))):
            with self.subTest(status=status):
                device = self.device(self.alice, self.session, provider=PushProvider.WNS,
                                     token=f"{WNS_CHANNEL}{n}")
                token = FakeResponse(200, {"access_token": "t", "expires_in": 3600})
                self.deliver_with(self.wns(token, FakeResponse(status))[1], device)
                device.refresh_from_db()
                self.assertEqual(stays_enabled, device.enabled)

    def test_an_unconfigured_provider_sends_nothing_and_breaks_nothing(self):
        fcm_device = self.device(self.alice, self.session)
        wns_device = self.device(self.alice, self.session, provider=PushProvider.WNS,
                                 token=WNS_CHANNEL)
        http = FakeHttp()
        unconfigured = (
            (FcmPushProvider(enabled=True, project_id="", credentials_file="", session=http),
             fcm_device),
            (FcmPushProvider(enabled=False, project_id="p", credentials_file="/x", session=http),
             fcm_device),
            (WnsPushProvider(enabled=True, tenant_id="t", client_id="c", client_secret="",
                             session=http), wns_device),
        )
        for provider, device in unconfigured:
            with self.subTest(provider=provider.name):
                outcome = self.deliver_with(provider, device)
                self.assertEqual((PushOutcome.UNAVAILABLE, "not_configured"),
                                 (outcome.status, outcome.code))
        self.assertEqual([], http.calls)
        for device in (fcm_device, wns_device):
            device.refresh_from_db()
            self.assertTrue(device.enabled)
            self.assertEqual("not_configured", device.last_error)
