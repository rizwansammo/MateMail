"""
The Platform Console's frontend contract, checked against its own CSP.

WHY THIS EXISTS
    The console shipped to production and every page reported a generic
    failure, while the API endpoints behind them were healthy and returned 200
    to curl. The cause was two correct-looking decisions that contradict each
    other:

      1. `lib/api.ts` baked NEXT_PUBLIC_API_URL — then
         `https://app.matemail.online`, the Workspace's hostname before the
         P11 rename — as the API base for the browser.
      2. `middleware.ts` sets `connect-src 'self'` on every console.

    On platform.matemail.online, `'self'` is the platform host, so a fetch to
    app.matemail.online was refused by the browser before it left. Nothing in
    the backend logs, nothing in nginx, no failing test — the request never
    happened.

    These assertions read the frontend sources the way the existing tests read
    nginx and compose files. They are cheap, and they encode the one rule that
    keeps the two consoles working: in the browser, the API is same-origin.
"""
import re
from pathlib import Path

from django.test import SimpleTestCase

REPO = Path(__file__).resolve().parents[2]
API_TS = REPO / "frontend" / "lib" / "api.ts"
MIDDLEWARE_TS = REPO / "frontend" / "middleware.ts"


class PlatformFrontendContractTest(SimpleTestCase):
    def setUp(self):
        self.api = API_TS.read_text(encoding="utf-8")
        self.middleware = MIDDLEWARE_TS.read_text(encoding="utf-8")

    def test_the_browser_calls_its_own_origin_in_production(self):
        """
        The fix, stated as a rule: production + browser => relative base.

        nginx proxies /api/ to the same Django backend on both hostnames, so a
        relative path follows whichever console the operator is on, and the
        HttpOnly refresh cookie stays first-party to it.
        """
        self.assertRegex(
            self.api,
            r'typeof window !== "undefined"[\s\S]{0,120}NODE_ENV === "production"',
            "api.ts must branch on being in the browser in production",
        )

    def test_no_absolute_api_host_reaches_the_browser(self):
        """
        The specific regression. A literal hostname here is baked into the
        client bundle and becomes cross-origin on the other console.
        """
        # Every hostname the product answers on, plus the retired one. A
        # literal for any of them is cross-origin from at least one of the
        # others, which is the whole failure this guards.
        base_block = self.api.split("async function request", 1)[0]
        for literal in (
            "portal.matemail.online",
            "postbox.matemail.online",
            "platform.matemail.online",
            "app.matemail.online",
        ):
            # Comments explain the history and may name the hosts; code must not.
            code = "\n".join(
                line for line in base_block.splitlines()
                if not line.lstrip().startswith(("*", "//", "/*"))
            )
            self.assertNotIn(literal, code, f"{literal} must not be a code literal")

    def test_the_csp_that_makes_this_necessary_is_still_there(self):
        """
        If `connect-src` is ever widened, the rule above stops being forced by
        the browser — and a future change could reintroduce an absolute base
        without anything failing until it reached production again.
        """
        self.assertIn("connect-src 'self'", self.middleware)

    def test_the_platform_host_is_configurable_not_hardcoded(self):
        """
        The hostname comparison belongs to configuration. Hard-coding it would
        make a staging deployment impossible to route.
        """
        for variable in (
            "NEXT_PUBLIC_WORKSPACE_HOST",
            "NEXT_PUBLIC_PLATFORM_HOST",
            "NEXT_PUBLIC_POSTBOX_HOST",
            "NEXT_PUBLIC_LEGACY_WORKSPACE_HOSTS",
        ):
            self.assertIn(variable, self.middleware)

    def test_the_retired_hostname_is_a_redirect_and_not_a_surface(self):
        """
        `app.matemail.online` was the Workspace until P11. It has to keep
        working for old bookmarks, and it must not become a second address
        the product serves from — two live hostnames for one surface is how
        cookies, CORS and canonical links quietly disagree.

        The middleware is the authority checked here rather than the nginx
        template, because the template may not be the file an operator
        actually installed.
        """
        self.assertIn("isLegacyWorkspaceHost", self.middleware)
        # It must be redirected, not rewritten: a rewrite would serve the
        # Workspace from the old hostname rather than move people off it.
        branch = self._top_level_branch(
            "if (WORKSPACE_HOST && isLegacyWorkspaceHost(host))"
        )
        self.assertIn('kind: "redirect"', branch)
        self.assertNotIn('kind: "rewrite"', branch)
        # 308, so a re-submitted POST keeps its method and body.
        self.assertIn("status: 308", branch)

    def test_no_console_is_served_from_the_retired_hostname(self):
        """
        The Workspace host is the redirect's target. If the two were ever the
        same value the redirect would loop, and the check above would still
        pass — it only asserts the shape of the branch.
        """
        self.assertNotIn(
            "NEXT_PUBLIC_WORKSPACE_HOST ?? \"app.matemail.online\"",
            self.middleware,
        )

    def test_the_host_rules_cannot_form_a_loop(self):
        """
        The platform host REWRITES; the Workspace host REDIRECTS to it. A
        rewrite does not change the browser's URL, so it cannot re-enter the
        middleware; that asymmetry is what terminates the chain after one hop.

        The branch is bounded by the next top-level `if (`, not by a comment.
        An earlier version sliced on the text "// Organization host" and so
        silently swallowed the rest of the file when that comment was renamed,
        failing on another branch's redirect while reporting the platform host.
        """
        branch = self._top_level_branch("if (isPlatformHost(host))")
        self.assertIn('kind: "rewrite"', branch)
        self.assertNotIn('kind: "redirect"', branch,
                         "the platform host must never redirect to itself")

    def _top_level_branch(self, opening: str) -> str:
        """The text from `opening` up to the next top-level `if (`."""
        parts = self.middleware.split(opening, 1)
        self.assertEqual(2, len(parts), f"branch missing: {opening}")
        rest = parts[1]
        end = rest.find("\n  if (")
        return rest if end == -1 else rest[:end]

    def test_api_paths_are_rooted(self):
        """
        A relative base only works if every caller's path starts with "/".
        `${API_BASE}${path}` with a bare path would resolve against the current
        page and produce /platform/organizations/api/... on a nested route.
        """
        platform_api = (REPO / "frontend" / "lib" / "platform-api.ts").read_text(
            encoding="utf-8")
        for match in re.finditer(r'api\.(get|post|patch|delete)<[^>]*>\(\s*`([^`]+)`',
                                 platform_api):
            path = match.group(2)
            with self.subTest(path=path):
                self.assertTrue(
                    path.startswith("/") or path.startswith("${P}"),
                    f"API path must be rooted: {path}",
                )
        self.assertRegex(platform_api, r'const P = "/api/platform"')


class PostBoxRemoteImageCspTest(SimpleTestCase):
    """
    PostBox may load remote HTTPS images; the other consoles may not.

    `img-src 'self' data: blob:` silently broke two things that look unrelated
    and share one cause: a signature's own logo on the company CDN, and the
    reader's "display remote images" control — the server sent the images once
    the reader asked, and the browser refused them with nothing in the UI to
    explain why.

    Widening this is safe because the SERVER decides whether a remote image is
    ever sent (`load_remote_images`, off by default). CSP is the mechanism; the
    preference is the policy. A CSP forbidding what the server never emits adds
    no protection and here removed a feature.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.middleware = (
            Path(__file__).resolve().parents[2]
            / "frontend" / "middleware.ts"
        ).read_text(encoding="utf-8")

    def test_the_policy_depends_on_the_host(self):
        self.assertIn("allowRemoteImages", self.middleware)
        self.assertIn("buildCsp(nonce, isPostBoxRequest(request))", self.middleware)

    def test_custom_postbox_surface_marker_is_used_only_for_unknown_hosts(self):
        self.assertIn("function customSurfaceOf(request: NextRequest)", self.middleware)
        self.assertIn('x-matemail-custom-host', self.middleware)
        self.assertIn('x-matemail-surface', self.middleware)
        self.assertIn('customSurfaceOf(request) === "postbox"', self.middleware)
        # Canonical/configured hosts ignore customer-supplied surface headers.
        self.assertIn("if (knownHost) return null;", self.middleware)

    def test_https_images_are_conditional_not_unconditional(self):
        """
        The failure to guard against: someone 'fixes' a broken image by adding
        `https:` to the shared policy, widening the Workspace and the Platform
        Console at the same time.
        """
        self.assertNotIn("img-src 'self' data: blob: https:", self.middleware)
        self.assertIn('allowRemoteImages ? " https:" : ""', self.middleware)

    def test_the_base_policy_is_unchanged(self):
        self.assertIn("img-src 'self' data: blob:", self.middleware)
