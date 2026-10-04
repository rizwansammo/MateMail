"""PostBox color UI contract: both create dialogs and message badges share colors."""
from pathlib import Path

from django.test import SimpleTestCase


FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def read(*parts):
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


class PostBoxColorUiTest(SimpleTestCase):
    def test_independent_folder_and_label_create_and_edit_palettes(self):
        source = read("app", "postbox", "(app)", "layout.tsx")
        self.assertIn('onCreate("folder")', source)
        self.assertIn('onCreate("label")', source)
        self.assertIn("ORGANIZE_COLORS.map", source)
        self.assertIn('type="color" aria-label="Pick a custom color"', source)
        self.assertIn('onCreate("folder", folder.name, undefined, folder.color, true)', source)
        self.assertIn('onCreate("label", label.name, label.id, label.color, true)', source)
        self.assertIn("await postbox.colorFolder(editor.original, entryColor)", source)
        self.assertIn("await postbox.colorLabel(editor.id, entryColor)", source)

    def test_colored_sidebar_icons_and_message_badges(self):
        layout = read("app", "postbox", "(app)", "layout.tsx")
        page = read("app", "postbox", "(app)", "page.tsx")
        thread = read("components", "postbox", "conversation-reader.tsx")
        css = read("app", "globals.css")
        self.assertIn("folder.color || FOLDER_COLOR_DEFAULT", layout)
        self.assertIn("label.color || LABEL_COLOR_DEFAULT", layout)
        self.assertIn('"--pb-label-color": palette?.find', page)
        self.assertIn('color: item.color || "#9333ea"', thread)
        self.assertIn("var(--pb-label-color, #9333ea)", css)

    def test_both_brands_share_the_same_color_aware_api(self):
        api = read("lib", "postbox-api.ts")
        self.assertIn("colorFolder:", api)
        self.assertIn("colorLabel:", api)
        self.assertIn("color: string | null;", api)

    def test_create_and_save_button_uses_the_theme_primary_not_pale_accent(self):
        css = read("app", "globals.css")
        layout = read("app", "postbox", "(app)", "layout.tsx")
        active = css.split(".pb-organize-dialog-actions button[type=submit] {", 1)[1].split("}", 1)[0]
        disabled = css.split(".pb-organize-dialog-actions button[type=submit]:disabled {", 1)[1].split("}", 1)[0]

        # Both brand/theme variants supply primary foreground/background tokens.
        self.assertIn("background:var(--pb-primary)", active)
        self.assertIn("color:var(--pb-primary-fg)", active)
        self.assertNotIn("background:var(--pb-accent)", active)
        self.assertIn("background:var(--pb-surface-2)", disabled)
        self.assertIn("color:var(--pb-muted)", disabled)
        self.assertIn('disabled={saving || !entryName.trim()}', layout)
