"""
Final integration contracts for the premium MateMail Workspace Portal.

The portal redesign intentionally follows a strict rule:
    prototype presentation + real MateMail capabilities only.

There is no React component test harness in this repository, so these tests
follow the same source-contract pattern used by the Platform Console and
PostBox regression suites.  ESLint, TypeScript/Next production builds and the
backend API tests cover compilation and behavior; these assertions pin the
cross-page integration decisions that are easiest to regress silently.
"""
from pathlib import Path

from django.test import SimpleTestCase

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "frontend"
APP = FRONTEND / "app" / "app"


def read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


class PremiumPortalRouteContractTest(SimpleTestCase):
    """Every visible premium navigation destination must be a real Next route."""

    NAV_ROUTES = {
        "/app": APP / "page.tsx",
        "/app/onboarding": APP / "onboarding" / "page.tsx",
        "/app/domains": APP / "domains" / "page.tsx",
        "/app/mailboxes": APP / "mailboxes" / "page.tsx",
        "/app/aliases": APP / "aliases" / "page.tsx",
        "/app/forwarding": APP / "forwarding" / "page.tsx",
        "/app/team": APP / "team" / "page.tsx",
        "/app/billing": APP / "billing" / "page.tsx",
        "/app/logs": APP / "logs" / "page.tsx",
        "/app/queue": APP / "queue" / "page.tsx",
        "/app/spam": APP / "spam" / "page.tsx",
        "/app/backups": APP / "backups" / "page.tsx",
        "/app/settings/api-keys": APP / "settings" / "api-keys" / "page.tsx",
        "/app/settings/integrations": APP / "settings" / "integrations" / "page.tsx",
        "/app/settings": APP / "settings" / "page.tsx",
        "/app/security": APP / "security" / "page.tsx",
    }

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.layout = read("app", "app", "layout.tsx")

    def test_every_visible_nav_route_exists(self):
        for href, path in self.NAV_ROUTES.items():
            with self.subTest(href=href):
                self.assertTrue(path.is_file(), f"Missing page for {href}: {path}")

    def test_sidebar_routes_are_all_real(self):
        for href in (
            "/app",
            "/app/onboarding",
            "/app/domains",
            "/app/mailboxes",
            "/app/aliases",
            "/app/forwarding",
            "/app/team",
            "/app/billing",
            "/app/logs",
            "/app/queue",
            "/app/spam",
            "/app/backups",
            "/app/settings/api-keys",
            "/app/settings/integrations",
            "/app/settings",
        ):
            with self.subTest(href=href):
                self.assertIn(f'href: "{href}"', self.layout)

    def test_account_security_is_a_real_account_destination(self):
        self.assertIn('href="/app/security"', self.layout)
        self.assertIn("Account security", self.layout)
        self.assertTrue((APP / "security" / "page.tsx").is_file())

    def test_activity_logs_are_no_longer_hidden_by_a_redirect(self):
        logs = read("app", "app", "logs", "page.tsx")
        self.assertIn('apiRequest("/api/logs/")', logs)
        self.assertNotIn("redirect(", logs)


class PremiumPortalTruthfulnessContractTest(SimpleTestCase):
    """Prototype-only capabilities must never be presented as implemented."""

    def test_billing_does_not_fake_self_service_plan_switching(self):
        billing = read("app", "app", "billing", "page.tsx")
        self.assertIn("Self-service plan switching is not implemented", billing)
        self.assertNotIn("Change plan", billing)
        self.assertNotIn("Upgrade plan", billing)

    def test_backups_do_not_offer_a_fake_tenant_archive_trigger(self):
        backups = read("app", "app", "backups", "page.tsx")
        self.assertIn("Per-organization backup export is not implemented yet", backups)
        self.assertNotIn('method: "POST"', backups)
        self.assertNotIn("startBackup", backups)
        self.assertNotIn("createBackup", backups)
        self.assertIn("not proof that a recoverable tenant archive exists", backups)

    def test_security_does_not_fake_session_inventory(self):
        security = read("app", "app", "security", "page.tsx")
        self.assertIn("does not currently expose an active-device/session inventory", security)
        for fake_endpoint in (
            "/api/auth/sessions/",
            "/api/auth/devices/",
            "/api/auth/session/revoke/",
        ):
            self.assertNotIn(fake_endpoint, security)

    def test_security_only_reveals_backup_codes_from_setup_response(self):
        security = read("app", "app", "security", "page.tsx")
        self.assertIn("data?.backup_codes", security)
        self.assertIn("cannot show these codes again", security)
        self.assertNotIn("/api/auth/2fa/backup-codes/", security)

    def test_mailbox_administration_does_not_restore_admin_webmail_sso(self):
        mailbox = read("app", "app", "mailboxes", "[id]", "page.tsx")
        self.assertIn("old administrator Webmail SSO was removed", mailbox)
        self.assertNotIn("Open PostBox", mailbox)
        self.assertNotIn("/api/postbox/sso", mailbox)

    def test_connected_app_authorization_is_not_saleshub_branded(self):
        authz = read("app", "app", "integrations", "authorize", "page.tsx")
        self.assertIn("details.name", authz)
        self.assertNotIn("SalesHub", authz)
        self.assertNotIn("NetaMate SalesHub", authz)


class PremiumPortalSecurityContractTest(SimpleTestCase):
    """High-risk controls remain bound to the hardened backend flows."""

    def test_two_factor_setup_and_disable_use_the_real_account_endpoints(self):
        security = read("app", "app", "security", "page.tsx")
        self.assertIn('"/api/auth/2fa/setup/"', security)
        self.assertIn('"/api/auth/2fa/disable/"', security)
        self.assertIn("password: disablePassword", security)

    def test_connected_app_approval_keeps_all_backend_factors(self):
        authz = read("app", "app", "integrations", "authorize", "page.tsx")
        for field in ("password", "two_factor_code", "mailbox_factor_code"):
            with self.subTest(field=field):
                self.assertIn(field, authz)
        self.assertIn("/api/integrations/authorize/?request=", authz)

    def test_api_keys_keep_granular_write_scopes(self):
        keys = read("app", "app", "settings", "api-keys", "page.tsx")
        for scope in (
            "domains:write",
            "mailboxes:write",
            "routing:write",
            "admin",
        ):
            with self.subTest(scope=scope):
                self.assertIn(scope, keys)
        self.assertIn('"read", ...newScopes', keys)

    def test_connected_apps_are_bound_to_one_mailbox(self):
        apps = read("app", "app", "settings", "integrations", "page.tsx")
        self.assertIn("mailbox_id: mailboxId", apps)
        self.assertIn("permanently bound to one workspace mailbox", apps)
        self.assertNotIn("mailbox_ids", apps)


class MateMailHubBrandingContractTest(SimpleTestCase):
    """The Organization Hub uses one canonical mark and wordmark."""

    def test_sidebar_is_icon_plus_matemail_hub_only(self):
        layout = read("app", "app", "layout.tsx")
        self.assertIn("MateMail Hub", layout)
        self.assertNotIn("ORGANIZATION PORTAL", layout)
        self.assertNotIn("Switch workspace", layout)
        self.assertNotIn('href="/workspaces"', layout)
        self.assertNotIn("ChevronsUpDown", layout)

    def test_browser_title_is_the_organization_hub_title(self):
        root_layout = read("app", "layout.tsx")
        self.assertIn('"MateMail · Organization Hub"', root_layout)
        self.assertIn('IS_NETAMATE_EMAIL', root_layout)
        self.assertIn('icon: "/assets/matemail-mark.svg"', root_layout)

    def test_brand_mark_uses_the_prototype_mark(self):
        brand = read("components", "brand-mark.tsx")
        self.assertIn('src="/assets/matemail-mark.svg"', brand)
        self.assertNotIn("/matemail-logo.png", brand)
        self.assertTrue((FRONTEND / "public" / "assets" / "matemail-mark.svg").is_file())
        self.assertTrue((FRONTEND / "app" / "icon.svg").is_file())


class SingleOrganizationFrontendContractTest(SimpleTestCase):
    """Tenant users have one organization; no chooser/create/switch UI remains."""

    def test_auth_context_has_no_workspace_switch_api(self):
        auth = read("contexts", "auth-context.tsx")
        self.assertNotIn("/api/workspaces/switch/", auth)
        self.assertNotIn("switchWorkspace", auth)
        self.assertIn("refreshTenant", auth)

    def test_legacy_workspaces_route_is_redirect_only(self):
        workspaces = read("app", "(auth)", "workspaces", "page.tsx")
        self.assertIn('router.replace(isAuthenticated ? "/app" : "/login")', workspaces)
        self.assertNotIn("Create a new workspace", workspaces)
        self.assertNotIn("/api/workspaces/create/", workspaces)
        self.assertNotIn("chooseWorkspace", workspaces)

    def test_login_and_two_factor_land_in_current_organization(self):
        login = read("app", "(auth)", "login", "page.tsx")
        two_factor = read("app", "(auth)", "2fa", "page.tsx")
        self.assertIn('const destination = explicitNext || "/app"', login)
        self.assertNotIn('"/workspaces"', login)
        self.assertNotIn('"/workspaces"', two_factor)

    def test_invite_signup_is_bound_to_invitation(self):
        signup = read("app", "(auth)", "signup", "page.tsx")
        accept = read("app", "(auth)", "accept-invite", "page.tsx")
        self.assertIn('inviteToken ? undefined : form.workspace_name', signup)
        self.assertIn('inviteToken || undefined', signup)
        self.assertIn('/signup?invite=', accept)
        self.assertNotIn('/api/workspaces/switch/', accept)


class PremiumPortalDesignIntegrationTest(SimpleTestCase):
    """All phase-owned pages must remain inside the shared premium system."""

    PHASE_PAGES = (
        ("app", "app", "page.tsx"),
        ("app", "app", "onboarding", "page.tsx"),
        ("app", "app", "domains", "page.tsx"),
        ("app", "app", "domains", "[id]", "page.tsx"),
        ("app", "app", "mailboxes", "page.tsx"),
        ("app", "app", "mailboxes", "[id]", "page.tsx"),
        ("app", "app", "aliases", "page.tsx"),
        ("app", "app", "forwarding", "page.tsx"),
        ("app", "app", "team", "page.tsx"),
        ("app", "app", "billing", "page.tsx"),
        ("app", "app", "settings", "page.tsx"),
        ("app", "app", "logs", "page.tsx"),
        ("app", "app", "queue", "page.tsx"),
        ("app", "app", "spam", "page.tsx"),
        ("app", "app", "backups", "page.tsx"),
        ("app", "app", "settings", "api-keys", "page.tsx"),
        ("app", "app", "settings", "integrations", "page.tsx"),
        ("app", "app", "security", "page.tsx"),
        ("app", "app", "integrations", "authorize", "page.tsx"),
    )

    def test_phase_pages_use_the_shared_portal_surface(self):
        for parts in self.PHASE_PAGES:
            source = read(*parts)
            with self.subTest(path="/".join(parts)):
                self.assertTrue(
                    "portal-page" in source or "portal-auth" in source,
                    f"{'/'.join(parts)} has drifted out of the premium portal system",
                )

    def test_workspace_shell_imports_the_scoped_premium_styles(self):
        layout = read("app", "app", "layout.tsx")
        root_layout = read("app", "layout.tsx")
        css = read("app", "app", "portal-premium.css")
        self.assertIn("portal-premium", layout)
        self.assertIn("portal-premium.css", root_layout)
        self.assertIn(".portal-premium", css)
