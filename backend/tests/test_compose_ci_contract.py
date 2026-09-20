"""
Every variable the production compose file REQUIRES has a CI placeholder.

WHY THIS EXISTS
    P11 added `POSTBOX_MASTER_PASSWORD: ${POSTBOX_MASTER_PASSWORD:?required}`
    to `deploy/docker-compose.yml`. The CI job that validates that file supplies
    its placeholders as a hand-written `env:` block, and nobody added the new
    name to it. The job failed with:

        error while interpolating x-backend-env.POSTBOX_MASTER_PASSWORD:
        required variable POSTBOX_MASTER_PASSWORD is missing a value: required

    The failure was loud, which is the good case. The bad case is the same drift
    in the other direction: a variable that stops being required, or a job that
    quietly stops resolving the whole file, and CI keeps passing while proving
    less than it used to. Neither direction is visible by reading either file
    alone — the contract is between them, so it is asserted between them.

WHY NOT GENERATE THE PLACEHOLDERS IN THE WORKFLOW
    A loop that reads the compose file and exports a placeholder for every
    `:?required` name would never fail, which sounds better and is worse: the
    point of `:?required` is that a deployment without the value stops. A job
    that manufactures values for names it has never seen cannot tell a variable
    somebody added deliberately from one added by accident, and it would have
    hidden this defect rather than reporting it.

    So the workflow keeps an explicit list, a human keeps adding to it, and
    this test is what makes forgetting cheap — a named assertion here instead
    of a red job after a push.

DKIM_ENCRYPTION_KEY is the one exception, and it is handled: the workflow
generates a real Fernet key into $GITHUB_ENV in an earlier step, because
something downstream actually constructs a Fernet from it.
"""
import re
from pathlib import Path

from django.test import SimpleTestCase

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "deploy" / "docker-compose.yml"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"

#: `${NAME:?message}` — the form that refuses to interpolate without a value.
REQUIRED = re.compile(r"\$\{([A-Z0-9_]+):\?")

#: Supplied by a generator step rather than a literal, with the reason.
GENERATED_IN_WORKFLOW = {
    "DKIM_ENCRYPTION_KEY": "generated per run as a real Fernet key",
}


class ComposeCiContractTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.compose = COMPOSE.read_text(encoding="utf-8")
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_the_files_this_test_reads_exist(self):
        """
        Both paths are read as text. If either moved, every assertion below
        would fail on a missing file rather than on the thing it is about, so
        say which one plainly.
        """
        self.assertTrue(COMPOSE.is_file(), f"missing: {COMPOSE}")
        self.assertTrue(WORKFLOW.is_file(), f"missing: {WORKFLOW}")

    def test_every_required_variable_has_a_ci_value(self):
        """
        The regression. A name declared `:?required` with no placeholder makes
        `docker compose config` fail, and the whole job proves nothing.
        """
        required = set(REQUIRED.findall(self.compose))
        self.assertTrue(required, "no :?required variables found — regex stale?")

        missing = []
        for name in sorted(required):
            if name in GENERATED_IN_WORKFLOW:
                continue
            # A literal assignment in an `env:` block, not merely the name
            # appearing in a comment or in `${NAME:?...}` quoted somewhere.
            if not re.search(rf"^\s+{name}: \S", self.workflow, re.MULTILINE):
                missing.append(name)

        self.assertEqual(
            [], missing,
            "deploy/docker-compose.yml requires these, and ci.yml supplies no "
            f"value for them: {missing}. Add a placeholder to BOTH env blocks "
            "of the compose job — do not relax the `:?required` in the compose "
            "file.",
        )

    def test_both_compose_steps_get_the_same_variables(self):
        """
        The job has two steps that each resolve the file: one runs
        `docker compose config --quiet`, the other writes `resolved.yml` and
        asserts the NetaMate standard against it. They carry separate `env:`
        blocks, so a variable added to one and not the other leaves the second
        step failing for a reason that has nothing to do with what it checks.
        """
        job = self.workflow.split("  compose:", 1)
        self.assertEqual(2, len(job), "the compose job is gone or was renamed")

        steps = job[1].split("      - name: ")
        env_blocks = [
            {
                match.group(1)
                for match in re.finditer(r"^\s+([A-Z0-9_]+): \S", step, re.MULTILINE)
            }
            for step in steps
            if "docker compose config" in step
        ]
        self.assertEqual(
            2, len(env_blocks),
            "expected exactly two steps that resolve the compose file",
        )
        self.assertEqual(
            env_blocks[0], env_blocks[1],
            "the two compose steps must be given the same variables; "
            f"only in the first: {env_blocks[0] - env_blocks[1]}, "
            f"only in the second: {env_blocks[1] - env_blocks[0]}",
        )

    def test_the_requirement_itself_is_not_weakened(self):
        """
        The other way to make this failure go away is to change
        `${POSTBOX_MASTER_PASSWORD:?required}` to a default. That would let a
        production deployment start with an empty master password, and every
        PostBox sign-in would fail against Dovecot with nothing saying why.

        Named explicitly rather than checked generically, because this is the
        one the CI failure would tempt somebody to soften.
        """
        self.assertIn(
            "${POSTBOX_MASTER_PASSWORD:?required}", self.compose,
            "POSTBOX_MASTER_PASSWORD must stay required in the compose file",
        )
