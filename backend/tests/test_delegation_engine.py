from tests.test_native_engine_provisioning import (
    ADDRESS,
    DOMAIN,
    EngineDatabaseTestCase,
    provisioning,
)


class DelegationNativeEngineTest(EngineDatabaseTestCase):
    DELEGATE = f"assistant@{DOMAIN}"
    ALIAS = f"executive-office@{DOMAIN}"

    def setUp(self):
        super().setUp()
        self.make_domain()
        self.make_mailbox()
        self.make_mailbox(
            address=self.DELEGATE,
            password="Assistant-Initial-Passphrase-2",
        )

    def test_personal_mailbox_keeps_direct_login_while_delegate_gains_sender_right(self):
        provisioning.ensure_mailbox(
            self.conn,
            {
                "address": ADDRESS,
                "display_name": "Executive",
                "login_enabled": True,
                "authorized_senders": [self.DELEGATE],
            },
            "",
        )

        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM dovecot_auth WHERE address=%s",
                (ADDRESS,),
            )
            auth_rows = cur.fetchone()[0]
            cur.execute(
                "SELECT owner FROM postfix_sender_login "
                "WHERE address=%s ORDER BY owner",
                (ADDRESS,),
            )
            owners = [row[0] for row in cur.fetchall()]

        self.assertEqual(1, auth_rows)
        self.assertEqual([ADDRESS, self.DELEGATE], owners)
        self.assertIn(
            ADDRESS,
            provisioning.authorized_send_as(self.conn, self.DELEGATE),
        )

    def test_alias_of_delegated_mailbox_inherits_delegate_sender_authorization(self):
        provisioning.ensure_mailbox(
            self.conn,
            {
                "address": ADDRESS,
                "login_enabled": True,
                "authorized_senders": [self.DELEGATE],
            },
            "",
        )
        provisioning.ensure_alias(
            self.conn,
            {
                "address": self.ALIAS,
                "destinations": [ADDRESS],
            },
        )

        self.assertIn(
            self.ALIAS,
            provisioning.authorized_send_as(self.conn, self.DELEGATE),
        )

    def test_removing_delegate_sender_right_does_not_change_target_password_or_login(self):
        provisioning.ensure_mailbox(
            self.conn,
            {
                "address": ADDRESS,
                "login_enabled": True,
                "authorized_senders": [self.DELEGATE],
            },
            "",
        )
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT password_hash FROM mailbox WHERE address=%s",
                (ADDRESS,),
            )
            before_hash = cur.fetchone()[0]

        provisioning.ensure_mailbox(
            self.conn,
            {
                "address": ADDRESS,
                "login_enabled": True,
                "authorized_senders": [],
            },
            "",
        )

        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT password_hash, login_enabled FROM mailbox WHERE address=%s",
                (ADDRESS,),
            )
            after_hash, login_enabled = cur.fetchone()
            cur.execute(
                "SELECT owner FROM postfix_sender_login "
                "WHERE address=%s ORDER BY owner",
                (ADDRESS,),
            )
            owners = [row[0] for row in cur.fetchall()]

        self.assertEqual(before_hash, after_hash)
        self.assertTrue(login_enabled)
        self.assertEqual([ADDRESS], owners)
