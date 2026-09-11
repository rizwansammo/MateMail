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
