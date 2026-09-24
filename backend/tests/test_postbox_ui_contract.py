"""
PostBox UI contracts that are cheap to break and expensive to notice.

WHY SOURCE-TEXT TESTS
    This repository has no React test harness — no jest, no vitest, no
    testing-library — and adding one for a UX polish phase would be a larger
    change than the phase itself. So these follow the pattern already used by
    `test_platform_frontend_contract.py`: assert on the source of the files,
    alongside `tsc`, ESLint and a production build.

    They are deliberately narrow. Each one pins a specific defect that shipped,
    not the shape of the markup around it, so ordinary UI work does not trip
    them.

THE CSS BUG THEY MOSTLY GUARD
    Tailwind v4 (`@import "tailwindcss"`) emits utilities inside
    `@layer utilities`. `globals.css` declares `.pb-btn { display: inline-flex }`
    OUTSIDE any layer, and unlayered CSS beats every cascade layer regardless
    of specificity or source order.

    So `className="pb-btn md:hidden"` does not hide anything. A close button
    meant for the mobile drawer rendered on desktop, and so did the reader's
    "back to list" arrow. The fix is a wrapper element that carries the
    utility, because the wrapper has no competing display rule.
"""
from pathlib import Path

from django.test import SimpleTestCase

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def read(*parts: str) -> str:
    return (FRONTEND.joinpath(*parts)).read_text(encoding="utf-8")


def code_only(source: str) -> str:
    """
    The source with comments removed.

    Negative assertions must not match prose. `assertNotIn('aria-modal')`
    was failing on the comment explaining why there is no `aria-modal`, so
    documenting the decision broke the test guarding it — and a guard that
    punishes explaining yourself is a guard somebody eventually deletes.
    """
    import re

    without_block = re.sub(r"(?s)/\*.*?\*/", "", source)
    return re.sub(r"(?m)^\s*//.*$", "", without_block)


class ResponsiveDisplayTest(SimpleTestCase):
    """A display utility on a `.pb-btn` is inert. Nothing may rely on one."""

    FILES = [
        ("app", "postbox", "(app)", "layout.tsx"),
        ("app", "postbox", "(app)", "page.tsx"),
        ("components", "postbox", "compose.tsx"),
    ]

    def test_no_pb_btn_carries_a_display_utility(self):
        """
        The regression, stated as a rule. `pb-btn` and `md:hidden` on the same
        element silently does nothing — the button stays visible.
        """
        import re

        pattern = re.compile(
            r'className="[^"]*\bpb-btn\b[^"]*\b'
            r"(?:md|sm|lg|xl):(?:hidden|block|flex|inline-flex)\b[^\"]*\""
        )
        for parts in self.FILES:
            source = read(*parts)
            found = pattern.findall(source)
            self.assertEqual(
                [], found,
                f"{parts[-1]}: a display utility on .pb-btn does not apply — "
                f"wrap the button instead: {found}",
            )

    def test_the_sidebar_close_button_is_wrapped(self):
        source = read("app", "postbox", "(app)", "layout.tsx")
        self.assertIn('<div className="ml-auto md:hidden">', source)
        self.assertIn('aria-label="Close folders"', source)

    def test_the_reader_back_button_is_wrapped(self):
        source = read("app", "postbox", "(app)", "page.tsx")
        self.assertIn('<div className="md:hidden">', source)
        self.assertIn('aria-label="Back to list"', source)


class FolderPresentationTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source = read("app", "postbox", "(app)", "layout.tsx")

    def test_the_standard_folder_order(self):
        """
        Reading order first, then what you sent, then storage, then the bins.
        Starred and Scheduled used to be appended after the loop, which put
        them below Trash.
        """
        expected = [
            "inbox", "starred", "scheduled", "sent",
            "drafts", "archive", "junk", "trash",
        ]
        block = self.source.split("const PINNED", 1)[1].split("];", 1)[0]

        seen = []
        for role in expected:
            marker = f'role: "{role}"'
            self.assertIn(marker, block, f"{role} missing from the pinned list")
            seen.append((block.index(marker), role))

        self.assertEqual(expected, [role for _, role in sorted(seen)])

    def test_each_standard_folder_has_its_own_icon(self):
        block = self.source.split("const PINNED", 1)[1].split("];", 1)[0]
        for role, icon in (
            ("inbox", "Inbox"),
            ("starred", "Star"),
            ("scheduled", "Clock"),
            ("sent", "Send"),
            ("drafts", "FileText"),
            ("archive", "Archive"),
            ("junk", "ShieldAlert"),
            ("trash", "Trash2"),
        ):
            line = next(
                (l for l in block.splitlines() if f'role: "{role}"' in l), ""
            )
            self.assertIn(f"icon: {icon}", line, f"{role} should use {icon}")

    def test_custom_folders_do_not_use_the_archive_icon(self):
        """
        The root cause of the identical icons: every custom folder was drawn
        with `Archive`, and the standard folders were falling into that branch.
        """
        self.assertIn("icon={FolderIcon}", self.source)
        self.assertNotIn("icon={Archive}", self.source)

    def test_a_standard_folder_cannot_also_appear_under_folders(self):
        """
        `custom` is now everything the pinned list did not claim, by ROLE.
        Filtering on `!f.role` let a role-less Sent folder appear twice once
        the name fallback started assigning roles.
        """
        self.assertIn("PINNED_ROLES", self.source)
        self.assertIn("folders.filter((f) => !PINNED_ROLES.has(f.role))", self.source)


class ComposeContractTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source = read("components", "postbox", "compose.tsx")

    def test_compact_is_the_default_desktop_state(self):
        """Compose opens as the small bottom-right window, never large."""
        self.assertIn("const [expanded, setExpanded] = useState(false);", self.source)

    def test_expanding_is_a_class_swap_not_a_different_tree(self):
        """
        State survival depends on this. Two different renders would remount the
        form and drop recipients, body, attachments, the signature choice, the
        schedule time and the draft UID.
        """
        self.assertIn("sm:h-[calc(100vh_-_3rem)] sm:w-[calc(100vw_-_3rem)]", self.source)
        self.assertIn("sm:h-[min(42rem,90vh)] sm:w-[min(40rem,95vw)]", self.source)
        # One conditional expression choosing between them, not two branches.
        self.assertIn("expanded\n            ?", self.source)

    def test_the_expand_control_is_labelled_both_ways(self):
        self.assertIn('aria-label={expanded ? "Restore compose" : "Expand compose"}', self.source)
        self.assertIn("aria-pressed={expanded}", self.source)
        self.assertIn("Maximize2", self.source)
        self.assertIn("Minimize2", self.source)

    def test_the_expand_control_is_hidden_on_mobile(self):
        """Nothing to expand into when Compose already fills the screen."""
        self.assertIn('<div className="hidden sm:block">', self.source)

    def test_cc_bcc_toggles_both_ways(self):
        self.assertIn("setShowCopies((current) => !current)", self.source)
        self.assertIn("aria-expanded={showCopies}", self.source)
        self.assertIn('aria-controls="pb-copies"', self.source)

    def test_collapsing_cc_bcc_does_not_clear_the_values(self):
        """
        `showCopies` controls VISIBILITY only. If the collapse branch reset
        `cc` or `bcc`, a recipient typed and then hidden would be dropped.
        """
        self.assertNotIn('setCc("")', self.source)
        self.assertNotIn('setBcc("")', self.source)

    def test_cc_and_bcc_are_always_in_the_payload(self):
        payload = self.source.split("const payload = useCallback(", 1)[1][:600]
        self.assertIn("cc: splitAddresses(cc)", payload)
        self.assertIn("bcc: splitAddresses(bcc)", payload)

    def test_it_opens_expanded_when_copies_already_exist(self):
        self.assertIn(
            "Boolean(initial.cc?.length || initial.bcc?.length)", self.source
        )

    def test_bcc_is_loaded_into_state_not_only_used_for_visibility(self):
        """
        `initial.bcc` was read only to decide whether the row was shown, while
        the state itself was hard-coded to "" — so a Bcc that arrived would
        open the field and then display nothing.
        """
        self.assertIn(
            'const [bcc, setBcc] = useState((initial.bcc ?? []).join(", "));',
            self.source,
        )
        self.assertNotIn('const [bcc, setBcc] = useState("");', self.source)

    def test_all_three_recipient_fields_initialise_the_same_way(self):
        for field in ("to", "cc", "bcc"):
            self.assertIn(
                f'const [{field}, set{field.capitalize()}] = '
                f'useState((initial.{field} ?? []).join(", "));',
                self.source,
                f"{field} should load from initial like the others",
            )

    def test_the_dialog_does_not_claim_a_modality_it_does_not_enforce(self):
        """
        From `sm` up the backdrop is transparent and click-through, so the page
        behind is interactive. `aria-modal="true"` would tell a screen reader
        to hide the rest of the document from its user.
        """
        code = code_only(self.source)
        self.assertNotIn('aria-modal="true"', code)
        self.assertIn('role="dialog"', code)
        self.assertIn('aria-label="Compose message"', code)

    def test_the_backdrop_is_styled_by_class_not_by_aria_label(self):
        self.assertIn("pb-compose-backdrop", self.source)
        css = read("app", "globals.css")
        self.assertIn(".pb-compose-backdrop", css)
        self.assertNotIn('[aria-label="Compose message"]', css)

    def test_the_expanded_size_uses_valid_calc_syntax(self):
        """
        `calc(100vh-3rem)` is invalid CSS — calc needs spaces around the
        operator — and a Tailwind arbitrary value cannot contain spaces, so it
        must be written with underscores. Written wrongly the class is dropped
        at build time and expanding resizes nothing, silently.
        """
        code = code_only(self.source)
        self.assertIn("sm:h-[calc(100vh_-_3rem)]", code)
        self.assertIn("sm:w-[calc(100vw_-_3rem)]", code)
        self.assertNotIn("calc(100vh-3rem)", code)
        self.assertNotIn("calc(100vw-3rem)", code)

    def test_the_signature_is_still_previewed_and_not_inlined(self):
        """
        The composer shows the signature but must never put it in `body`: the
        backend appends it, so an inlined copy would send it twice.
        """
        self.assertIn("selectedSignature", self.source)
        self.assertIn("pb-sig-canvas", self.source)
        self.assertNotIn("setBody(body + signature", self.source)

    def test_the_footer_actions_are_all_still_there(self):
        for label in ("Send", "Attach", "Discard"):
            self.assertIn(label, self.source)
        self.assertIn("scheduleAt", self.source)


class NetaMateBrandTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.source = read("components", "netamate-brand.tsx")

    def test_postbox_uses_a_two_line_wordmark(self):
        postbox = self.source.split('surface === "PostBox"', 1)[1].split(") : (", 1)[0]
        # Two lines, in order, inside the PostBox branch.
        self.assertIn("NetaMate", postbox)
        self.assertIn("PostBox", postbox)
        self.assertLess(postbox.index("NetaMate"), postbox.index("PostBox"))

        # Both carry the brand face; the second is the smaller sub-line.
        self.assertIn("nm-wordmark-stack", postbox)
        self.assertIn("nm-wordmark-sub", postbox)
        self.assertEqual(2, postbox.count("<p"), "expected exactly two lines")

    def test_postbox_does_not_use_the_category_caption(self):
        """
        `nm-surface-label` is a spaced uppercase caption. On PostBox it made
        the product name read as a tag stapled under a different product.
        """
        postbox = self.source.split('surface === "PostBox"', 1)[1].split(") : (", 1)[0]
        self.assertNotIn("nm-surface-label", postbox)

    def test_mailadmin_branding_is_unchanged(self):
        mailadmin = self.source.split(") : (", 1)[1]
        self.assertIn("NetaMate Email", mailadmin)
        self.assertIn("nm-surface-label", mailadmin)
        self.assertIn("{surface}", mailadmin)


class NetaMateLayoutTest(SimpleTestCase):
    def test_the_narrow_rail_is_netamate_only(self):
        """
        A NetaMate width decision must not silently reshape MateMail's own
        PostBox, which keeps `w-60`.
        """
        layout = read("app", "postbox", "(app)", "layout.tsx")
        self.assertIn('IS_NETAMATE_EMAIL ? "nm-rail " : ""', layout)
        self.assertIn("w-60", layout)

        css = read("app", "globals.css")
        self.assertIn(".nm-rail", css)
        self.assertIn("width: 13.5rem", css)

    def test_the_message_list_widens_with_the_viewport(self):
        page = read("app", "postbox", "(app)", "page.tsx")
        self.assertIn("md:w-[24rem] lg:w-[27rem] xl:w-[28rem]", page)
        self.assertNotIn("md:w-[22rem]", page)

    def test_both_reading_pane_positions_still_exist(self):
        page = read("app", "postbox", "(app)", "page.tsx")
        self.assertIn("paneRight", page)
        self.assertIn("flex-1 border-b", page)
