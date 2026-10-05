from pathlib import Path
from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PAGE = ROOT / "frontend" / "app" / "app" / "settings" / "page.tsx"
CUSTOM_HOST_UI = (
    ROOT
    / "frontend"
    / "components"
    / "workspace"
    / "custom-hostnames-settings.tsx"
)


class CustomHostnameUIContractTest(SimpleTestCase):
    def setUp(self):
        self.settings = SETTINGS_PAGE.read_text(encoding="utf-8")
        self.ui = CUSTOM_HOST_UI.read_text(encoding="utf-8")

    def test_workspace_settings_exposes_custom_url_management(self):
        self.assertIn(
            'import { CustomHostnamesSettings }',
            self.settings,
        )
        self.assertIn("<CustomHostnamesSettings canEdit={canEdit} />", self.settings)

    def test_ui_uses_real_custom_hostname_api(self):
        self.assertIn('apiRequest("/api/custom-hostnames/")', self.ui)
        self.assertIn('"/api/custom-hostnames/" + row.id + "/verify/"', self.ui)
        self.assertIn('method: "DELETE"', self.ui)

    def test_customer_can_choose_arbitrary_hub_and_postbox_hostnames(self):
        self.assertIn('placeholder: "manage.company.com"', self.ui)
        self.assertIn('placeholder: "mail.company.com"', self.ui)
        self.assertIn('type Surface = "hub" | "postbox"', self.ui)
        self.assertNotIn("postbox.company.com", self.ui)
        self.assertNotIn("mailadmin.company.com", self.ui)

    def test_one_cname_flow_and_https_status_are_visible(self):
        self.assertIn("Add this DNS record", self.ui)
        self.assertIn("row.cname_target", self.ui)
        self.assertIn("Verify DNS", self.ui)
        self.assertIn("MateMail is preparing HTTPS", self.ui)
        self.assertIn("The browser stays on your", self.ui)

    def test_ui_polls_only_during_transient_edge_states(self):
        self.assertIn("function needsPolling", self.ui)
        self.assertIn('row.provisioning_status === "deactivating"', self.ui)
        self.assertIn("window.setInterval", self.ui)
        self.assertIn("6000", self.ui)
