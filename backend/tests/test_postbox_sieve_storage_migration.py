"""Non-destructive Sieve migration must preserve existing scripts."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/native-engine/scripts/migrate-maildir-sieve.py"
spec = importlib.util.spec_from_file_location("sieve_migration", SCRIPT)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class SieveMigrationTest(SimpleTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.volume = Path(self.temp.name) / "vmail"
        self.home = self.volume / "example.test" / "immutable-mailbox-uuid"
        (self.home / "sieve").mkdir(parents=True)
        self.source = self.home / "sieve" / "matemail-postbox.sieve"
        self.source.write_text('require ["fileinto"];\nfileinto "Test Emails";\n')
        self.active = self.home / ".dovecot.sieve"
        self.active.symlink_to("sieve/matemail-postbox.sieve")

    def test_dry_run_writes_nothing(self):
        result = migration.migrate(self.volume, self.home, "alice@example.test", False)
        self.assertIn("DRY_RUN", result)
        self.assertFalse((self.volume / ".sieve").exists())
        self.assertTrue(self.active.is_symlink())

    def test_migration_copies_without_mutating_original(self):
        original = self.source.read_bytes()
        result = migration.migrate(self.volume, self.home, "alice@example.test", True)
        self.assertIn("COPIED", result)
        new = self.volume / ".sieve/example.test/alice"
        self.assertEqual((new / "scripts/matemail-postbox.sieve").read_bytes(), original)
        self.assertEqual((new / "active.sieve").readlink().as_posix(), "scripts/matemail-postbox.sieve")
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.active.readlink().as_posix(), "sieve/matemail-postbox.sieve")
        with self.assertRaisesRegex(ValueError, "Destination already exists"):
            migration.migrate(self.volume, self.home, "alice@example.test", True)

    def test_refuses_unexpected_active_script_target(self):
        self.active.unlink()
        target = self.home / "unrelated.sieve"
        target.write_text("keep;")
        self.active.symlink_to("unrelated.sieve")
        with self.assertRaisesRegex(ValueError, "inside original sieve directory"):
            migration.migrate(self.volume, self.home, "alice@example.test", True)
        self.assertFalse((self.volume / ".sieve").exists())
