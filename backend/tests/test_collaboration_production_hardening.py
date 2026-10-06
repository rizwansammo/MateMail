import pathlib
import re

from django.test import SimpleTestCase


REPO = pathlib.Path(__file__).resolve().parents[2]
ENGINE = REPO / "engine" / "native_api"
DEPLOY = REPO / "deploy"


class CollaborationProductionHardeningContractTest(SimpleTestCase):
    def test_native_engine_required_version_matches_latest_shipped_migration(self):
        migrations = sorted((ENGINE / "migrations").glob("[0-9][0-9][0-9]_*.sql"))
        self.assertTrue(migrations)
        versions = [int(path.name.split("_", 1)[0]) for path in migrations]
        self.assertEqual(list(range(1, max(versions) + 1)), versions)

        db_source = (ENGINE / "db.py").read_text(encoding="utf-8")
        match = re.search(r"^REQUIRED_VERSION\s*=\s*(\d+)\s*$", db_source, re.M)
        self.assertIsNotNone(match)
        self.assertEqual(max(versions), int(match.group(1)))
        self.assertEqual(6, max(versions))

    def test_native_ready_reports_collaboration_capabilities(self):
        source = (ENGINE / "app.py").read_text(encoding="utf-8")
        ready = source.split("def _handle_ready", 1)[1].split(
            "def _handle_status", 1
        )[0]

        for capability in (
            '"mailbox_sender_authorization": True',
            '"forward_groups": True',
            '"forward_group_sender_policy": True',
            '"collaboration_version": 1',
        ):
            self.assertIn(capability, ready)

    def test_application_deploy_refuses_old_native_engine_before_staging_release(self):
        workflow = (REPO / ".github" / "workflows" / "deploy.yml").read_text(
            encoding="utf-8"
        )
        guard = workflow.index("Checking Native Engine collaboration readiness")
        stage = workflow.index("Stage the exact-revision Compose on MateServer")
        self.assertLess(guard, stage)

        for required in (
            "matemail-native-api",
            "matemail-native-postfix",
            "schema_version",
            "forward_groups",
            "forward_group_sender_policy",
            "collaboration_version",
            "postconf smtpd_recipient_restrictions",
            "def forward_group_verdict",
        ):
            self.assertIn(required, workflow)

    def test_production_migrate_runs_data_preflight_before_backend_can_start(self):
        compose = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
        migrate = compose.index("python manage.py migrate --noinput")
        preflight = compose.index("python manage.py collaboration_preflight")
        backend_gate = compose.index(
            "migrate: {condition: service_completed_successfully}"
        )

        self.assertLess(migrate, preflight)
        self.assertIn("collaboration_preflight", compose)
        self.assertGreater(backend_gate, 0)

    def test_native_postfix_has_both_policy_hook_and_policy_implementation(self):
        main_cf = (
            DEPLOY / "native-engine" / "postfix" / "main.cf"
        ).read_text(encoding="utf-8")
        control = (
            DEPLOY
            / "native-engine"
            / "images"
            / "postfix"
            / "engine_control.py"
        ).read_text(encoding="utf-8")

        restrictions = main_cf.split(
            "smtpd_recipient_restrictions =", 1
        )[1].split("\n\n", 1)[0]
        policy = restrictions.index(
            "check_policy_service inet:127.0.0.1:10032"
        )
        self.assertLess(policy, restrictions.index("permit_mynetworks"))
        self.assertLess(policy, restrictions.index("permit_sasl_authenticated"))
        self.assertIn("def forward_group_verdict", control)

    def test_native_deploy_recreates_postfix_when_bound_configuration_changes(self):
        script = (
            DEPLOY / "native-engine" / "deploy.sh"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "NATIVE_POSTFIX_CONFIG_HASH=$(config_hash postfix)",
            script,
        )
        self.assertIn("NATIVE_POSTFIX_CONFIG_HASH", script)
        self.assertNotIn("docker compose down", script)
