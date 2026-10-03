"""Phase 2: RFC conversation graph and authenticated read-only API regressions."""
from __future__ import annotations

import unittest
from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.domains.models import Domain
from apps.mailboxes.models import Mailbox, MailboxStatus
from apps.postbox import imap, threading
from tests.factories import (
    FAST_PASSWORD_HASHERS,
    disable_throttling,
    make_tenant,
    make_user,
)


DATE1 = "Wed, 03 Jun 2026 10:00:00 +0000"
DATE2 = "Wed, 03 Jun 2026 11:00:00 +0000"
DATE3 = "Wed, 03 Jun 2026 12:00:00 +0000"
ROLES = {"INBOX": "inbox", "Sent": "sent", "Archive": "archive"}


def mail(
    uid, folder, message_id="", *, date=DATE1, subject="Project",
    sender="alice@example.test", to=None, references=None, reply_to="",
    seen=True, flagged=False, validity=77,
):
    return imap.MessageSummary(
        uid=uid,
        uid_validity=validity,
        folder=folder,
        message_id=message_id,
        subject=subject,
        from_address=sender,
        to=to if to is not None else ["bob@example.test"],
        date=date,
        seen=seen,
        flagged=flagged,
        thread_references=references if references is not None else [],
        in_reply_to=reply_to,
    )


class HeaderThreadingTest(unittest.TestCase):
    def test_inbox_sent_archive_dedup_and_unread_copy(self):
        root = mail(10, "INBOX", "<root@example.test>", seen=False)
        duplicated = mail(9, "Archive", "<root@example.test>", seen=True)
        sent = mail(
            3, "Sent", "<sent@example.test>", date=DATE2,
            subject="Re: Project", sender="bob@example.test",
            references=["<root@example.test>"], reply_to="<root@example.test>",
        )
        answer = mail(
            11, "INBOX", "<answer@example.test>", date=DATE3,
            subject="Re: Project", references=[
                "<root@example.test>", "<sent@example.test>"
            ], reply_to="<sent@example.test>",
        )
        grouped = threading.build_conversations(
            [sent, duplicated, answer, root], ROLES
        )
        self.assertEqual(1, len(grouped))
        conversation = grouped[0]
        self.assertEqual(3, len(conversation.messages))
        self.assertEqual(1, conversation.unread_count)
        self.assertTrue(conversation.has_inbox)
        self.assertEqual("<answer@example.test>", conversation.latest.own_id)
        self.assertEqual(["Archive", "INBOX"], sorted(
            copy.folder for copy in conversation.messages[0].copies
        ))
        self.assertEqual("INBOX", conversation.messages[0].primary.folder)

    def test_same_subject_does_not_infer_a_thread(self):
        x = mail(10, "INBOX", "<x@example.test>")
        y = mail(20, "Sent", "<y@example.test>")
        threads = threading.build_conversations([x, y], ROLES)
        self.assertEqual(2, len(threads))
        self.assertNotEqual(threads[0].id, threads[1].id)

    def test_missing_ancestor_still_connects_replies(self):
        x = mail(
            1, "INBOX", "<x@example.test>",
            references=["<missing@example.test>"],
        )
        y = mail(
            2, "Sent", "<y@example.test>",
            references=["<missing@example.test>", "<x@example.test>"],
            date=DATE2,
        )
        self.assertEqual(
            1, len(threading.build_conversations([x, y], ROLES))
        )

    def test_conflicting_reused_message_id_does_not_merge(self):
        first = mail(1, "INBOX", "<same@example.test>")
        collision = mail(
            2, "Sent", "<same@example.test>",
            sender="attacker@another.test",
        )
        groups = threading.build_conversations([first, collision], ROLES)
        self.assertEqual(2, len(groups))
        self.assertEqual([1, 1], sorted(len(g.messages) for g in groups))

    def test_invalid_headers_and_headerless_mail_stay_distinct(self):
        x = mail(1, "INBOX", "not-an-id", references=["bad-ref"])
        y = mail(2, "INBOX", "not-an-id", references=["bad-ref"])
        groups = threading.build_conversations([x, y], ROLES)
        self.assertEqual(2, len(groups))

    def test_id_parser_bounded_and_domain_case_normalized(self):
        self.assertEqual(
            ("<X@example.test>", "<next@else.test>"),
            threading.message_ids(
                "ignored <X@EXAMPLE.TEST> <X@example.test> <next@ELSE.test>"
            ),
        )
        many = " ".join(f"<id{i}@example.test>" for i in range(200))
        self.assertEqual(100, len(threading.message_ids(many)))

    def test_shared_root_and_flags_survive_out_of_order_input(self):
        src = mail(1, "INBOX", "<root@example.test>", flagged=True)
        reply = mail(
            5, "Sent", "<later@example.test>", date=DATE2,
            references=["<root@example.test>"],
        )
        first = threading.build_conversations([src, reply], ROLES)[0]
        second = threading.build_conversations([reply, src], ROLES)[0]
        self.assertEqual(first.id, second.id)
        self.assertTrue(first.flagged)
        self.assertEqual(0, first.unread_count)

    def test_full_bounded_scan_remains_one_header_only_conversation(self):
        """Exercise the documented 5,000-summary capacity without MIME bodies."""
        root = mail(1, "INBOX", "<root@load.example.test>")
        replies = [
            mail(
                n, "Sent", f"<reply-{n}@load.example.test>",
                subject="Re: Project",
                references=["<root@load.example.test>"],
                reply_to="<root@load.example.test>",
                date=DATE2,
            )
            for n in range(2, 5001)
        ]
        result = threading.build_conversations([*reversed(replies), root], ROLES)
        self.assertEqual(1, len(result))
        self.assertEqual(5000, len(result[0].messages))
        self.assertEqual(0, result[0].unread_count)

    def test_bare_reply_to_references_are_not_subject_fallback(self):
        root = mail(1, "INBOX", "<root@example.test>")
        reply = mail(
            2, "Sent", "<reply@example.test>", date=DATE2,
            reply_to=" <root@EXAMPLE.TEST> ",
        )
        self.assertEqual(
            1, len(threading.build_conversations([root, reply], ROLES))
        )


LOCMEM_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "postbox-phase2-thread-tests",
    }
}


@override_settings(PASSWORD_HASHERS=FAST_PASSWORD_HASHERS, CACHES=LOCMEM_CACHE)
class ConversationAPITest(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()
        disable_throttling(self)
        owner = make_user("thread-owner@example.com")
        tenant = make_tenant(owner, name="Threads", slug="threads")
        domain = Domain.objects.create(
            tenant=tenant, domain="example.com", status="active",
            ownership_status="verified", mail_engine_provisioned=True,
        )
        self.mailbox = Mailbox.objects.create(
            tenant=tenant, domain=domain,
            local_part="alice", email="alice@example.com",
            status=MailboxStatus.ACTIVE, mail_engine_provisioned=True,
        )
        self.api = APIClient()
        with mock.patch("apps.postbox.auth.imap.authenticate", return_value=True), \
             mock.patch("apps.postbox.imap.open_mailbox"):
            response = self.api.post(
                "/api/postbox/auth/login/",
                {"email": self.mailbox.email, "password": "x"}, format="json",
            )
        self.assertEqual(200, response.status_code)

        self.folder_list = [
            imap.FolderInfo(name="INBOX", role="inbox"),
            imap.FolderInfo(name="Sent", role="sent"),
            imap.FolderInfo(name="Archive", role="archive"),
            imap.FolderInfo(name="Junk", role="junk"),
            imap.FolderInfo(name="Trash", role="trash"),
            imap.FolderInfo(name="Drafts", role="drafts"),
            imap.FolderInfo(name="Unselectable", selectable=False),
        ]
        self.messages = {
            "INBOX": [
                mail(
                    10, "INBOX", "<root@example.test>",
                    seen=False, sender="client@example.test",
                ),
            ],
            "Sent": [
                mail(
                    20, "Sent", "<reply@example.test>", date=DATE2,
                    subject="Re: Project", sender="alice@example.com",
                    reply_to="<root@example.test>",
                ),
                mail(
                    21, "Sent", "<unrelated@example.test>", date=DATE3,
                    subject="Another", sender="alice@example.com",
                ),
            ],
            "Archive": [
                mail(
                    9, "Archive", "<root@example.test>",
                    seen=True, sender="client@example.test",
                )
            ],
        }

    def mock_mailbox(self, opener, *, validity=77):
        connection = opener.return_value.__enter__.return_value
        connection.list_folders.return_value = self.folder_list
        state = {"folder": ""}

        def select(folder, readonly=False):
            state["folder"] = folder
            return imap.FolderInfo(name=folder, uid_validity=validity)

        connection.select.side_effect = select
        connection.search_uids.side_effect = lambda criteria: [
            entry.uid for entry in self.messages.get(state["folder"], [])
        ]
        connection.fetch_summaries.side_effect = lambda uids: [
            entry for entry in self.messages.get(state["folder"], [])
            if entry.uid in uids
        ]
        return connection

    def test_scoped_list_pagination_and_copy_aware_unread(self):
        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            connection = self.mock_mailbox(opener)
            response = self.api.get(
                "/api/postbox/conversations/?scope=inbox&page=1&page_size=1"
            )
            opener.assert_called_once_with(self.mailbox.email)
            scanned = [call.args[0] for call in connection.select.call_args_list]
            self.assertEqual(["INBOX", "Sent", "Archive"], scanned)

        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual(1, body["total"])
        self.assertFalse(body["has_next"])
        thread = body["results"][0]
        self.assertEqual(2, thread["message_count"])
        self.assertEqual(1, thread["unread_count"])
        self.assertEqual("Sent", thread["latest"]["folder"])
        self.assertEqual("Re: Project", thread["subject"])
        self.assertEqual(["alice@example.com", "bob@example.test", "client@example.test"],
                         thread["participants"])

    def test_all_scope_returns_sent_only_with_stable_pagination(self):
        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            self.mock_mailbox(opener)
            response = self.api.get(
                "/api/postbox/conversations/?scope=all&page=1&page_size=1"
            )
        self.assertEqual(200, response.status_code)
        self.assertEqual(2, response.json()["total"])
        self.assertTrue(response.json()["has_next"])
        self.assertEqual("Another", response.json()["results"][0]["subject"])

    def test_message_anchor_returns_ascending_members_with_copies(self):
        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            self.mock_mailbox(opener)
            response = self.api.get(
                "/api/postbox/conversations/for-message/"
                "?folder=INBOX&uid=10&uid_validity=77"
            )
        self.assertEqual(200, response.status_code)
        thread = response.json()
        self.assertEqual(2, len(thread["messages"]))
        first = thread["messages"][0]
        self.assertEqual("INBOX", first["folder"])
        self.assertEqual(2, len(first["copies"]))
        self.assertFalse(first["seen"])
        self.assertEqual("Sent", thread["messages"][1]["folder"])

    def test_stale_uidvalidity_fails_closed(self):
        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            self.mock_mailbox(opener)
            response = self.api.get(
                "/api/postbox/conversations/for-message/"
                "?folder=INBOX&uid=10&uid_validity=123"
            )
        self.assertEqual(409, response.status_code)

    def test_unknown_and_excluded_folder_not_exposed(self):
        for folder in ("Junk", "NotMyFolder"):
            with self.subTest(folder=folder), \
                 mock.patch("apps.postbox.imap.open_mailbox") as opener:
                self.mock_mailbox(opener)
                response = self.api.get(
                    "/api/postbox/conversations/for-message/",
                    {"folder": folder, "uid": 1, "uid_validity": 77},
                )
            self.assertEqual(404, response.status_code)

    def test_missing_uid_returns_404_not_an_unrelated_message(self):
        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            self.mock_mailbox(opener)
            response = self.api.get(
                "/api/postbox/conversations/for-message/",
                {"folder": "INBOX", "uid": 999, "uid_validity": 77},
            )
        self.assertEqual(404, response.status_code)

    def test_oversized_header_scan_does_not_silently_truncate(self):
        with mock.patch("apps.postbox.imap.open_mailbox") as opener:
            connection = self.mock_mailbox(opener)
            connection.search_uids.return_value = list(range(1, 5002))
            connection.search_uids.side_effect = None
            response = self.api.get("/api/postbox/conversations/?scope=all")
            connection.fetch_summaries.assert_not_called()
        self.assertEqual(400, response.status_code)
        self.assertIn("too many messages", response.json()["detail"])

    def test_invalid_query_rejected_before_imap(self):
        for query in (
            "?page=0", "?page_size=101", "?scope=trash", "?page=oops",
        ):
            with self.subTest(query=query), \
                 mock.patch("apps.postbox.imap.open_mailbox") as opener:
                response = self.api.get("/api/postbox/conversations/" + query)
                self.assertEqual(400, response.status_code)
                opener.assert_not_called()
