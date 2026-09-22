"""
Static assertions on the deployment workflow.

These are cheap guards against changes that are easy to make and expensive to
discover: a deploy trigger that fires on push, a control that does not control,
or a host-global docker command on a shared production box.
"""
import pathlib

import yaml
from django.test import SimpleTestCase

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
DEPLOY = REPO / ".github" / "workflows" / "deploy.yml"
CI = REPO / ".github" / "workflows" / "ci.yml"


def load(path):
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    # PyYAML parses the bare key `on:` as the boolean True.
    data["on"] = data.pop(True, data.get("on"))
    return data


class DeployWorkflowTest(SimpleTestCase):
    def setUp(self):
        self.raw = DEPLOY.read_text(encoding="utf-8")
        self.wf = load(DEPLOY)

    def test_deploy_is_manual_only(self):
        self.assertEqual(list(self.wf["on"]), ["workflow_dispatch"])

    def test_deploy_requires_a_sha_and_confirmation(self):
        inputs = self.wf["on"]["workflow_dispatch"]["inputs"]
        self.assertIn("image_tag", inputs)
        self.assertIn("confirm", inputs)
        self.assertTrue(inputs["image_tag"]["required"])
        self.assertIn("[0-9a-f]{40}", self.raw, "full-SHA format check missing")
        self.assertIn("MateServer", self.raw, "confirmation phrase missing")

    def test_no_fake_run_migrations_control(self):
        """
        Migrations are mandatory: the compose `migrate` service gates the
        others via service_completed_successfully. An input implying it could
        be skipped would be a control that does not control.
        """
        inputs = self.wf["on"]["workflow_dispatch"]["inputs"]
        self.assertNotIn("run_migrations", inputs)
        # Also gone from the SSH env wiring (a comment explaining the absence
        # is fine, an actual reference is not).
        live = [
            line for line in self.raw.splitlines()
            if "RUN_MIGRATIONS" in line and not line.strip().startswith("#")
        ]
        self.assertEqual(live, [], f"RUN_MIGRATIONS still wired: {live}")

    def test_no_host_global_docker_maintenance(self):
        """
        MateServer runs NetaMate, TalkRoom, MateDesk, MateAssist, MateConnect
        and Portfolio. A MateMail deploy must never run a host-wide prune: it
        would delete images another application needs to recreate a container.
        """
        forbidden = [
            "docker image prune",
            "docker system prune",
            "docker volume prune",
            "docker network prune",
            "docker container prune",
        ]
        for phrase in forbidden:
            live = [
                line for line in self.raw.splitlines()
                if phrase in line and not line.strip().startswith("#")
            ]
            self.assertEqual(live, [], f"host-global maintenance present: {live}")

    def test_no_destructive_compose_verbs(self):
        for phrase in ("docker compose down", "compose down -v"):
            live = [
                line for line in self.raw.splitlines()
                if phrase in line and not line.strip().startswith("#")
            ]
            self.assertEqual(live, [], f"destructive compose verb present: {live}")

    def test_uses_least_privilege_github_token(self):
        deploy = self.wf["jobs"]["deploy"]
        self.assertEqual(deploy["permissions"], {"contents": "read", "packages": "read"})
        self.assertIn("secrets.GITHUB_TOKEN", self.raw)
        live = [
            line for line in self.raw.splitlines()
            if "secrets.GHCR_TOKEN" in line and not line.strip().startswith("#")
        ]
        self.assertEqual(live, [], "a long-lived GHCR_TOKEN secret is still referenced")

    def test_uses_the_expected_vps_secrets(self):
        for name in ("VPS_HOST", "VPS_USER", "VPS_SSH_KEY", "VPS_PORT"):
            self.assertIn(f"secrets.{name}", self.raw)

    def test_deploy_is_gated_and_environment_scoped(self):
        deploy = self.wf["jobs"]["deploy"]
        self.assertEqual(deploy["needs"], ["gate"])
        self.assertEqual(deploy["environment"]["name"], "production")


class CiWorkflowTest(SimpleTestCase):
    def setUp(self):
        self.raw = CI.read_text(encoding="utf-8")
        self.wf = load(CI)

    def test_ci_never_deploys(self):
        for phrase in ("appleboy/ssh-action", "VPS_HOST", "ssh ", "scp "):
            self.assertNotIn(phrase, self.raw, f"CI can reach the server via {phrase!r}")

    def test_publish_requires_every_gate(self):
        needs = self.wf["jobs"]["publish"]["needs"]
        self.assertCountEqual(needs, ["backend", "frontend", "compose"])

    def test_publish_never_runs_for_a_pull_request(self):
        self.assertIn("github.event_name != 'pull_request'", self.raw)

    def test_no_latest_tag_is_published(self):
        """
        Production must name the exact image it runs so a rollback can name
        the one it returns to. Assert on the published tag list specifically —
        the string ":latest" also appears in the compose compliance check,
        where it is the guard against exactly this.
        """
        published = []
        for step in self.wf["jobs"]["publish"]["steps"]:
            tags = (step.get("with") or {}).get("tags")
            if tags:
                published += [t.strip() for t in tags.strip().splitlines()]
        self.assertTrue(published, "no image tags found in the publish job")
        for tag in published:
            self.assertFalse(
                tag.endswith(":latest"), f"publish job produces a latest tag: {tag}"
            )
        self.assertTrue(
            any("github.sha" in t for t in published),
            "publish job does not produce a SHA-pinned tag",
        )


class ProductionNetworkTopologyTest(SimpleTestCase):
    """
    Invariants of the private MateMail-to-Mail-Engine path.

    The engine used to be reached at a host-published socket on this stack's own
    bridge gateway (172.24.0.1). That was measured and rejected: a published
    bind address selects a DESTINATION address, never a permitted SOURCE, so
    every other Docker network on the host could open TCP connections to the
    engine's API and submission ports. Six unrelated application networks could.

    The replacement is a dedicated internal network carrying one TCP passthrough
    gateway. These tests pin the parts of that design that are easy to undo by
    accident and whose failure modes look like anything but their cause.
    """

    COMPOSE = REPO / "deploy" / "docker-compose.yml"
    LINK = "matemail_engine_link"
    ENGINE_HOST = "mx.matemail.online"
    REJECTED_GATEWAY = "172.24.0.1"

    def setUp(self):
        self.raw = self.COMPOSE.read_text(encoding="utf-8")
        with open(self.COMPOSE, encoding="utf-8") as fh:
            self.compose = yaml.safe_load(fh)

    # ── the rejected topology must not come back ────────────────────────────

    def test_nothing_addresses_the_host_bridge_gateway(self):
        self.assertNotIn(
            self.REJECTED_GATEWAY + ":", self.raw,
            "the engine is addressed at the app network's host gateway again; "
            "that socket is reachable from every Docker network on the host",
        )

    def test_no_service_hard_codes_a_host_mapping_for_the_engine(self):
        for name, svc in self.compose["services"].items():
            self.assertIsNone(
                svc.get("extra_hosts"),
                f"{name} maps a host entry; the engine hostname now comes from "
                "the link network's alias, not a pinned address",
            )

    def test_no_application_subnet_is_pinned(self):
        """
        The pins existed only to keep the engine's bind address stable. That
        coupling is gone, so the pins are gone: nothing outside this file
        depends on which range Docker allocates.
        """
        for name, cfg in self.compose["networks"].items():
            self.assertNotIn(
                "ipam", cfg,
                f"{name} pins a subnet; no remaining requirement needs one",
            )

    # ── the dedicated link ──────────────────────────────────────────────────

    def test_the_engine_link_is_external(self):
        """
        Externally owned so neither this stack's lifecycle nor the engine's can
        delete a network the other still depends on.
        """
        self.assertTrue(self.compose["networks"][self.LINK].get("external"))

    def test_only_the_services_that_call_the_engine_join_the_link(self):
        expected = {"backend", "celery-worker"}
        joined = {
            name for name, svc in self.compose["services"].items()
            if self.LINK in (svc.get("networks") or [])
        }
        self.assertEqual(
            joined, expected,
            "exactly backend and celery-worker may reach the Mail Engine",
        )

    def test_dedicated_tenant_hosts_are_passed_to_backend(self):
        backend_env = self.compose["x-backend-env"]
        self.assertIn(
            "DEDICATED_TENANT_HOSTS",
            backend_env,
            "production .env bindings must reach Django; otherwise dedicated host isolation is silently disabled",
        )

    def test_datastores_and_frontend_stay_off_the_link(self):
        for service in ("postgres", "redis", "frontend", "migrate", "celery-beat"):
            self.assertNotIn(
                self.LINK, self.compose["services"][service].get("networks") or [],
                f"{service} does not call the Mail Engine",
            )

    def test_the_datastore_network_is_still_internal(self):
        self.assertIs(self.compose["networks"]["matemail_internal"]["internal"], True)

    def test_datastores_stay_off_the_application_network(self):
        for service in ("postgres", "redis"):
            self.assertEqual(
                self.compose["services"][service]["networks"], ["matemail_internal"]
            )

    # ── addressing ──────────────────────────────────────────────────────────

    def test_the_engine_is_addressed_by_hostname_not_by_ip(self):
        """
        The certificate is issued for the hostname, and the link network's alias
        is what resolves it. An IP in a URL setting cannot validate against the
        certificate, and the ways around that are all worse than the routing
        problem they solve.
        """
        for key in ("MAIL_ENGINE_API_URL", "EMAIL_HOST"):
            for line in self.raw.splitlines():
                if line.strip().startswith(f"{key}:"):
                    self.assertNotIn(self.REJECTED_GATEWAY, line)

    def test_no_service_uses_host_networking(self):
        for name, svc in self.compose["services"].items():
            self.assertNotEqual(
                svc.get("network_mode"), "host", f"{name} uses host networking"
            )


class EngineLinkPreflightTest(SimpleTestCase):
    """
    `matemail_engine_link` is external, so Compose will not create it — but it
    fails late, after the database backup and the image pull, with a message
    that reads like a transient Docker fault rather than a missing prerequisite.
    The deploy checks for it up front.

    It must NOT create one: a network conjured with Docker's defaults is a
    routable bridge, not an internal one, which silently reopens the
    cross-application exposure the dedicated link exists to close.
    """

    def setUp(self):
        self.raw = DEPLOY.read_text(encoding="utf-8")

    def test_the_deploy_verifies_the_link_exists(self):
        self.assertIn("docker network inspect matemail_engine_link", self.raw)

    def test_the_deploy_verifies_the_link_is_internal(self):
        self.assertIn("{{.Internal}}", self.raw)
        self.assertIn("LINK_INTERNAL", self.raw)

    def test_the_deploy_never_creates_the_link_itself(self):
        for line in self.raw.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("echo"):
                continue
            self.assertNotIn(
                "docker network create", stripped,
                "the deploy must not create the engine link — a default-settings "
                "network would be routable, not internal",
            )

    def test_the_preflight_runs_before_the_pull(self):
        """
        Ordering by line, and only counting executed lines — the phrase
        "docker compose pull" also appears in a comment much earlier, and
        matching that would make this assertion pass for the wrong reason.
        """
        lines = self.raw.splitlines()

        def line_of(command):
            for i, line in enumerate(lines):
                if line.strip() == command:
                    return i
            self.fail(f"{command!r} is not executed anywhere in the deploy")

        self.assertLess(
            line_of("if ! docker network inspect matemail_engine_link >/dev/null 2>&1; then"),
            line_of("docker compose pull"),
            "the preflight must fail before the deploy does any work",
        )
