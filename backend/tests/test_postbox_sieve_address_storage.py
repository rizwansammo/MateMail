"""Regression tests: address-aware filters and Sieve outside backed-up Maildir."""
from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.postbox.sieve import compile_rules

ROOT = Path(__file__).resolve().parents[2]


def example(field="from", match="is", value="sender@example.test", action="move"):
    return SimpleNamespace(
        pk=1, name="Example", enabled=True, field=field, match=match,
        value=value, action=action, action_folder="Test Emails",
        stop_processing=False,
    )


class SieveAddressRuleTest(SimpleTestCase):
    def compile(self, item):
        return compile_rules([item], valid_folders={"Test Emails"}).body

    def test_from_exact_matches_mailbox_address_not_display_name(self):
        body = self.compile(example())
        self.assertIn('if address :is ["From"] "sender@example.test"', body)
        self.assertNotIn('if header :is ["From"]', body)
        self.assertIn('fileinto "Test Emails";', body)

    def test_from_contains_matches_address_part(self):
        body = self.compile(example(match="contains", value="@example.test"))
        self.assertIn('if address :contains ["From"] "@example.test"', body)

    def test_to_includes_cc_with_address_semantics(self):
        body = self.compile(example(field="to"))
        self.assertIn('if address :is ["To", "Cc"] "sender@example.test"', body)

    def test_subject_retains_full_header_text_semantics(self):
        body = self.compile(example(field="subject", value="Check invoices"))
        self.assertIn('if header :is ["Subject"] "Check invoices"', body)
        self.assertNotIn('if address :is ["Subject"]', body)

    def test_disabled_rule_writes_no_address_matcher(self):
        item = example()
        item.enabled = False
        body = self.compile(item)
        self.assertNotIn("if address ", body)


class SieveStorageContractTest(SimpleTestCase):
    def test_sieve_storage_is_outside_user_maildir_but_inside_backed_up_vmail(self):
        config = (ROOT / "deploy/native-engine/dovecot/dovecot.conf").read_text()
        self.assertIn("mail_path = ~/", config)
        self.assertIn("path = /var/vmail/.sieve/%{user | domain}/%{user | username}/scripts", config)
        self.assertIn("active_path = /var/vmail/.sieve/%{user | domain}/%{user | username}/active.sieve", config)
        self.assertNotIn("path = ~/sieve", config)
        self.assertNotIn("active_path = ~/.dovecot.sieve", config)
        backup = (ROOT / "deploy/native-engine/docker-compose.yml").read_text()
        self.assertIn("native_vmail:/var/vmail", backup)
        self.assertIn("sieve_script personal {", config)
