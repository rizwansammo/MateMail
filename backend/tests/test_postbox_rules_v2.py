"""Rules v2: bounded structured filters, multiple actions, no forwarding."""
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.postbox.models import MailRule
from apps.postbox.serializers import MailRuleSerializer
from apps.postbox.sieve import compile_rules


def rule(**kwargs):
    defaults = dict(
        pk=1,
        name="Rules v2",
        enabled=True,
        field="from",
        match="contains",
        value="example.test",
        action="move",
        action_folder="Finance",
        condition_mode="all",
        conditions=[],
        actions=[],
        stop_processing=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class RulesV2SerializerTest(SimpleTestCase):
    def test_structured_payload_mirrors_legacy_primary_fields(self):
        serializer = MailRuleSerializer(data={
            "name": "Invoices",
            "position": 2,
            "enabled": True,
            "condition_mode": "all",
            "conditions": [
                {"field": "sender_domain", "match": "is", "value": "@billing.example.com"},
                {"field": "subject", "match": "contains", "value": "Invoice"},
            ],
            "actions": [
                {"action": "star"},
                {"action": "move", "folder": "Finance"},
            ],
            "stop_processing": True,
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        data = serializer.validated_data
        self.assertEqual("sender_domain", data["field"])
        self.assertEqual("is", data["match"])
        self.assertEqual("billing.example.com", data["value"])
        self.assertEqual("star", data["action"])
        self.assertEqual("", data["action_folder"])
        self.assertEqual(2, len(data["conditions"]))
        self.assertEqual(2, len(data["actions"]))

    def test_forwarding_is_explicitly_refused_in_postbox(self):
        serializer = MailRuleSerializer(data={
            "name": "Unsafe",
            "conditions": [{"field": "from", "match": "contains", "value": "@example.com"}],
            "actions": [{"action": "forward", "destination": "outside@example.net"}],
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn("MateMail Hub", str(serializer.errors))

    def test_only_one_terminal_filing_action_is_allowed(self):
        serializer = MailRuleSerializer(data={
            "name": "Ambiguous",
            "conditions": [{"field": "subject", "match": "contains", "value": "x"}],
            "actions": [{"action": "archive"}, {"action": "delete"}],
        })
        self.assertFalse(serializer.is_valid())
        self.assertIn("one final filing action", str(serializer.errors))

    def test_attachment_and_size_fields_are_strict(self):
        serializer = MailRuleSerializer(data={
            "name": "Attachments",
            "condition_mode": "all",
            "conditions": [
                {"field": "has_attachment", "match": "is", "value": "yes"},
                {"field": "message_size", "match": "over", "value": "5120"},
            ],
            "actions": [{"action": "copy", "folder": "Large Attachments"}],
        })
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual("5120", serializer.validated_data["conditions"][1]["value"])

    def test_body_only_supports_contains_and_negative_contains(self):
        serializer = MailRuleSerializer(data={
            "name": "Body exact",
            "conditions": [{"field": "body", "match": "is", "value": "exact body"}],
            "actions": [{"action": "star"}],
        })
        self.assertFalse(serializer.is_valid())


class RulesV2CompilerTest(SimpleTestCase):
    folders = {"INBOX", "Archive", "Trash", "Finance", "Receipts", "Large Attachments"}

    def test_all_conditions_and_multiple_actions_compile(self):
        script = compile_rules([
            rule(
                conditions=[
                    {"field": "sender_domain", "match": "is", "value": "billing.example.com"},
                    {"field": "subject", "match": "contains", "value": "Invoice"},
                    {"field": "has_attachment", "match": "is", "value": "yes"},
                ],
                actions=[
                    {"action": "star"},
                    {"action": "copy", "folder": "Receipts"},
                    {"action": "move", "folder": "Finance"},
                ],
                stop_processing=True,
            )
        ], valid_folders=self.folders)
        self.assertIn("allof(", script.body)
        self.assertIn('address :domain :is ["From"] "billing.example.com"', script.body)
        self.assertIn('header :contains ["Subject"] "Invoice"', script.body)
        self.assertIn("header :mime :anychild", script.body)
        self.assertIn('addflag "\\\\Flagged";', script.body)
        self.assertIn('fileinto :copy "Receipts";', script.body)
        self.assertIn('fileinto "Finance";', script.body)
        self.assertIn("    stop;", script.body)
        self.assertEqual(
            ["copy", "fileinto", "imap4flags", "mime"],
            script.requires,
        )

    def test_any_negative_body_size_and_unread_compile(self):
        script = compile_rules([
            rule(
                condition_mode="any",
                conditions=[
                    {"field": "body", "match": "not_contains", "value": "unsubscribe"},
                    {"field": "message_size", "match": "over", "value": "2048"},
                ],
                actions=[
                    {"action": "mark_unread"},
                    {"action": "archive"},
                ],
            )
        ], valid_folders=self.folders)
        self.assertIn("anyof(", script.body)
        self.assertIn('not body :contains "unsubscribe"', script.body)
        self.assertIn("size :over 2048K", script.body)
        self.assertIn('removeflag "\\\\Seen";', script.body)
        self.assertIn('fileinto "Archive";', script.body)
        self.assertEqual(["body", "fileinto", "imap4flags"], script.requires)

    def test_attachment_name_uses_mime_children(self):
        script = compile_rules([
            rule(
                conditions=[
                    {"field": "attachment_name", "match": "contains", "value": ".pdf"},
                ],
                actions=[{"action": "copy", "folder": "Receipts"}],
            )
        ], valid_folders=self.folders)
        self.assertIn("header :mime :anychild :contains", script.body)
        self.assertIn('"Content-Disposition", "Content-Type"', script.body)
        self.assertIn('"mime"', script.body)
        self.assertIn('"copy"', script.body)

    def test_missing_copy_destination_skips_whole_rule(self):
        script = compile_rules([
            rule(
                conditions=[{"field": "subject", "match": "contains", "value": "Invoice"}],
                actions=[{"action": "star"}, {"action": "copy", "folder": "Gone"}],
            )
        ], valid_folders=self.folders)
        self.assertNotIn("# Rules v2", script.body)
        self.assertNotIn("addflag", script.body)

    def test_legacy_single_condition_rule_still_compiles(self):
        script = compile_rules([
            rule(conditions=[], actions=[], field="from", match="is",
                 value="sender@example.test", action="move", action_folder="Finance")
        ], valid_folders=self.folders)
        self.assertIn('if address :is ["From"] "sender@example.test"', script.body)
        self.assertIn('fileinto "Finance";', script.body)


class RulesV2FolderReferenceTest(SimpleTestCase):
    def test_copy_and_move_are_folder_dependencies_and_can_be_retargeted(self):
        item = MailRule(
            field="subject", match="contains", value="x",
            action="copy", action_folder="Finance",
            conditions=[{"field": "subject", "match": "contains", "value": "x"}],
            actions=[
                {"action": "copy", "folder": "Finance"},
                {"action": "move", "folder": "Receipts"},
            ],
        )
        self.assertTrue(item.references_folder("Finance"))
        self.assertTrue(item.references_folder("Receipts"))
        self.assertFalse(item.references_folder("Other"))
        self.assertTrue(item.retarget_folder("Finance", "Accounting"))
        self.assertEqual("Accounting", item.actions[0]["folder"])
        self.assertEqual("Accounting", item.action_folder)
