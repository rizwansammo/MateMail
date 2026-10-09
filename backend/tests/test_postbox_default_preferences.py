"""Default PostBox preferences and DNS layout source contracts."""
from pathlib import Path

from django.test import SimpleTestCase

from apps.postbox.models import PostBoxPreference

ROOT = Path(__file__).resolve().parents[2]


class PostBoxDefaultPreferenceTest(SimpleTestCase):
    def test_model_defaults(self):
        for field, value in (
            ("theme", "light"),
            ("density", "extra_compact"),
            ("reading_pane", "off"),
        ):
            self.assertEqual(PostBoxPreference._meta.get_field(field).get_default(), value)

    def test_frontend_fallback_matches_database(self):
        source = (ROOT / "frontend/contexts/postbox-context.tsx").read_text()
        for value in ('theme: "light"', 'density: "extra_compact"', 'reading_pane: "off"'):
            self.assertIn(value, source)
        self.assertNotIn('applyTheme(data.preferences?.theme ?? "system")', source)

    def test_migration_only_changes_defaults(self):
        source = (ROOT / "backend/apps/postbox/migrations/"
                  "0016_default_light_full_extra_compact.py").read_text()
        self.assertEqual(source.count("migrations.AlterField("), 3)
        self.assertNotIn("RunPython", source)
        self.assertNotIn("RunSQL", source)

    def test_txt_label_has_one_line_layout_and_copy_button(self):
        ownership = (ROOT / "frontend/components/domain-ownership.tsx").read_text()
        css = (ROOT / "frontend/app/app/portal-premium.css").read_text()
        self.assertIn("portal-dns-type-value", ownership)
        self.assertIn("grid-template-columns: 110px", css)
        self.assertIn("min-width: max-content", css)
        self.assertIn("white-space: nowrap", css)

    def test_custom_dns_name_copy_does_not_use_full_hostname(self):
        source = (ROOT / "frontend/components/workspace/custom-hostnames-settings.tsx").read_text()
        self.assertIn("Copy relative DNS host", source)
        self.assertIn("row.cname_zone", source)
        self.assertNotIn('PortalCopyButton value={row.hostname}', source)
