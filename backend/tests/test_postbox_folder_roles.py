"""
Folder roles: what the server says, what we infer, and what we refuse to infer.

WHY THIS EXISTS
    Sent, Drafts, Trash, Junk and Archive were reaching the UI with no role at
    all, so they fell into the "custom folders" branch and were drawn with one
    generic icon. Two causes, both confirmed against production:

      1. `ensure_standard_folders()` sent a bare `CREATE "Sent"`, because
         `imaplib.create()` cannot pass RFC 6154's `USE (\\Sent)`. Nothing
         MateMail has ever created carries an attribute.
      2. The Native Engine's Dovecot declares no `special_use` for any mailbox
         either — `doveconf` returns none — so it supplies nothing on our
         behalf.

    It was not only cosmetic: `views_compose` resolves the Sent folder with
    `roles.get("sent", "Sent")`, and had been relying on that English default
    for every filed copy.

THE LINE THESE TESTS HOLD
    A real special-use attribute is authoritative. The name fallback runs only
    when the server offered nothing, matches the WHOLE name, and refuses
    anything that merely looks like a system folder — because promoting
    "Old Sent" would start filing a customer's sent mail into their archive
    without telling them.
"""
from unittest import mock

from django.test import SimpleTestCase

from apps.postbox.imap import (
    CANONICAL_ROLE_NAMES,
    SPECIAL_USE_FOR_CREATE,
    MailboxConnection,
)


def list_response(*rows: str):
    """An IMAP LIST reply, as imaplib hands it back."""
    return "OK", [row.encode() for row in rows]


class RoleResolutionTest(SimpleTestCase):
    """`list_folders` decides the role. These are its two inputs."""

    def folders(self, *rows: str):
        connection = MailboxConnection.__new__(MailboxConnection)
        connection._imap = mock.Mock()
        connection._imap.list.return_value = list_response(*rows)
        return {f.name: f.role for f in connection.list_folders()}

    # ── the server is authoritative ─────────────────────────────────────────

    def test_a_special_use_attribute_sets_the_role(self):
        roles = self.folders(
            r'(\HasNoChildren \Sent) "." "Sent"',
            r'(\HasNoChildren \Drafts) "." "Drafts"',
            r'(\HasNoChildren \Trash) "." "Trash"',
            r'(\HasNoChildren \Junk) "." "Junk"',
            r'(\HasNoChildren \Archive) "." "Archive"',
        )
        self.assertEqual(
            {"Sent": "sent", "Drafts": "drafts", "Trash": "trash",
             "Junk": "junk", "Archive": "archive"},
            roles,
        )

    def test_a_designated_mailbox_is_not_shadowed_by_a_same_named_one(self):
        """
        THE ORDERING BUG.

        Downstream builds `{f.role: f.name for f in folders if f.role}` — a
        dict, so for one role the LAST row wins. Resolving row by row let a
        name guess replace the server's own designation whenever LIST put
        it second, and sent mail would then be filed into a folder the
        server never designated.

        Only the flagged mailbox may hold the role, whichever order it
        arrives in.
        """
        flagged_first = self.folders(
            r'(\HasNoChildren \Sent) "." "Enviados"',
            r'(\HasNoChildren) "." "Sent"',
        )
        self.assertEqual({"Enviados": "sent", "Sent": ""}, flagged_first)

        flagged_last = self.folders(
            r'(\HasNoChildren) "." "Sent"',
            r'(\HasNoChildren \Sent) "." "Enviados"',
        )
        self.assertEqual({"Sent": "", "Enviados": "sent"}, flagged_last)

        # The property that actually matters: exactly one mailbox per role,
        # and it is the designated one — in either order.
        for roles in (flagged_first, flagged_last):
            owners = [name for name, role in roles.items() if role == "sent"]
            self.assertEqual(["Enviados"], owners)

    def test_the_role_map_downstream_code_builds_is_unambiguous(self):
        """
        The exact comprehension used in views_compose, tasks and
        views_settings. This is what the two-pass resolution protects.
        """
        for rows in (
            (r'(\Sent) "." "Enviados"', r'() "." "Sent"'),
            (r'() "." "Sent"', r'(\Sent) "." "Enviados"'),
        ):
            connection = MailboxConnection.__new__(MailboxConnection)
            connection._imap = mock.Mock()
            connection._imap.list.return_value = list_response(*rows)
            folders = connection.list_folders()

            role_map = {f.role: f.name for f in folders if f.role}
            self.assertEqual("Enviados", role_map["sent"], rows)

    def test_two_unflagged_candidates_do_not_both_claim_a_role(self):
        """
        Nothing designated Sent, and two folders could be it. One takes the
        role and the other stays custom — a duplicate would put the same
        folder twice in the sidebar and make the role map order-dependent
        again.
        """
        roles = self.folders(
            r'(\HasNoChildren) "." "Sent"',
            r'(\HasNoChildren) "." "sent"',
        )
        self.assertEqual(1, sum(1 for role in roles.values() if role == "sent"))

    def test_an_attribute_claims_the_role_even_with_no_name_match(self):
        roles = self.folders(r'(\HasNoChildren \Junk) "." "Correo no deseado"')
        self.assertEqual({"Correo no deseado": "junk"}, roles)

    # ── the fallback, when the server says nothing ──────────────────────────

    def test_exact_canonical_names_get_a_role(self):
        roles = self.folders(
            r'(\HasNoChildren) "." "INBOX"',
            r'(\HasNoChildren) "." "Sent"',
            r'(\HasNoChildren) "." "Drafts"',
            r'(\HasNoChildren) "." "Trash"',
            r'(\HasNoChildren) "." "Junk"',
            r'(\HasNoChildren) "." "Archive"',
            r'(\HasNoChildren) "." "Scheduled"',
        )
        self.assertEqual(
            {"INBOX": "inbox", "Sent": "sent", "Drafts": "drafts",
             "Trash": "trash", "Junk": "junk", "Archive": "archive",
             "Scheduled": "scheduled"},
            roles,
        )

    def test_the_match_is_case_insensitive(self):
        roles = self.folders(
            r'(\HasNoChildren) "." "sent"',
            r'(\HasNoChildren) "." "DRAFTS"',
            r'(\HasNoChildren) "." "tRaSh"',
        )
        self.assertEqual({"sent": "sent", "DRAFTS": "drafts", "tRaSh": "trash"}, roles)

    def test_spam_is_junk(self):
        self.assertEqual({"Spam": "junk"}, self.folders(r'(\HasNoChildren) "." "Spam"'))

    # ── what must stay custom ───────────────────────────────────────────────

    def test_folders_that_merely_resemble_system_folders_stay_custom(self):
        """
        The reason the match is whole-name. Each of these is somebody's own
        folder, and promoting one would silently redirect their mail.
        """
        roles = self.folders(
            r'(\HasNoChildren) "." "Old Sent"',
            r'(\HasNoChildren) "." "Sent 2025"',
            r'(\HasNoChildren) "." "My Archive"',
            r'(\HasNoChildren) "." "Drafts old"',
            r'(\HasNoChildren) "." "Junk Mail Backup"',
            r'(\HasNoChildren) "." "Archived"',
            r'(\HasNoChildren) "." "Clients"',
        )
        self.assertEqual(
            {"Old Sent": "", "Sent 2025": "", "My Archive": "",
             "Drafts old": "", "Junk Mail Backup": "", "Archived": "",
             "Clients": ""},
            roles,
        )

    def test_a_nested_folder_is_not_promoted(self):
        """
        `INBOX.Sent` is a different mailbox from `Sent`, and matching the last
        path segment would also promote `Clients.Sent`. Conservative wins.
        """
        roles = self.folders(r'(\HasNoChildren) "." "INBOX.Sent"')
        self.assertEqual({"INBOX.Sent": ""}, roles)

    def test_every_canonical_name_maps_to_a_role_the_ui_pins(self):
        """The fallback and the sidebar have to agree on the vocabulary."""
        pinned = {
            "inbox", "sent", "drafts", "trash", "junk", "archive", "scheduled",
        }
        self.assertEqual(pinned, set(CANONICAL_ROLE_NAMES.values()))

    def test_the_map_is_only_the_names_we_have_evidence_for(self):
        """
        The folders MateMail creates, plus INBOX and the Spam spelling of
        Junk. `Sent Items` and `Deleted Items` are Outlook's names and no
        mailbox here has been observed using them — adding them would be
        widening a heuristic on a guess, and the whole point of this
        fallback is that it guesses as little as possible.
        """
        self.assertEqual(
            {
                "INBOX", "SENT", "DRAFTS", "TRASH",
                "JUNK", "SPAM", "ARCHIVE", "SCHEDULED",
            },
            set(CANONICAL_ROLE_NAMES),
        )


class CreateSpecialUseTest(SimpleTestCase):
    """
    Creating a standard folder should label it, where the server allows.

    WHY CAPABILITY IS RE-ASKED
        `imaplib` fills `IMAP4.capabilities` from `_get_capabilities()`, which
        it calls from `__init__` and from `starttls()` and nowhere else —
        `login()` does not refresh it. The attribute therefore holds the
        PRE-authentication list for the whole connection, and servers commonly
        advertise a smaller set before login. Reading it to decide what the
        authenticated session supports asks the wrong question and gets "no".
    """

    def connection(
        self,
        *,
        capability=("IMAP4REV1",),
        capability_status="OK",
        capability_raises=None,
        create_status="OK",
        use_raises=None,
        stale_preauth=("IMAP4REV1", "STARTTLS"),
    ):
        connection = MailboxConnection.__new__(MailboxConnection)
        connection._imap = mock.Mock()

        # The stale pre-auth tuple imaplib caches. Nothing may read it.
        connection._imap.capabilities = stale_preauth

        if capability_raises is not None:
            connection._imap.capability.side_effect = capability_raises
        else:
            connection._imap.capability.return_value = (
                capability_status,
                [" ".join(capability).encode()],
            )

        connection._imap.create.return_value = ("OK", [b"done"])
        if use_raises is not None:
            connection._imap._simple_command.side_effect = use_raises
        else:
            connection._imap._simple_command.return_value = (create_status, [b""])
        return connection

    # ── the capability itself ───────────────────────────────────────────────

    def test_a_capability_advertised_after_authentication_is_detected(self):
        """
        The case the old code got wrong: the server advertises it only once
        logged in, and the pre-auth tuple does not mention it.
        """
        connection = self.connection(
            capability=("IMAP4REV1", "CREATE-SPECIAL-USE", "IDLE"),
            stale_preauth=("IMAP4REV1", "STARTTLS"),
        )
        self.assertTrue(connection._supports_special_use_create())
        connection._imap.capability.assert_called_once_with()

    def test_the_stale_preauth_tuple_is_not_consulted(self):
        """
        Inverted: the pre-auth tuple claims support and the authenticated
        session does not. Trusting the cached attribute would send a parameter
        the live session rejects.
        """
        connection = self.connection(
            capability=("IMAP4REV1",),
            stale_preauth=("IMAP4REV1", "CREATE-SPECIAL-USE"),
        )
        self.assertFalse(connection._supports_special_use_create())

    def test_capability_is_asked_once_per_connection(self):
        """
        `ensure_standard_folders` asks for six folders on every sign-in. Six
        round trips for an answer that cannot change is six too many.
        """
        connection = self.connection(capability=("CREATE-SPECIAL-USE",))
        for _ in range(6):
            connection._supports_special_use_create()
        connection._imap.capability.assert_called_once_with()

    def test_a_multi_line_capability_response_is_parsed(self):
        connection = MailboxConnection.__new__(MailboxConnection)
        connection._imap = mock.Mock()
        connection._imap.capability.return_value = (
            "OK", [b"IMAP4REV1 IDLE", b"CREATE-SPECIAL-USE LITERAL+"],
        )
        self.assertTrue(connection._supports_special_use_create())

    def test_a_failed_capability_query_is_treated_as_no_support(self):
        """
        Safe direction. An unlabelled Sent folder is cosmetic; a sign-in that
        fails because CAPABILITY misbehaved is not.
        """
        import imaplib

        for kwargs in (
            {"capability_raises": imaplib.IMAP4.error("broken")},
            {"capability_status": "NO"},
        ):
            connection = self.connection(**kwargs)
            self.assertFalse(connection._supports_special_use_create())

    # ── what gets sent ──────────────────────────────────────────────────────

    def test_it_sends_the_use_parameter_when_the_server_supports_it(self):
        connection = self.connection(capability=("CREATE-SPECIAL-USE",))

        self.assertEqual("OK", connection._create_folder("Sent", "\\Sent"))

        connection._imap._simple_command.assert_called_once_with(
            "CREATE", '"Sent"', "(USE (\\Sent))"
        )
        connection._imap.create.assert_not_called()

    def test_it_sends_a_plain_create_when_the_capability_is_absent(self):
        """
        Production's Dovecot is this case. Sending the parameter anyway would
        get a BAD and leave the mailbox without a Sent folder at all.
        """
        connection = self.connection(capability=("IMAP4REV1", "IDLE"))

        self.assertEqual("OK", connection._create_folder("Sent", "\\Sent"))

        connection._imap._simple_command.assert_not_called()
        connection._imap.create.assert_called_once_with('"Sent"')

    def test_a_failed_capability_query_still_creates_the_folder(self):
        import imaplib

        connection = self.connection(
            capability_raises=imaplib.IMAP4.error("no CAPABILITY")
        )

        self.assertEqual("OK", connection._create_folder("Sent", "\\Sent"))
        connection._imap._simple_command.assert_not_called()
        connection._imap.create.assert_called_once_with('"Sent"')

    def test_a_refused_use_parameter_falls_back_to_a_plain_create(self):
        """
        A server may advertise the capability and still refuse a particular
        attribute. An unlabelled Sent folder is cosmetic; no Sent folder is not.
        """
        connection = self.connection(
            capability=("CREATE-SPECIAL-USE",), create_status="NO"
        )

        self.assertEqual("OK", connection._create_folder("Sent", "\\Sent"))
        connection._imap.create.assert_called_once_with('"Sent"')

    def test_a_raised_error_falls_back_to_a_plain_create(self):
        import imaplib

        connection = self.connection(
            capability=("CREATE-SPECIAL-USE",),
            use_raises=imaplib.IMAP4.error("command refused"),
        )

        self.assertEqual("OK", connection._create_folder("Sent", "\\Sent"))
        connection._imap.create.assert_called_once_with('"Sent"')

    def test_a_folder_with_no_attribute_uses_a_plain_create(self):
        """`Scheduled` is MateMail's own — IMAP has no attribute for it."""
        connection = self.connection(capability=("CREATE-SPECIAL-USE",))

        self.assertEqual("OK", connection._create_folder("Scheduled", None))

        connection._imap._simple_command.assert_not_called()
        connection._imap.create.assert_called_once_with('"Scheduled"')

    def test_every_required_attribute_is_a_real_special_use_flag(self):
        for name, attribute in SPECIAL_USE_FOR_CREATE.items():
            self.assertTrue(attribute.startswith("\\"), f"{name}: {attribute}")
