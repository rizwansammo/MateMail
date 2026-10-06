import importlib.util
import pathlib
from unittest import mock

from django.test import SimpleTestCase

from tests.test_native_engine_provisioning import (
    ADDRESS,
    DOMAIN,
    EngineDatabaseTestCase,
    ValidationError,
    provisioning,
)


CONTROL_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "deploy"
    / "native-engine"
    / "images"
    / "postfix"
    / "engine_control.py"
)
MAIN_CF_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "deploy"
    / "native-engine"
    / "postfix"
    / "main.cf"
)

_spec = importlib.util.spec_from_file_location("fg_engine_control", CONTROL_PATH)
engine_control = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(engine_control)


class ForwardGroupNativeEngineTest(EngineDatabaseTestCase):
    GROUP = f"engineering@{DOMAIN}"
    BOB = f"bob@{DOMAIN}"

    def setUp(self):
        super().setUp()
        self.make_domain()
        self.make_mailbox()
        self.make_mailbox(
            address=self.BOB,
            password="Bob-Initial-Passphrase-2",
        )

    def ensure_group(
        self,
        *,
        destinations=None,
        sender_policy="anyone",
        allowed_senders=None,
        active=True,
    ):
        return provisioning.ensure_forward_group(
            self.conn,
            {
                "address": self.GROUP,
                "destinations": destinations or [ADDRESS, self.BOB],
                "sender_policy": sender_policy,
                "allowed_senders": allowed_senders or [],
                "active": active,
            },
        )

    def test_group_routes_to_each_member_without_becoming_a_mailbox(self):
        self.ensure_group()
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT destination FROM postfix_virtual_alias "
                "WHERE address=%s ORDER BY destination",
                (self.GROUP,),
            )
            destinations = [row[0] for row in cur.fetchall()]
            cur.execute(
                "SELECT count(*) FROM mailbox WHERE address=%s",
                (self.GROUP,),
            )
            mailbox_rows = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM postfix_sender_login WHERE address=%s",
                (self.GROUP,),
            )
            sender_rows = cur.fetchone()[0]

        self.assertEqual([ADDRESS, self.BOB], destinations)
        self.assertEqual(0, mailbox_rows)
        self.assertEqual(0, sender_rows)

    def test_group_destination_and_sender_sets_are_replaced_not_merged(self):
        self.ensure_group(
            destinations=[ADDRESS, self.BOB],
            sender_policy="selected",
            allowed_senders=[ADDRESS, self.BOB],
        )
        self.ensure_group(
            destinations=[self.BOB],
            sender_policy="selected",
            allowed_senders=[self.BOB],
        )

        state = provisioning.get_forward_group(self.conn, self.GROUP)
        self.assertEqual([self.BOB], state["destinations"])
        self.assertEqual([self.BOB], state["allowed_senders"])

    def test_restricted_policy_is_published_through_postfix_view(self):
        self.ensure_group(
            sender_policy="members",
            allowed_senders=[ADDRESS],
        )
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT sender_policy, sender FROM postfix_forward_group_policy "
                "WHERE address=%s",
                (self.GROUP,),
            )
            rows = cur.fetchall()
        self.assertEqual([("members", ADDRESS)], rows)

    def test_anyone_policy_is_visible_even_without_sender_rows(self):
        self.ensure_group(sender_policy="anyone", allowed_senders=[])
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT sender_policy, sender FROM postfix_forward_group_policy "
                "WHERE address=%s",
                (self.GROUP,),
            )
            rows = cur.fetchall()
        self.assertEqual([("anyone", None)], rows)

    def test_disabled_group_disappears_from_routing_and_policy_views(self):
        self.ensure_group(active=False)
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM postfix_virtual_alias WHERE address=%s",
                (self.GROUP,),
            )
            routes = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM postfix_forward_group_policy WHERE address=%s",
                (self.GROUP,),
            )
            policy_rows = cur.fetchone()[0]
        self.assertEqual(0, routes)
        self.assertEqual(0, policy_rows)

    def test_delete_group_is_idempotent(self):
        self.ensure_group()
        provisioning.delete_forward_group(self.conn, self.GROUP)
        provisioning.delete_forward_group(self.conn, self.GROUP)
        self.assertIsNone(provisioning.get_forward_group(self.conn, self.GROUP))

    def test_group_cannot_route_to_itself(self):
        with self.assertRaises(ValidationError):
            self.ensure_group(destinations=[self.GROUP])

    def test_unknown_allowed_sender_fails_closed(self):
        with self.assertRaises(provisioning.NotFound):
            self.ensure_group(
                sender_policy="selected",
                allowed_senders=[f"missing@{DOMAIN}"],
            )


class ForwardGroupPolicyServiceTest(SimpleTestCase):
    GROUP = "engineering@example.invalid"

    def test_anyone_group_allows_unauthenticated_sender(self):
        with mock.patch.object(
            engine_control,
            "lookup_forward_group_policy",
            return_value={"policy": "anyone", "allowed_senders": set()},
        ):
            self.assertEqual(
                "action=DUNNO",
                engine_control.forward_group_verdict(self.GROUP, ""),
            )

    def test_restricted_group_requires_authenticated_allowed_mailbox(self):
        policy = {
            "policy": "members",
            "allowed_senders": {"alice@example.invalid"},
        }
        with mock.patch.object(
            engine_control,
            "lookup_forward_group_policy",
            return_value=policy,
        ):
            allowed = engine_control.forward_group_verdict(
                self.GROUP,
                "alice@example.invalid",
            )
            denied = engine_control.forward_group_verdict(
                self.GROUP,
                "mallory@example.invalid",
            )
            anonymous = engine_control.forward_group_verdict(self.GROUP, "")

        self.assertEqual("action=DUNNO", allowed)
        self.assertTrue(denied.startswith("action=REJECT 5.7.1"))
        self.assertTrue(anonymous.startswith("action=REJECT 5.7.1"))

    def test_non_group_recipient_is_untouched(self):
        with mock.patch.object(
            engine_control,
            "lookup_forward_group_policy",
            return_value=None,
        ):
            self.assertEqual(
                "action=DUNNO",
                engine_control.forward_group_verdict(
                    "ordinary@example.invalid",
                    "",
                ),
            )

    def test_recipient_policy_hook_runs_before_permit_shortcuts(self):
        text = MAIN_CF_PATH.read_text(encoding="utf-8")
        block = text.split("smtpd_recipient_restrictions =", 1)[1].split(
            "\n\n", 1
        )[0]
        policy = block.index("check_policy_service inet:127.0.0.1:10032")
        mynetworks = block.index("permit_mynetworks")
        authenticated = block.index("permit_sasl_authenticated")
        self.assertLess(policy, mynetworks)
        self.assertLess(policy, authenticated)
