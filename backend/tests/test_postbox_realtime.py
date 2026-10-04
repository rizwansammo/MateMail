import json
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

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
