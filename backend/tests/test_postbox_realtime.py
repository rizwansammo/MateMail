import json
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIRequestFactory

from apps.postbox.auth import PostBoxSessionAuthentication
from apps.postbox.views_realtime import RealtimeEventView

from apps.postbox import realtime


class FakePubSub:
    def __init__(self, message=None):
        self.message = message
        self.subscribed = []
        self.closed = False

    def subscribe(self, name):
        self.subscribed.append(name)

    def get_message(self, timeout=0):
        message, self.message = self.message, None
        return message

    def close(self):
        self.closed = True


class FakeRedis:
    def __init__(self, message=None):
        self.published = []
        self.pubsub_instance = FakePubSub(message)

    def publish(self, channel, payload):
        self.published.append((channel, payload))
        return 1

    def pubsub(self, **kwargs):
        return self.pubsub_instance


class PostBoxRealtimeTest(SimpleTestCase):
    def test_publish_is_content_free_and_mailbox_scoped(self):
        fake = FakeRedis()
        mailbox = SimpleNamespace(pk=42)
        with mock.patch.object(realtime, "_client", return_value=fake):
            event_id = realtime.publish(
                mailbox,
                kind="mailbox_changed",
                folder="INBOX",
                uid_validity=11,
                uid=7,
                action="unread",
            )

        self.assertTrue(event_id)
        self.assertEqual(1, len(fake.published))
        channel, raw = fake.published[0]
        self.assertEqual("postbox:realtime:42", channel)
        payload = json.loads(raw)
        self.assertEqual(
            {"event_id", "kind", "folder", "uid_validity", "uid", "action"},
            set(payload),
        )
        self.assertNotIn("subject", raw.lower())
        self.assertNotIn("body", raw.lower())
        self.assertNotIn("sender", raw.lower())

    def test_stream_emits_sse_and_closes_its_subscription(self):
        raw = json.dumps({"event_id": "evt-1", "kind": "new_mail", "folder": "INBOX"})
        fake = FakeRedis({"type": "message", "data": raw})
        with mock.patch.object(realtime, "_client", return_value=fake):
            stream = realtime.stream(9)
            self.assertEqual("retry: 3000\n\n", next(stream))
            self.assertEqual(f"event: mailbox\ndata: {raw}\n\n", next(stream))
            stream.close()

        self.assertEqual(["postbox:realtime:9"], fake.pubsub_instance.subscribed)
        self.assertTrue(fake.pubsub_instance.closed)

    def test_redis_failure_never_breaks_a_mailbox_action(self):
        mailbox = SimpleNamespace(pk=5)
        with mock.patch.object(
            realtime,
            "_client",
            side_effect=OSError("synthetic redis outage"),
        ):
            event_id = realtime.publish(mailbox, kind="mailbox_changed", folder="INBOX")
        self.assertTrue(event_id)


class PostBoxRealtimeEndpointTest(SimpleTestCase):
    """EventSource must stream, while session and mailbox guards still apply."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.url = "/api/postbox/events/"

    def test_eventsource_accept_header_reaches_authenticated_sse_stream(self):
        def grant_mailbox_read(request, permission):
            self.assertEqual("read", permission)
            request.mailbox = SimpleNamespace(pk=9)

        user = SimpleNamespace(pk=10, is_authenticated=True)
        with (
            mock.patch.object(
                PostBoxSessionAuthentication, "authenticate", return_value=(user, None)
            ),
            mock.patch(
                "apps.postbox.views_realtime.postbox_auth.require_active_mailbox_permission",
                side_effect=grant_mailbox_read,
            ),
            mock.patch.object(
                realtime, "stream", return_value=iter(["retry: 3000\\n\\n"])
            ) as stream,
        ):
            response = RealtimeEventView.as_view()(
                self.factory.get(self.url, HTTP_ACCEPT="text/event-stream")
            )
            self.assertEqual(200, response.status_code)
            self.assertTrue(response.streaming)
            self.assertEqual("text/event-stream", response["Content-Type"])
            self.assertEqual("no-cache, no-transform", response["Cache-Control"])
            self.assertEqual("no", response["X-Accel-Buffering"])
            self.assertEqual(b"retry: 3000\\n\\n", b"".join(response.streaming_content))
            stream.assert_called_once_with(9)

    def test_eventsource_accept_does_not_bypass_login(self):
        with mock.patch.object(PostBoxSessionAuthentication, "authenticate", return_value=None):
            response = RealtimeEventView.as_view()(
                self.factory.get(self.url, HTTP_ACCEPT="text/event-stream")
            )

        self.assertEqual(403, response.status_code)
        self.assertFalse(response.streaming)

    def test_eventsource_accept_does_not_bypass_mailbox_permission(self):
        user = SimpleNamespace(pk=10, is_authenticated=True)
        with (
            mock.patch.object(
                PostBoxSessionAuthentication, "authenticate", return_value=(user, None)
            ),
            mock.patch(
                "apps.postbox.views_realtime.postbox_auth.require_active_mailbox_permission",
                side_effect=PermissionDenied("Mailbox read access denied."),
            ),
            mock.patch.object(realtime, "stream") as stream,
        ):
            response = RealtimeEventView.as_view()(
                self.factory.get(self.url, HTTP_ACCEPT="text/event-stream")
            )

        self.assertEqual(403, response.status_code)
        self.assertFalse(response.streaming)
        stream.assert_not_called()
