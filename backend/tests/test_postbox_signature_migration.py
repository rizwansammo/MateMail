"""
The backfill that classifies signatures written before `kind` existed.

WHY IT IS CONSERVATIVE
    Production holds one signature row, and it is the broken one: a ~3.5 KB
    HTML document stored in `text` because the settings UI gave people nowhere
    else to put it.

    The temptation is to treat anything containing a tag as HTML. That would be
    wrong, and silently: a plain-text signature reading

        Ops <ops@example.com>

    would be reinterpreted, the address deleted as an unknown element, and the
    owner would have no way to know their signature had changed. So the test is
    a document wrapper — `<!doctype html>` or `<html>` — which nobody writes by
    accident in prose.

    These tests run the migration's own function against rows built here, so
    what is proved is the function that will actually run against the database.
"""
import pathlib

from django.test import TestCase

from apps.postbox.models import MailSignature, SignatureKind
from tests.factories import make_domain, make_mailbox, make_tenant, make_user

PRODUCTION_SHAPE = """<!doctype html>
<html>
<head><meta charset="utf-8"><title>Rizwan Signature</title>
<style>.sig { font-weight: bold }</style></head>
<body>
  <table cellpadding="0" cellspacing="0"><tr><td>
    <strong>Rizwan Sammo</strong><br>
    Chief Business Officer<br>
    <a href="https://netamate.com">NetaMate Solutions</a>
  </td></tr></table>
</body>
</html>"""


class _Apps:
    """The minimal `apps` a RunPython function needs."""

    @staticmethod
    def get_model(app_label, model_name):
        assert (app_label, model_name) == ("postbox", "MailSignature")
        return MailSignature


class SignatureBackfillTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        owner = make_user("owner@acme.example")
        cls.tenant = make_tenant(owner)
        cls.domain = make_domain(cls.tenant, "acme.example")
        cls.mailbox = make_mailbox(cls.tenant, cls.domain, local_part="alice")

    def setUp(self):
        # Imported lazily and by file path: a migration module name starts with
        # a digit, so it cannot be imported with a normal `from ... import`.
        import importlib

        self.classify = importlib.import_module(
            "apps.postbox.migrations.0004_signature_kind_backfill"
        ).classify

    def make(self, **kwargs):
        kwargs.setdefault("mailbox", self.mailbox)
        kwargs.setdefault("name", "Sig")
        return MailSignature.objects.create(**kwargs)

    def backfill(self):
        self.classify(_Apps(), None)

    # ── the row this exists for ─────────────────────────────────────────────

    def test_the_production_row_becomes_a_working_html_signature(self):
        row = self.make(text=PRODUCTION_SHAPE, use_for_new=True, use_for_replies=True)
        self.backfill()
        row.refresh_from_db()

        self.assertEqual(SignatureKind.HTML, row.kind)
        self.assertIn("Rizwan Sammo", row.html)
        self.assertIn("<strong>", row.html)
        # The wrapper is gone from what will be sent.
        for marker in ("<!doctype", "<html", "<head", "<title", "<style"):
            self.assertNotIn(marker, row.html.lower())
        # And the words in <title> are not in either field.
        self.assertNotIn("Rizwan Signature", row.html)
        self.assertNotIn("Rizwan Signature", row.text)

    def test_it_gains_a_readable_plain_text_fallback(self):
        row = self.make(text=PRODUCTION_SHAPE)
        self.backfill()
        row.refresh_from_db()

        self.assertIn("Rizwan Sammo", row.text)
        self.assertIn("Chief Business Officer", row.text)
        self.assertNotIn("<", row.text)

    def test_defaults_are_preserved(self):
        """Migrating content must not quietly change which signature is used."""
        row = self.make(text=PRODUCTION_SHAPE, use_for_new=True, use_for_replies=True)
        self.backfill()
        row.refresh_from_db()

        self.assertTrue(row.use_for_new)
        self.assertTrue(row.use_for_replies)

    # ── what must NOT be reinterpreted ──────────────────────────────────────

    def test_plain_text_containing_angle_brackets_is_left_alone(self):
        """
        The case that makes a looser rule dangerous. Reinterpreting this as
        HTML would delete the address as an unknown element.
        """
        original = "Ops <ops@example.com>\nWe ship 24/7 -- <3"
        row = self.make(text=original)
        self.backfill()
        row.refresh_from_db()

        self.assertEqual(SignatureKind.TEXT, row.kind)
        self.assertEqual(original, row.text)
        self.assertEqual("", row.html)

    def test_a_bare_fragment_is_not_assumed_to_be_html(self):
        """
        `<b>Riz</b>` in the text field is ambiguous — it could be somebody
        writing about markup. Without a document wrapper it stays text, and the
        owner converts it in Settings if they meant otherwise.
        """
        row = self.make(text="<b>Riz</b>")
        self.backfill()
        row.refresh_from_db()

        self.assertEqual(SignatureKind.TEXT, row.kind)
        self.assertEqual("<b>Riz</b>", row.text)

    def test_ordinary_plain_text_is_untouched(self):
        row = self.make(text="Rizwan Sammo\nChief Business Officer")
        self.backfill()
        row.refresh_from_db()

        self.assertEqual(SignatureKind.TEXT, row.kind)
        self.assertEqual("Rizwan Sammo\nChief Business Officer", row.text)

    def test_an_empty_signature_survives(self):
        row = self.make(text="", html="")
        self.backfill()
        row.refresh_from_db()
        self.assertEqual(SignatureKind.TEXT, row.kind)

    # ── a row an API client already populated correctly ─────────────────────

    def test_an_existing_html_field_is_trusted_over_a_guess(self):
        row = self.make(html="<p>Set by an API client</p>", text="")
        self.backfill()
        row.refresh_from_db()

        self.assertEqual(SignatureKind.HTML, row.kind)
        self.assertEqual("<p>Set by an API client</p>", row.html)
        self.assertEqual("Set by an API client", row.text)

    def test_running_it_twice_changes_nothing_further(self):
        """A migration that is not idempotent is a migration nobody can rerun."""
        row = self.make(text=PRODUCTION_SHAPE)
        self.backfill()
        row.refresh_from_db()
        first_html, first_text = row.html, row.text

        self.backfill()
        row.refresh_from_db()
        self.assertEqual(first_html, row.html)
        self.assertEqual(first_text, row.text)


class FrozenMigrationTest(TestCase):
    """
    The migration must not depend on current application code.

    An earlier version imported `sanitize_signature` and `html_to_text` from
    `apps.postbox.mime`. Tightening the allow-list later would then have
    silently changed what this migration produces, and renaming either function
    would have broken it outright — on a rebuild from zero, which is exactly
    when a migration has to work.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import importlib

        cls.module = importlib.import_module(
            "apps.postbox.migrations.0004_signature_kind_backfill"
        )
        cls.source = pathlib.Path(cls.module.__file__).read_text(encoding="utf-8")

    def test_it_imports_nothing_from_the_postbox_app(self):
        for forbidden in (
            "from apps.postbox",
            "import apps.postbox",
            "from .mime",
            "from ..mime",
        ):
            self.assertNotIn(forbidden, self.source, f"{forbidden} reintroduced")

    def test_the_allow_lists_are_literals_in_the_file(self):
        """Frozen, not referenced. A reference is a live dependency."""
        self.assertIn("_TAGS = {", self.source)
        self.assertIn("_ATTRIBUTES = {", self.source)
        self.assertIn("_URL_SCHEMES = {", self.source)
        self.assertIn("_CSS = {", self.source)
        self.assertNotIn("ALLOWED_TAGS", self.source)

    def test_the_frozen_sanitiser_still_removes_the_dangerous_things(self):
        """
        A frozen copy is only safe if it is a copy of something safe. This is
        the snapshot's own security test, independent of mime.py's.
        """
        sanitize = self.module._sanitize

        cleaned = sanitize("<div>Hi<script>alert(1)</script></div>")
        self.assertNotIn("script", cleaned)
        self.assertNotIn("alert(1)", cleaned)

        self.assertNotIn("onclick", sanitize('<div onclick="x()">Hi</div>'))
        self.assertNotIn("javascript:", sanitize('<a href="javascript:x()">c</a>'))
        self.assertNotIn("<iframe", sanitize('<iframe src="https://e.example"></iframe>'))
        self.assertNotIn("position", sanitize('<div style="position:fixed">x</div>'))

    def test_the_frozen_sanitiser_strips_the_document_wrapper(self):
        cleaned = self.module._sanitize(PRODUCTION_SHAPE)
        for marker in ("<!doctype", "<html", "<head", "<title", "<style"):
            self.assertNotIn(marker, cleaned.lower())
        self.assertNotIn("Rizwan Signature", cleaned)
        self.assertIn("Rizwan Sammo", cleaned)

    def test_the_frozen_sanitiser_keeps_safe_signature_markup(self):
        cleaned = self.module._sanitize(PRODUCTION_SHAPE)
        for marker in ("<table", "<td", "<strong", "href="):
            self.assertIn(marker, cleaned)

    def test_it_refuses_rather_than_storing_uncleaned_html(self):
        """
        Without nh3 the migration must NOT fall back to storing the raw value:
        the row keeps its original content as plain text and the owner converts
        it deliberately. Silently storing unsanitised HTML would be worse than
        not migrating.
        """
        from unittest import mock

        with mock.patch.object(self.module, "nh3", None):
            self.assertEqual("", self.module._sanitize(PRODUCTION_SHAPE))


class ReverseHonestyTest(TestCase):
    """The reverse is best-effort, and says so."""

    @classmethod
    def setUpTestData(cls):
        owner = make_user("owner@acme.example")
        cls.tenant = make_tenant(owner)
        cls.domain = make_domain(cls.tenant, "acme.example")
        cls.mailbox = make_mailbox(cls.tenant, cls.domain, local_part="alice")

    def setUp(self):
        import importlib

        module = importlib.import_module(
            "apps.postbox.migrations.0004_signature_kind_backfill"
        )
        self.classify = module.classify
        self.unclassify = module.unclassify

    def test_reverse_restores_the_shape_but_not_the_original_bytes(self):
        """
        The claim being pinned. Sanitising is lossy and nothing kept a copy of
        the source, so a reverse CANNOT be byte-for-byte — and the docstring
        must not pretend otherwise.
        """
        row = MailSignature.objects.create(
            mailbox=self.mailbox, name="Sig", text=PRODUCTION_SHAPE
        )

        self.classify(_Apps(), None)
        self.unclassify(_Apps(), None)
        row.refresh_from_db()

        # Shape restored: markup back in the legacy text column, kind reset.
        self.assertEqual(SignatureKind.TEXT, row.kind)
        self.assertEqual("", row.html)
        self.assertIn("Rizwan Sammo", row.text)

        # Content NOT restored, and that is the documented behaviour.
        self.assertNotEqual(PRODUCTION_SHAPE, row.text)
        self.assertNotIn("<!doctype", row.text.lower())
        self.assertNotIn("Rizwan Signature", row.text)

    def test_the_docstring_does_not_claim_exact_reversal(self):
        import importlib

        module = importlib.import_module(
            "apps.postbox.migrations.0004_signature_kind_backfill"
        )
        doc = (module.__doc__ or "").lower()
        self.assertIn("best-effort", doc)
        self.assertIn("does not restore the original bytes", doc)

    def test_a_plain_text_row_is_untouched_by_the_reverse(self):
        row = MailSignature.objects.create(
            mailbox=self.mailbox, name="Plain", text="Riz\nCBO"
        )
        self.classify(_Apps(), None)
        self.unclassify(_Apps(), None)
        row.refresh_from_db()

        self.assertEqual("Riz\nCBO", row.text)
        self.assertEqual(SignatureKind.TEXT, row.kind)
