"""Branding contract for the public MateMail homepage."""
from pathlib import Path

from django.test import SimpleTestCase

REPO = Path(__file__).resolve().parents[2]
FRONTEND = REPO / "frontend"


def read(*parts: str) -> str:
    return FRONTEND.joinpath(*parts).read_text(encoding="utf-8")


class PublicHomepageBrandingContractTest(SimpleTestCase):
    def test_homepage_uses_canonical_mark_everywhere(self):
        homepage = read("components", "public-home", "homepage.tsx")

        self.assertIn('import { BrandMark } from "@/components/brand-mark";', homepage)
        self.assertEqual(homepage.count("<BrandMark"), 5)
        self.assertNotIn("/matemail-logo.png", homepage)

    def test_homepage_wordmark_uses_verified_hemi_head_variable(self):
        homepage = read("components", "public-home", "homepage.tsx")
        css = read("app", "globals.css")
        root = read("app", "layout.tsx")

        self.assertIn('className="mm-wordmark">MateMail</span>', homepage)
        self.assertIn('className="mm-brand-name">MateMail</span>', homepage)
        self.assertIn(
            "font-family: var(--font-matemail-hub), sans-serif;",
            css,
        )
        self.assertIn('src: "../public/assets/HemiHead-Bold.otf"', root)
        self.assertIn('variable: "--font-matemail-hub"', root)


    def test_public_host_allows_brand_assets_without_workspace_redirect(self):
        middleware = read("middleware.ts")
        self.assertIn('pathname.startsWith("/assets/")', middleware)

    def test_public_home_owns_its_browser_title_and_favicon(self):
        page = read("app", "public-home", "page.tsx")
        self.assertIn(
            'absolute: "MateMail | Business Email by NetaMate Solutions"',
            page,
        )
        self.assertIn('icon: "/assets/matemail-mark.svg"', page)
