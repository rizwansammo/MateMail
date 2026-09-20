"""
The Platform Console's frontend contract, checked against its own CSP.

WHY THIS EXISTS
    The console shipped to production and every page reported a generic
    failure, while the API endpoints behind them were healthy and returned 200
    to curl. The cause was two correct-looking decisions that contradict each
    other:

      1. `lib/api.ts` baked NEXT_PUBLIC_API_URL — `https://app.matemail.online`
         — as the API base for the browser.
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
        base_block = self.api.split("async function request", 1)[0]
        for literal in ("app.matemail.online", "platform.matemail.online"):
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
        self.assertIn("NEXT_PUBLIC_PLATFORM_HOST", self.middleware)

    def test_the_host_rules_cannot_form_a_loop(self):
        """
        The platform host REWRITES and the app host REDIRECTS. A rewrite does
        not change the browser's URL, so it cannot re-enter the middleware;
        that asymmetry is what terminates the chain after one hop.
        """
        platform_branch = self.middleware.split("if (isPlatformHost(host))", 1)
        self.assertEqual(2, len(platform_branch), "platform-host branch missing")
        branch = platform_branch[1].split("// Organization host", 1)[0]
        self.assertIn('kind: "rewrite"', branch)
        self.assertNotIn('kind: "redirect"', branch,
                         "the platform host must never redirect to itself")

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
