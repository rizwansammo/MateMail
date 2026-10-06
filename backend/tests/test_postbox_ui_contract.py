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

    def test_the_premium_sidebar_uses_its_overlay_close_control(self):
        source = read("app", "postbox", "(app)", "layout.tsx")
        self.assertIn('className="pb-premium-overlay"', source)
        self.assertIn('aria-label="Close folders"', source)
        self.assertNotIn('className="pb-btn pb-btn-plain md:hidden"', source)

    def test_the_reader_back_button_is_available_on_desktop_and_mobile(self):
        source = read("app", "postbox", "(app)", "page.tsx")
        reader = source.split('className="pb-premium-reader-toolbar"', 1)[1][:850]
        # The reader now needs Back at every viewport, not a mobile-only
        # wrapper: Single Message must be navigable after opening a thread.
        self.assertNotIn('<div className="md:hidden">', reader)
        self.assertIn('aria-label="Back to mailbox"', reader)
        self.assertIn('<span>Back</span>', reader)


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
        custom_block = self.source.split("custom.map((folder)", 1)[1].split("</Link>", 1)[0]
        self.assertIn("<FolderIcon", custom_block)
        self.assertNotIn("<Archive", custom_block)

    def test_a_standard_folder_cannot_also_appear_under_folders(self):
        """
        `custom` is now everything the pinned list did not claim, by ROLE.
        Filtering on `!f.role` let a role-less Sent folder appear twice once
        the name fallback started assigning roles.
        """
        self.assertIn("PINNED_ROLES", self.source)
        self.assertIn(
            "folders.filter((folder) => !PINNED_ROLES.has(folder.role))",
            self.source,
        )


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
        self.assertIn('aria-controls={fieldId("copies")}', self.source)
        self.assertIn('id={fieldId("copies")}', self.source)
        self.assertIn('inline ? "pb-thread-" : "pb-"', self.source)

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
        self.assertIn('role={inline ? "region" : "dialog"}', code)
        self.assertIn('aria-label={inline ? "Inline reply editor" : "Compose message"}', code)

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


class DedicatedBrandParityTest(SimpleTestCase):
    """
    Dedicated NetaMate hostnames are access boundaries, not a separate visual
    product. Branding must stay MateMail/PostBox so a dedicated tenant cannot
    silently drift into its own frontend identity again.
    """

    def test_authenticated_postbox_has_no_dedicated_brand_branch(self):
        layout = read("app", "postbox", "(app)", "layout.tsx")
        self.assertIn('<span className="pb-premium-wordmark">PostBox</span>', layout)
        self.assertNotIn("NetaMateBrand", layout)
        self.assertNotIn("IS_NETAMATE_EMAIL", layout)
        self.assertNotIn("nm-postbox", layout)

    def test_postbox_login_is_shared_by_every_host(self):
        login = read("app", "postbox", "login", "page.tsx")
        self.assertIn('className="pb pb-premium-login"', login)
        self.assertIn('<span className="pb-premium-wordmark">PostBox</span>', login)
        self.assertNotIn("NetaMateAuthShell", login)
        self.assertNotIn("IS_NETAMATE_EMAIL", login)

    def test_mailadmin_uses_the_standard_matemail_mark_and_theme(self):
        layout = read("app", "app", "layout.tsx")
        self.assertIn('<BrandMark size={31} className="portal-brand-mark" preload />', layout)
        self.assertIn(">MateMail Hub</span>", layout)
        self.assertNotIn("NetaMateBrand", layout)
        self.assertNotIn("nm-mailadmin", layout)

    def test_dedicated_tab_branding_is_matemail(self):
        root = read("app", "layout.tsx")
        postbox = read("app", "postbox", "layout.tsx")
        self.assertIn('"MateMail · Organization Hub"', root)
        self.assertNotIn("NetaMate Email", root)
        self.assertNotIn("NETAMATE_LOGO_SRC", root)
        self.assertIn('url: "/postbox/favicon?v=3"', postbox)
        self.assertNotIn("NetaMate Email", postbox)
        self.assertNotIn("NETAMATE_LOGO_SRC", postbox)


class NetaMateLayoutTest(SimpleTestCase):
    def test_netamate_uses_the_same_premium_shell(self):
        """
        Dedicated hostnames use the exact same authenticated PostBox shell.
        Only tenant access policy and host routing may differ.
        """
        layout = read("app", "postbox", "(app)", "layout.tsx")
        self.assertIn("<PremiumPostBoxShell", layout)
        self.assertNotIn("if (!IS_NETAMATE_EMAIL)", layout)
        self.assertNotIn("nm-rail", layout)

    def test_the_message_list_widens_with_the_viewport(self):
        page = read("app", "postbox", "(app)", "page.tsx")
        self.assertIn("md:w-[24rem] lg:w-[27rem] xl:w-[28rem]", page)
        self.assertNotIn("md:w-[22rem]", page)

    def test_both_reading_pane_positions_still_exist(self):
        page = read("app", "postbox", "(app)", "page.tsx")
        self.assertIn("paneRight", page)
        self.assertIn("flex-1 border-b", page)

class ScrollContainmentTest(SimpleTestCase):
    """The shared premium PostBox shell keeps scrolling inside the app."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.layout = read("app", "postbox", "(app)", "layout.tsx")
        cls.page = read("app", "postbox", "(app)", "page.tsx")
        cls.root = read("app", "layout.tsx")
        cls.css = read("app", "globals.css")

    def test_the_shared_shell_is_viewport_bounded(self):
        rule = self.css.split(".pb-premium-shell {", 1)[1].split("}", 1)[0]
        self.assertIn("height:100dvh", rule)
        self.assertIn("min-height:0", rule)
        self.assertIn("overflow:hidden", rule)

    def test_the_fix_is_not_applied_to_the_shared_body(self):
        body = code_only(self.root).split("<body", 1)[1].split(">", 1)[0]
        self.assertNotIn("overflow-hidden", body)
        self.assertIn("min-h-full", body)

    def test_the_premium_sidebar_navigation_scrolls_internally(self):
        rule = self.css.split(".pb-premium-nav {", 1)[1].split("}", 1)[0]
        self.assertIn("min-height:0", rule)
        self.assertIn("overflow-y:auto", rule)

    def test_the_mailbox_root_fills_its_boundary(self):
        self.assertIn('className="flex h-full min-h-0 flex-col"', self.page)

    def test_the_list_reader_split_is_constrained_in_both_orientations(self):
        split = self.page.split("flex min-h-0 flex-1 overflow-hidden", 1)
        self.assertEqual(2, len(split), "the split boundary lost its containment")
        self.assertIn('paneRight ? "flex-row" : "flex-col"', split[1][:200])

    def test_the_message_list_is_its_own_scroll_region(self):
        self.assertIn("pb-scroll min-h-0", self.page)

    def test_contacts_and_settings_scroll_inside_the_content_area(self):
        for page in ("contacts", "settings"):
            source = read("app", "postbox", "(app)", page, "page.tsx")
            self.assertIn('className="pb-scroll h-full"', source)

    def test_the_scroll_utility_still_contains_its_overscroll(self):
        rule = self.css.split(".pb-scroll {", 1)[1].split("}", 1)[0]
        self.assertIn("overflow-y: auto", rule)
        self.assertIn("overscroll-behavior: contain", rule)


class PremiumReleaseIntegrationTest(SimpleTestCase):
    """Final integration contracts for the premium PostBox release."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.layout = read("app", "postbox", "(app)", "layout.tsx")
        cls.page = read("app", "postbox", "(app)", "page.tsx")
        cls.settings_route = read(
            "app", "postbox", "(app)", "settings", "page.tsx"
        )
        cls.contacts_route = read(
            "app", "postbox", "(app)", "contacts", "page.tsx"
        )
        cls.settings = read("components", "postbox", "premium-settings.tsx")
        cls.contacts = read("components", "postbox", "premium-contacts.tsx")

    def test_feature_routes_are_brand_agnostic(self):
        """Settings and Contacts must use the same premium components everywhere."""
        self.assertIn("<PremiumSettings />", self.settings_route)
        self.assertIn("<PremiumContacts />", self.contacts_route)
        self.assertNotIn("IS_NETAMATE_EMAIL", self.settings_route)
        self.assertNotIn("IS_NETAMATE_EMAIL", self.contacts_route)
        self.assertNotIn("LegacySettingsPage", self.settings_route)
        self.assertNotIn("LegacyContactsPage", self.contacts_route)

    def test_contacts_send_message_prefills_the_recipient(self):
        self.assertIn("compose=new&to=", self.contacts)
        self.assertIn('params.get("to")', self.page)
        self.assertIn("{ to: [composeRecipient] }", self.page)

    def test_closing_compose_preserves_mailbox_context(self):
        self.assertIn('next.delete("compose")', self.page)
        self.assertIn('next.delete("to")', self.page)
        self.assertIn("new URLSearchParams(params.toString())", self.page)
        self.assertNotIn(
            'router.replace(`/postbox?folder=${encodeURIComponent(folder)}`)',
            self.page,
        )

    def test_sidebar_starred_is_a_real_cross_folder_view(self):
        self.assertIn(
            'href: "/postbox?folder=INBOX&starred=true&scope=all"',
            self.layout,
        )
        self.assertIn(
            'filteredStarredOnly && searchScope !== "folder"',
            self.page,
        )
        self.assertIn("const isCrossFolderView", self.page)
        self.assertIn("!isCrossFolderView && rows.length > 0", self.page)

    def test_keyboard_hints_have_working_handlers(self):
        self.assertIn('event.key.toLowerCase() === "c"', self.layout)
        self.assertIn('router.push("/postbox?compose=new")', self.layout)
        self.assertIn('event.key !== "/"', self.layout)
        self.assertIn("inputRef.current?.focus()", self.layout)

    def test_settings_sections_are_deep_linkable(self):
        self.assertIn('params.get("section")', self.settings)
        self.assertIn('/postbox/settings?section=', self.settings)
        self.assertIn('/postbox/settings?section=account', self.layout)
        self.assertIn('/postbox/settings?section=appearance', self.layout)

    def test_contacts_search_is_debounced(self):
        self.assertIn(
            "setTimeout(() => setSearch(query.trim()), 250)", self.contacts
        )
        self.assertIn(
            "() => postbox.contacts(search || undefined)", self.contacts
        )

    def test_filtered_reader_closes_when_the_message_leaves_the_filter(self):
        self.assertIn('(unreadOnly && action === "read")', self.page)
        self.assertIn(
            '(filteredStarredOnly && action === "unstar")', self.page
        )


class NetaMateFeatureParityRegressionTest(SimpleTestCase):
    """
    NetaMate is a branding variant, not a product fork.
    Feature/layout code must never branch on the brand flag.
    """

    def test_mailbox_and_compose_have_no_brand_feature_gates(self):
        for path in (
            ("app", "postbox", "(app)", "page.tsx"),
            ("components", "postbox", "compose.tsx"),
        ):
            source = read(*path)
            self.assertNotIn(
                "IS_NETAMATE_EMAIL",
                source,
                f"{'/'.join(path)} must stay brand-agnostic",
            )
            self.assertNotIn("LegacyReader", source)

    def test_settings_and_contacts_never_fall_back_to_legacy_components(self):
        settings = read("app", "postbox", "(app)", "settings", "page.tsx")
        contacts = read("app", "postbox", "(app)", "contacts", "page.tsx")
        self.assertNotIn("LegacySettingsPage", settings)
        self.assertNotIn("LegacyContactsPage", contacts)


class PremiumBrandingRegressionTest(SimpleTestCase):
    def test_premium_postbox_brand_is_self_contained(self):
        layout = read("app", "postbox", "(app)", "layout.tsx")
        css = read("app", "globals.css")
        self.assertIn('className="pb-premium-brand-mark"', layout)
        self.assertIn('<span className="pb-premium-wordmark">PostBox</span>', layout)
        self.assertNotIn('pb-premium-brand-dot', layout)
        self.assertNotIn('>MAIL<', layout)
        self.assertIn('.pb-premium-brand-mark {', css)


class PostBoxTabBrandingRegressionTest(SimpleTestCase):
    def test_standard_postbox_owns_its_tab_title_and_favicon(self):
        layout = read("app", "postbox", "layout.tsx")
        self.assertIn('default: "PostBox"', layout)
        self.assertIn('url: "/postbox/favicon?v=3"', layout)
        self.assertNotIn('default: "MateMail PostBox"', layout)


class PostBoxPublicAssetIndependenceTest(SimpleTestCase):
    def test_login_logo_is_inline_and_favicon_uses_app_route(self):
        login = read("app", "postbox", "login", "page.tsx")
        layout = read("app", "postbox", "layout.tsx")
        self.assertIn('className="pb-premium-login-mark"', login)
        self.assertNotIn('src="/postbox-mark.svg"', login)
        self.assertIn('url: "/postbox/favicon?v=3"', layout)
        self.assertNotIn('url: "/postbox-mark.svg"', layout)


class PostBoxComposeAndImageRenderingRegressionTest(SimpleTestCase):
    def test_compose_label_column_cannot_overlap_subject_input(self):
        css = read("app", "globals.css")
        self.assertIn(
            ".pb-premium-compose-shell .pb-compose-fields label {\n  width:64px;\n  flex:0 0 64px;",
            css,
        )

    def test_reader_resolves_safe_cid_images_through_preview_endpoint(self):
        page = read("app", "postbox", "(app)", "page.tsx")
        conversation = read("components", "postbox", "conversation-reader.tsx")
        resolver = read("lib", "postbox-inline-images.ts")
        self.assertIn("export function resolveInlineImageReferences", resolver)
        self.assertIn("attachment.content_id", resolver)
        self.assertIn("attachment.previewable", resolver)
        self.assertIn("postbox.attachmentPreviewUrl", resolver)
        self.assertIn("import { resolveInlineImageReferences }", page)
        self.assertIn("import { resolveInlineImageReferences }", conversation)
        self.assertIn("dangerouslySetInnerHTML={{ __html: renderedHtml }}", page)
        self.assertIn("dangerouslySetInnerHTML={{ __html: safeHtml }}", conversation)

    def test_external_image_privacy_controls_remain_visible_when_blocked(self):
        page = read("app", "postbox", "(app)", "page.tsx")
        self.assertIn("detail.remote_images_blocked && !showRemote", page)
        self.assertIn(">Display images</button>", page)
        self.assertIn("Always display images from this sender", page)


class PostBoxReaderNavigationAndHtmlLayoutRegressionTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.page = read("app", "postbox", "(app)", "page.tsx")
        cls.layout = read("app", "postbox", "(app)", "layout.tsx")
        cls.css = read("app", "globals.css")

    def test_html_mail_is_not_forced_into_a_centered_postbox_card(self):
        body = self.css.split(
            ".pb-premium-shell .pb-premium-message-body {", 1
        )[1].split("}", 1)[0]
        html = self.css.split(
            ".pb-premium-shell .pb-premium-html-mail {", 1
        )[1].split("}", 1)[0]

        self.assertIn("width:100%", body)
        self.assertIn("max-width:none", body)
        self.assertIn("width:100%", html)
        self.assertIn("max-width:none", html)
        self.assertIn("margin:0", html)
        self.assertIn("border:0", html)
        self.assertIn("padding:0", html)
        self.assertNotIn("margin:0 auto", html)
        self.assertNotIn("max-width:750px", html)

    def test_brand_and_folder_clicks_explicitly_return_reader_to_list(self):
        self.assertIn('aria-label="PostBox Inbox"', self.layout)
        self.assertIn('href="/postbox?folder=INBOX"', self.layout)
        self.assertIn('"postbox:return-to-list"', self.layout)
        self.assertIn('"postbox:return-to-list"', self.page)

    def test_inline_mime_parts_do_not_show_as_download_attachments(self):
        self.assertIn(
            "detail.attachments.filter((attachment) => !attachment.inline)",
            self.page,
        )
        self.assertIn("visibleAttachments.length", self.page)
        self.assertIn("visibleAttachments.map((attachment)", self.page)

    def test_clickable_brand_keeps_normal_link_styling(self):
        block = self.css.split(".pb-premium-brand {", 1)[1].split("}", 1)[0]
        self.assertIn("text-decoration:none", block)
        self.assertIn("cursor:pointer", block)


class MultiAccountSwitcherContractTest(SimpleTestCase):
    """The account menu must stay token-safe while supporting fast switching."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.layout = read("app", "postbox", "(app)", "layout.tsx")
        cls.login = read("app", "postbox", "login", "page.tsx")
        cls.add_account = read("app", "postbox", "add-account", "page.tsx")
        cls.api = read("lib", "postbox-api.ts")

    def test_profile_menu_exposes_add_and_switch_actions(self):
        self.assertIn('href="/postbox/add-account"', self.layout)
        self.assertIn("postbox.switchAccount(item.session_id)", self.layout)
        self.assertIn("Accounts on this device", self.layout)
        self.assertIn("Add another account", self.layout)

    def test_switching_hard_resets_mailbox_scoped_frontend_state(self):
        self.assertIn('window.location.assign("/postbox?folder=INBOX")', self.layout)
        self.assertIn('window.location.assign("/postbox?folder=INBOX")', self.login)

    def test_saved_account_capabilities_never_use_web_storage(self):
        combined = "\n".join((self.layout, self.login, self.add_account, self.api))
        self.assertNotIn("localStorage", combined)
        self.assertNotIn("sessionStorage", combined)

    def test_add_account_defaults_to_a_remembered_session(self):
        self.assertIn("const [remember, setRemember] = useState(true);", self.add_account)
        self.assertIn("Keep this account available on this device for 30 days", self.add_account)

    def test_sign_in_screen_can_resume_a_saved_account_without_a_password(self):
        self.assertIn(".accounts()", self.login)
        self.assertIn("postbox.switchAccount(item.session_id)", self.login)


class Phase4ThreadSecurityAndAccessibilityContractTest(SimpleTestCase):
    """Guard the integration seams not covered by the Python mail-engine tests."""

    def test_thread_cards_are_keyboard_accessible_and_html_is_sanitised(self):
        thread = read("components", "postbox", "conversation-reader.tsx")
        self.assertIn("aria-expanded={open}", thread)
        self.assertIn('aria-label="Messages in conversation"', thread)
        self.assertIn("resolveInlineImageReferences(detail)", thread)
        self.assertIn("dangerouslySetInnerHTML={{ __html: safeHtml }}", thread)
        self.assertIn('postbox.message(member.folder, member.uid, false, member.uid_validity)', thread)
        self.assertIn('uid_validity: copy.uid_validity', thread)

    def test_inline_reply_cannot_silently_unmount_from_common_navigation(self):
        page = read("app", "postbox", "(app)", "page.tsx")
        thread = read("components", "postbox", "conversation-reader.tsx")
        self.assertIn("threadInlineActive", page)
        self.assertIn('document.addEventListener("click", protectInlineDraft, true)', page)
        self.assertIn("onInlineChange={reportInlineState}", page)
        self.assertIn("Save or close your inline reply", page)
        self.assertIn("guardNavigation(onBack)", thread)
        self.assertIn("guardNavigation(onSingle)", thread)

    def test_raw_and_attachment_urls_carry_folder_generation(self):
        api = read("lib", "postbox-api.ts")
        resolver = read("lib", "postbox-inline-images.ts")
        thread = read("components", "postbox", "conversation-reader.tsx")
        for field in ("rawUrl:", "attachmentUrl:", "attachmentPreviewUrl:"):
            self.assertIn(field, api)
        self.assertIn("qs({ uid_validity: uidValidity })", api)
        self.assertIn("detail.uid_validity", resolver)
        self.assertIn("detail.uid_validity", thread)


class ThreadReadingLayoutRegressionTest(SimpleTestCase):
    """Prevent regressions in the real inline composer / reader CSS cascade."""

    def test_mailbox_bulk_toolbar_only_appears_on_list(self):
        page = read("app", "postbox", "(app)", "page.tsx")
        self.assertIn('{!detail && <div className="pb-premium-mail-toolbar">', page)
        self.assertIn('aria-label="Select all visible messages"', page)
        self.assertIn('aria-label="Mailbox layout"', page)

    def test_single_message_can_return_to_existing_conversation(self):
        page = read("app", "postbox", "(app)", "page.tsx")
        thread = read("components", "postbox", "conversation-reader.tsx")
        self.assertIn('onSingle={() => changeReaderView("single")}', page)
        self.assertIn('onThread={thread && preferences.reader_view === "single" ? () => changeReaderView("thread") : undefined}', page)
        self.assertIn('updatePreferences({ list_view: mode })', page)
        self.assertIn('updatePreferences({ reader_view: mode })', page)
        self.assertIn('thread && preferences.reader_view === "thread"', page)
        self.assertIn('onThread?: () => void;', page)
        self.assertIn('aria-label="Return to email thread"', page)
        self.assertIn('guardNavigation(onSingle)', thread)

    def test_inline_reply_and_message_cards_share_full_width_without_fixed_height(self):
        css = read("app", "globals.css")
        composer = read("components", "postbox", "compose.tsx")
        self.assertIn('width:100%;max-width:none;min-width:0;', css)
        self.assertIn(
            ".pb-thread-scroll .pb-thread-compose-host .pb-premium-compose-shell.pb-thread-inline-panel",
            css,
        )
        self.assertIn("width:100% !important;", css)
        self.assertIn("height:auto !important;", css)
        self.assertIn("max-height:none !important;", css)
        self.assertIn(".pb-thread-compose-host .pb-premium-compose-shell .pb-compose-footer", css)
        self.assertIn("border-top:0 !important;", css)
        self.assertIn("pb-compose-quote-toggle", composer)
        self.assertIn('className={inline ? "pb-thread-compose-host"', composer)


class SidebarThemeRelocationTest(SimpleTestCase):
    """Storage/appearance remain in Settings; compact themes move to avatar card."""

    def test_sidebar_has_no_redundant_storage_or_theme_footer(self):
        layout = read("app", "postbox", "(app)", "layout.tsx")
        css = read("app", "globals.css")
        self.assertNotIn('className="pb-premium-sidebar-footer"', layout)
        self.assertNotIn('className="pb-premium-storage"', layout)
        self.assertNotIn('account?.storage', layout)
        self.assertNotIn('postbox\n      .account()', layout)
        self.assertNotIn(".pb-premium-sidebar-footer {", css)
        self.assertNotIn(".pb-premium-storage {", css)

    def test_avatar_has_accessible_compact_theme_controls(self):
        layout = read("app", "postbox", "(app)", "layout.tsx")
        css = read("app", "globals.css")
        self.assertIn('className="pb-premium-account-appearance"', layout)
        self.assertIn('className="pb-premium-account-appearance-label">Theme', layout)
        self.assertIn('aria-label="Colour theme"', layout)
        self.assertIn('aria-pressed={preferences.theme === value}', layout)
        self.assertIn('onClick={() => void updatePreferences({ theme: value })}', layout)
        for theme in ('"light"', '"system"', '"dark"'):
            self.assertIn('value: ' + theme, layout)
        self.assertIn(".pb-premium-account-appearance .pb-premium-theme-row", css)
        self.assertIn(".pb-premium-account-appearance .pb-premium-theme:focus-visible", css)

    def test_settings_and_account_switch_remain_available(self):
        layout = read("app", "postbox", "(app)", "layout.tsx")
        self.assertIn('href="/postbox/settings"', layout)
        self.assertIn('href="/postbox/settings?section=appearance"', layout)
        self.assertIn('postbox.switchAccount(item.session_id)', layout)


class ThreadOriginalAndProfileDismissalTest(SimpleTestCase):
    """Each thread member owns its raw MIME reference; account card dismisses safely."""

    def test_expanded_thread_message_original_download_matches_single_reader(self):
        thread = read("components", "postbox", "conversation-reader.tsx")
        single = read("app", "postbox", "(app)", "page.tsx")
        api = read("lib", "postbox-api.ts")
        self.assertIn("postbox.rawUrl(member.folder, member.uid, member.uid_validity)", thread)
        self.assertIn("View original message with complete headers", thread)
        self.assertIn('MessageHeaders folder={member.folder} uid={member.uid}', thread)
        self.assertIn('uidValidity={member.uid_validity}', thread)
        self.assertIn('MessageHeaders key={detail.folder', single)
        self.assertIn("postbox.headers(folder, uid, uidValidity)", read("components", "postbox", "message-headers.tsx"))
        self.assertIn('rel="noopener noreferrer"', thread)
        self.assertIn("postbox.rawUrl(detail.folder, detail.uid, detail.uid_validity)", single)
        self.assertIn("qs({ uid_validity: uidValidity })", api)

    def test_profile_outside_pointer_and_escape_both_dismiss(self):
        layout = read("app", "postbox", "(app)", "layout.tsx")
        self.assertIn("useRef<HTMLDetailsElement | null>(null)", layout)
        self.assertIn('ref={accountMenuRef}', layout)
        self.assertIn("menu?.open && event.target instanceof Node", layout)
        self.assertIn("!menu.contains(event.target)", layout)
        self.assertIn("menu.open = false", layout)
        self.assertIn('event.key !== "Escape"', layout)
        self.assertIn('menu.querySelector("summary")?.focus()', layout)
        self.assertIn('document.addEventListener("pointerdown", dismissOutside)', layout)
        self.assertIn('document.removeEventListener("pointerdown", dismissOutside)', layout)
        self.assertIn('document.removeEventListener("keydown", dismissOnEscape)', layout)
        # Controls inside the menu remain usable.
        self.assertIn("postbox.switchAccount(item.session_id)", layout)
        self.assertIn("onClick={() => void updatePreferences({ theme: value })}", layout)

    def test_sidebar_only_lists_mail_folders_but_contacts_and_settings_survive_in_profile(self):
        layout = read("app", "postbox", "(app)", "layout.tsx")
        nav = layout.split("function PremiumFolderNavigation(", 1)[1]
        self.assertNotIn('href="/postbox/contacts"', nav)
        self.assertNotIn('href="/postbox/settings"', nav)
        self.assertNotIn('pb-premium-separator', nav)
        self.assertIn('href="/postbox/contacts"', layout)
        self.assertIn('className="pb-premium-account-contacts"', layout)
        self.assertIn('href="/postbox/settings"', layout)
        self.assertIn('href="/postbox/settings?section=appearance"', layout)
        self.assertIn('className="pb-premium-account-footer"', layout)


class PlainTextLinkificationRegressionTest(SimpleTestCase):
    """Plain-text mail may gain clickable URLs, but never altered message text."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.helper = read("components", "postbox", "linkified-plain-text.tsx")
        cls.page = read("app", "postbox", "(app)", "page.tsx")
        cls.thread = read("components", "postbox", "conversation-reader.tsx")
        cls.css = read("app", "globals.css")

    def test_only_http_and_https_are_promoted_to_links(self):
        self.assertIn('const HTTP_URL = /https?:\\/\\/[^\\s<>"\']+/gi;', self.helper)
        self.assertIn('href={href}', self.helper)
        self.assertIn('target="_blank"', self.helper)
        self.assertIn('rel="noopener noreferrer nofollow"', self.helper)
        helper_code = code_only(self.helper)
        self.assertNotIn("dangerouslySetInnerHTML", helper_code)
        self.assertNotIn("javascript:", helper_code)

    def test_original_plain_text_is_emitted_without_reflow_or_html_conversion(self):
        self.assertIn("output.push(text.slice(cursor, start))", self.helper)
        self.assertIn("output.push(text.slice(cursor))", self.helper)
        self.assertIn("if (trailing) output.push(trailing)", self.helper)
        self.assertNotIn(".trim()", self.helper)
        self.assertNotIn(".replace(", self.helper)

    def test_single_and_thread_readers_use_the_same_safe_linkifier(self):
        self.assertIn(
            'import { LinkifiedPlainText } from "@/components/postbox/linkified-plain-text";',
            self.page,
        )
        self.assertIn(
            'import { LinkifiedPlainText } from "@/components/postbox/linkified-plain-text";',
            self.thread,
        )
        self.assertIn(
            'text={detail.text || "(This message has no readable content.)"}',
            self.page,
        )
        self.assertIn("text={plainQuote.fresh}", self.thread)
        self.assertIn("text={plainQuote.quoted}", self.thread)
        self.assertIn(
            'text={detail.text || "(This message has no readable content.)"}',
            self.thread,
        )

    def test_long_verification_links_can_wrap_without_changing_surrounding_text(self):
        block = self.css.split(".pb-plain-text-link {", 1)[1].split("}", 1)[0]
        self.assertIn("overflow-wrap:anywhere", block)
        self.assertIn("word-break:break-word", block)
