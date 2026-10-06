from tests.test_native_engine_provisioning import (
    ADDRESS,
    DOMAIN,
    EngineDatabaseTestCase,
    ValidationError,
    provisioning,
)


class TeamBoxNativeEngineTest(EngineDatabaseTestCase):
    SUPPORT = f"support@{DOMAIN}"
    SUPPORT_ALIAS = f"help@{DOMAIN}"
    BOB = f"bob@{DOMAIN}"

    def setUp(self):
        super().setUp()
        self.make_domain()
        self.make_mailbox()
        self.make_mailbox(
            address=self.BOB,
            password="Bob-Initial-Passphrase-2",
        )

    def make_team_box(self, senders=()):
        return provisioning.ensure_mailbox(
            self.conn,
            {
                "address": self.SUPPORT,
                "display_name": "Customer Support",
                "login_enabled": False,
                "authorized_senders": list(senders),
            },
            "",
        )

    def test_team_box_is_deliverable_but_absent_from_direct_auth(self):
        self.make_team_box()
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT password_hash, login_enabled FROM mailbox WHERE address=%s",
                (self.SUPPORT,),
            )
            password_hash, login_enabled = cur.fetchone()
            cur.execute(
                "SELECT count(*) FROM postfix_virtual_mailbox WHERE address=%s",
                (self.SUPPORT,),
            )
            delivered = cur.fetchone()[0]
            cur.execute(
                "SELECT count(*) FROM dovecot_auth WHERE address=%s",
                (self.SUPPORT,),
            )
            auth_rows = cur.fetchone()[0]

        self.assertIsNone(password_hash)
        self.assertFalse(login_enabled)
        self.assertEqual(1, delivered)
        self.assertEqual(0, auth_rows)

    def test_team_box_sender_authorization_is_enforced_by_postfix_view(self):
        self.make_team_box([ADDRESS])
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT owner FROM postfix_sender_login WHERE address=%s ORDER BY owner",
                (self.SUPPORT,),
            )
            owners = [row[0] for row in cur.fetchall()]
        self.assertEqual([ADDRESS], owners)
        self.assertIn(
            self.SUPPORT,
            provisioning.authorized_send_as(self.conn, ADDRESS),
        )
        self.assertNotIn(
            self.SUPPORT,
            provisioning.authorized_send_as(self.conn, self.BOB),
        )

    def test_sender_set_is_replaced_not_merged(self):
        self.make_team_box([ADDRESS])
        self.make_team_box([self.BOB])
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT owner FROM postfix_sender_login WHERE address=%s ORDER BY owner",
                (self.SUPPORT,),
            )
            owners = [row[0] for row in cur.fetchall()]
        self.assertEqual([self.BOB], owners)

    def test_alias_of_team_box_inherits_team_box_sender_authorization(self):
        self.make_team_box([ADDRESS])
        provisioning.ensure_alias(
            self.conn,
            {
                "address": self.SUPPORT_ALIAS,
                "destinations": [self.SUPPORT],
            },
        )
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT owner FROM postfix_sender_login WHERE address=%s",
                (self.SUPPORT_ALIAS,),
            )
            owners = [row[0] for row in cur.fetchall()]
        self.assertEqual([ADDRESS], owners)

    def test_direct_password_cannot_be_set_on_team_box(self):
        self.make_team_box()
        with self.assertRaises(ValidationError):
            provisioning.set_mailbox_password(
                self.conn,
                self.SUPPORT,
                "This-Must-Remain-Blocked-9",
            )

    def test_unknown_authorized_sender_fails_closed(self):
        with self.assertRaises(provisioning.NotFound):
            self.make_team_box([f"missing@{DOMAIN}"])
