"""
Compose and images are one deployment unit.

`deploy/docker-compose.yml` now carries security-critical topology — which
services join the private engine link, that the link is external, that the
datastore network stays internal. Deploying new images against whatever Compose
file happened to be sitting on the server is how those drift apart, and the
drift is silent: the stack comes up, and the boundary is simply not what the
release says it is.

So the Compose file is taken from the same commit as the images, validated on
the server before it replaces anything, installed atomically, and rolled back
together with the image pins.
"""
import pathlib
import shutil
import subprocess
import tempfile

import yaml
from django.test import SimpleTestCase

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
DEPLOY = REPO / ".github" / "workflows" / "deploy.yml"


class DeployRevisionSyncTest(SimpleTestCase):
    def setUp(self):
        self.raw = DEPLOY.read_text(encoding="utf-8")
        with open(DEPLOY, encoding="utf-8") as fh:
            self.wf = yaml.safe_load(fh)
        self.steps = self.wf["jobs"]["deploy"]["steps"]
        self.script = "\n".join(
            (s.get("with") or {}).get("script") or s.get("run") or "" for s in self.steps
        )

    def _step_index(self, needle):
        for i, step in enumerate(self.steps):
            haystack = " ".join(filter(None, [
                step.get("name", ""), step.get("uses", ""),
                (step.get("with") or {}).get("script") or step.get("run") or "",
            ]))
            if needle in haystack:
                return i
        return None

    # ── the Compose file comes from IMAGE_TAG, not from workflow HEAD ───────

    def test_the_compose_file_is_extracted_from_the_requested_commit(self):
        self.assertIn(
            'git show "${IMAGE_TAG}:deploy/docker-compose.yml"', self.script,
            "the Compose file must come from the deployed commit, not the checkout",
        )

    def test_the_workflow_never_transfers_its_own_working_copy(self):
        """
        A bare `deploy/docker-compose.yml` source would be the checkout HEAD,
        which is not necessarily IMAGE_TAG — a manual deploy of an older
        published SHA must receive that SHA's Compose file.
        """
        for step in self.steps:
            source = (step.get("with") or {}).get("source")
            if source:
                self.assertIn(
                    "candidate/", source,
                    "only the extracted candidate may be transferred",
                )

    def test_no_windows_working_tree_path_is_referenced(self):
        for marker in ("C:\\", "C:/", "\\Users\\", "/mnt/c/"):
            self.assertNotIn(marker, self.raw, f"workflow references a local path: {marker}")

    def test_the_requested_commit_is_verified_to_exist(self):
        self.assertIn('git cat-file -e "${IMAGE_TAG}^{commit}"', self.script)

    def test_the_requested_commit_must_be_reachable_from_the_default_branch(self):
        """Blocks deploying a dangling object, or one from an abandoned branch."""
        self.assertIn("git merge-base --is-ancestor", self.script)

    def test_the_compose_file_is_verified_to_exist_in_that_commit(self):
        self.assertIn('git cat-file -e "${IMAGE_TAG}:deploy/docker-compose.yml"', self.script)

    def test_the_sha_format_is_checked_where_it_selects_a_git_object(self):
        """
        Once in the gate job, and again in the deploy job immediately before the
        value is used to select a Git object. A format check belongs at the
        boundary that consumes the value, not only at the front door.
        """
        self.assertIn("[0-9a-f]{40}", self.script, "the deploy job does not re-check the SHA")
        self.assertGreaterEqual(
            self.raw.count("[0-9a-f]{40}"), 2,
            "expected the SHA format to be checked in both the gate and deploy jobs",
        )

    def test_the_checkout_has_full_history(self):
        """A shallow clone would not contain the commit being deployed."""
        for step in self.steps:
            if (step.get("uses") or "").startswith("actions/checkout"):
                self.assertEqual((step.get("with") or {}).get("fetch-depth"), 0)
                return
        self.fail("no checkout step")

    def test_the_confirmation_gate_is_unchanged(self):
        self.assertIn('!= "MateServer"', self.raw)

    # ── staging, validation, atomic install ─────────────────────────────────

    def test_the_candidate_is_staged_rather_than_written_over_the_live_file(self):
        self.assertIn("/opt/MateMail/.deploy/", self.script + str(self.steps))
        self.assertIn("docker-compose.${IMAGE_TAG}.yml", self.script)

    def test_the_candidate_is_validated_before_it_is_installed(self):
        validate = self.script.index('-f "$CANDIDATE" config --quiet')
        install = self.script.index('mv -f "$DEPLOY_DIR/.docker-compose.yml.new"')
        self.assertLess(validate, install, "the candidate is installed before validation")

    def test_the_candidate_is_validated_against_the_production_env(self):
        self.assertIn('--env-file "$DEPLOY_DIR/.env" -f "$CANDIDATE"', self.script)

    def test_security_invariants_are_checked_on_the_resolved_candidate(self):
        for invariant in (
            "8020", "3020",
            "must publish no host port",
            "uses host networking",
            ":latest",
            "matemail_internal must stay internal",
            "matemail_engine_link must be an external network",
            "only backend and celery-worker may join the engine link",
        ):
            self.assertIn(invariant, self.script, f"invariant not checked: {invariant}")

    def test_the_invariant_check_reads_composes_own_resolved_output(self):
        """
        Asserting on the file text would let an anchor, an override or a
        variable hide a change. Compose resolves it; the check reads that.
        """
        self.assertIn("config --format json", self.script)

    def test_the_live_compose_is_backed_up_before_replacement(self):
        backup = self.script.index('cp -a docker-compose.yml "$COMPOSE_BACKUP"')
        install = self.script.index('mv -f "$DEPLOY_DIR/.docker-compose.yml.new"')
        self.assertLess(backup, install)
        self.assertIn("/backups/docker-compose.${STAMP}.yml", self.script)

    def test_backups_are_timestamped_and_bounded_not_wiped(self):
        self.assertIn("date -u +%Y%m%dT%H%M%SZ", self.script)
        self.assertIn("tail -n +11 | xargs -r rm --", self.script)

    def test_the_install_is_atomic_and_correctly_owned(self):
        """
        A rename on the same filesystem, so no reader — including a concurrent
        `docker compose` — can observe a half-written Compose file.
        """
        self.assertIn(
            'mv -f "$DEPLOY_DIR/.docker-compose.yml.new" "$DEPLOY_DIR/docker-compose.yml"',
            self.script,
        )
        self.assertIn("install -o root -g root -m 644", self.script)

    def test_images_are_pulled_only_after_the_candidate_is_installed(self):
        """
        Ordering by executed line. The phrase "docker compose pull" also appears
        in an explanatory comment in an earlier step, and matching that would
        make this pass for the wrong reason.
        """
        lines = self.script.splitlines()
        pull = next(
            (i for i, line in enumerate(lines) if line.strip() == "docker compose pull"),
            None,
        )
        install = next(
            (i for i, line in enumerate(lines)
             if line.strip().startswith('mv -f "$DEPLOY_DIR/.docker-compose.yml.new"')),
            None,
        )
        self.assertIsNotNone(pull, "docker compose pull is never executed")
        self.assertIsNotNone(install, "the candidate is never installed")
        self.assertLess(install, pull, "images pulled against the old Compose file")

    # ── rollback ────────────────────────────────────────────────────────────

    def test_rollback_restores_the_compose_file(self):
        self.assertIn('cp -a "$COMPOSE_BACKUP"', self.script)

    def test_rollback_restores_the_env_too(self):
        """
        Restoring one without the other leaves new topology against old images,
        or the reverse — both are states nobody chose.
        """
        self.assertIn('cp -a "$ENV_BACKUP"', self.script)

    def test_rollback_brings_the_previous_stack_back(self):
        body = self.script.split("rollback() {")[1].split("trap rollback ERR")[0]
        self.assertIn("docker compose up -d", body)

    def test_rollback_is_never_destructive(self):
        body = self.script.split("rollback() {")[1].split("trap rollback ERR")[0]
        for destructive in ("down -v", "--volumes", "prune", "rm -rf"):
            self.assertNotIn(destructive, body, f"rollback runs {destructive}")

    def test_rollback_is_armed_only_after_the_live_state_changed(self):
        arm = self.script.index("ROLLBACK_ARMED=1")
        install = self.script.index('mv -f "$DEPLOY_DIR/.docker-compose.yml.new"')
        self.assertLess(install, arm, "rollback armed before anything was replaced")

    def test_rollback_is_disarmed_once_the_deployment_succeeds(self):
        """A failure while printing a summary must not tear down a healthy stack."""
        self.assertIn("trap - ERR", self.script)

    def _health_timeout_branch(self):
        """The body of `if [ "$i" -eq 30 ]; then ... fi` in the health loop."""
        marker = 'if [ "$i" -eq 30 ]; then'
        self.assertIn(marker, self.script, "the health-timeout branch is gone")
        after = self.script.split(marker, 1)[1]
        # Ends at the `fi` closing this branch — the first line that is exactly
        # `fi` at the branch indentation.
        body = []
        for line in after.splitlines():
            if line.strip() == "fi":
                break
            body.append(line)
        return "\n".join(body)

    def test_the_health_timeout_calls_rollback_explicitly(self):
        """
        bash fires ERR when a command returns non-zero, NOT when the script
        calls `exit`. An earlier version of this branch relied on `exit 1`
        reaching `trap rollback ERR`, so a backend that never came healthy left
        the new Compose file and new image pins in place — the one state the
        whole mechanism exists to prevent.
        """
        body = self._health_timeout_branch()
        calls = [line.strip() for line in body.splitlines()
                 if line.strip() == "rollback"]
        self.assertEqual(
            len(calls), 1,
            "the health-timeout branch must call rollback exactly once, "
            "explicitly — the ERR trap will not fire on `exit`",
        )

    def test_rollback_is_called_before_the_branch_exits(self):
        body = self._health_timeout_branch()
        lines = [line.strip() for line in body.splitlines()]
        self.assertIn("rollback", lines)
        self.assertIn("exit 1", lines)
        self.assertLess(
            lines.index("rollback"), lines.index("exit 1"),
            "rollback must run before the script terminates",
        )

    def test_the_branch_does_not_claim_the_err_trap_will_fire(self):
        """The comment that made the bug look intentional must not come back."""
        body = self._health_timeout_branch().lower()
        for false_claim in (
            "rolls back automatically via the err trap",
            "automatically via the err trap",
        ):
            self.assertNotIn(false_claim, body)

    def test_diagnostics_cannot_pre_empt_the_rollback(self):
        """
        Under `set -e` a failing `docker compose logs` would trip the ERR trap
        first and take a different path out of this branch. The diagnostics are
        tolerant so the explicit path is the one that runs.
        """
        body = self._health_timeout_branch()
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("docker compose "):
                self.assertTrue(
                    stripped.endswith("|| true"),
                    f"diagnostic can abort the branch: {stripped}",
                )

    def test_the_err_trap_is_still_installed_for_unexpected_failures(self):
        """
        The explicit call covers the deliberate failure. The trap still covers
        every command that fails unexpectedly after the live state changed.
        """
        self.assertIn("trap rollback ERR", self.script)

    def test_rollback_runs_at_most_once_however_it_is_entered(self):
        body = self.script.split("rollback() {")[1].split("trap rollback ERR")[0]
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        self.assertEqual(
            lines[0], '[ "$ROLLBACK_ARMED" = "1" ] || return 0',
            "rollback must guard on ROLLBACK_ARMED first",
        )
        self.assertEqual(
            lines[1], "ROLLBACK_ARMED=0",
            "rollback must disarm itself before doing any work",
        )

    # ── the engine link stays a prerequisite, never a side effect ───────────

    def test_the_engine_network_preflight_runs_before_anything_is_transferred(self):
        preflight = self._step_index("docker network inspect matemail_engine_link")
        transfer = self._step_index("scp-action")
        self.assertIsNotNone(preflight, "no engine-link preflight")
        self.assertIsNotNone(transfer, "no transfer step")
        self.assertLess(
            preflight, transfer, "the server is written to before it is checked"
        )

    def test_the_deploy_still_never_creates_the_engine_network(self):
        for line in self.script.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("echo"):
                continue
            self.assertNotIn(
                "docker network create", stripped,
                "a network created with Docker defaults is routable, not internal",
            )

    def test_the_transfer_action_is_pinned_to_a_release(self):
        transfer = self._step_index("scp-action")
        uses = self.steps[transfer]["uses"]
        self.assertRegex(uses, r"@v\d+\.\d+\.\d+$", f"{uses} is not pinned to a release")

    def test_every_third_party_action_is_version_pinned(self):
        for step in self.steps:
            uses = step.get("uses")
            if not uses:
                continue
            self.assertIn("@", uses, f"{uses} has no version")
            for floating in ("@main", "@master", "@latest"):
                self.assertNotIn(floating, uses, f"{uses} floats")


class BashErrTrapSemanticsTest(SimpleTestCase):
    """
    The shell fact the deployment depends on, pinned by running bash.

    This exists because the assumption was wrong once and the wrongness was
    invisible: the workflow read as though `exit 1` would reach
    `trap rollback ERR`, a comment said so, and a test asserted the comment.
    Nothing asserted the behaviour.

    Two tiny scripts, one shell invocation each. No fake deployment.
    """

    BASH = shutil.which("bash")

    def _run(self, body):
        script = "#!/bin/bash\nset -Eeuo pipefail\n" \
                 "rollback() { echo ROLLBACK_RAN; }\ntrap rollback ERR\n" + body
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "t.sh"
            path.write_text(script, encoding="utf-8", newline="\n")
            proc = subprocess.run(
                [self.BASH, str(path)], capture_output=True, text=True, timeout=30
            )
        return proc.stdout

    def setUp(self):
        if not self.BASH:
            self.skipTest("bash is not available on this machine")

    def test_an_explicit_exit_does_not_fire_the_err_trap(self):
        """
        The bug. If this ever starts printing ROLLBACK_RAN, bash changed and the
        explicit call in the workflow becomes a double-rollback rather than the
        only rollback — still safe, because rollback disarms itself, but the
        reasoning in the workflow comments would need revisiting.
        """
        self.assertNotIn("ROLLBACK_RAN", self._run("exit 1\n"))

    def test_a_failing_command_does_fire_the_err_trap(self):
        """The other half: the trap is genuinely useful for unexpected failures."""
        self.assertIn("ROLLBACK_RAN", self._run("false\n"))

    def test_calling_it_explicitly_before_exit_is_what_works(self):
        """The fix, in miniature."""
        self.assertIn("ROLLBACK_RAN", self._run("rollback\ntrap - ERR\nexit 1\n"))


class RetentionPruneTest(SimpleTestCase):
    """
    The retention-prune pipelines must survive matching nothing.

    This is the defect that failed the first real deployment. The staging
    directory had just been created and was empty, so the glob matched nothing,
    `ls` exited 2, `set -o pipefail` adopted that as the pipeline status and
    `set -e` aborted the deployment.

    It is a first-run-only bug, which is why every static check and every
    earlier test passed: on a host that already had backups the globs matched
    and the same lines worked fine. Nothing exercised the empty case.
    """

    def setUp(self):
        self.raw = DEPLOY.read_text(encoding="utf-8")
        with open(DEPLOY, encoding="utf-8") as fh:
            wf = yaml.safe_load(fh)
        self.script = "\n".join(
            (s.get("with") or {}).get("script") or s.get("run") or ""
            for job in ("gate", "deploy")
            for s in wf["jobs"][job]["steps"]
        )

    def _prune_lines(self):
        return [
            line.strip() for line in self.script.splitlines()
            if "ls -1t" in line and "xargs -r rm" in line
        ]

    def test_the_workflow_still_prunes_something(self):
        self.assertTrue(self._prune_lines(), "no retention pruning found at all")

    def test_every_prune_tolerates_a_glob_that_matches_nothing(self):
        for line in self._prune_lines():
            self.assertIn(
                "|| true", line,
                "an unguarded `ls` on an empty glob exits 2, and under "
                f"pipefail that aborts the deployment: {line}",
            )

    def test_the_guard_covers_the_ls_only(self):
        """
        `|| true` must sit inside the braces around `ls`. Applied to the whole
        pipeline it would also swallow a genuine `rm` failure, which is a real
        problem quietly ignored rather than a harmless empty directory.
        """
        for line in self._prune_lines():
            self.assertTrue(
                line.startswith("{ ls -1t") and "|| true; }" in line,
                f"the tolerance must be scoped to the ls, not the pipeline: {line}",
            )
            self.assertFalse(
                line.rstrip().endswith("|| true"),
                f"the whole pipeline is tolerated, hiding rm failures: {line}",
            )

    def test_pruning_still_keeps_a_bounded_number(self):
        for line in self._prune_lines():
            self.assertRegex(line, r"tail -n \+\d+", f"no retention bound: {line}")


class PruneShellSemanticsTest(SimpleTestCase):
    """
    The shell behaviour behind the bug, pinned by running bash — the same
    approach used for the ERR-trap semantics, and for the same reason: this was
    an assumption, it was wrong, and nothing executable was checking it.
    """

    BASH = shutil.which("bash")

    def setUp(self):
        if not self.BASH:
            self.skipTest("bash is not available on this machine")

    def _run(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            empty = pathlib.Path(tmp) / "empty"
            empty.mkdir()
            script = pathlib.Path(tmp) / "t.sh"
            script.write_text(
                "#!/bin/bash\nset -Eeuo pipefail\n"
                + body.replace("@DIR@", str(empty).replace("\\", "/"))
                + '\necho REACHED_END\n',
                encoding="utf-8", newline="\n",
            )
            proc = subprocess.run(
                [self.BASH, str(script)], capture_output=True, text=True, timeout=30
            )
        return proc.stdout

    def test_an_unguarded_prune_aborts_on_an_empty_directory(self):
        """The bug, reproduced."""
        out = self._run(
            "ls -1t @DIR@/docker-compose.*.yml 2>/dev/null | tail -n +6 | xargs -r rm --"
        )
        self.assertNotIn("REACHED_END", out)

    def test_the_guarded_prune_continues(self):
        """The fix, in miniature."""
        out = self._run(
            "{ ls -1t @DIR@/docker-compose.*.yml 2>/dev/null || true; } "
            "| tail -n +6 | xargs -r rm --"
        )
        self.assertIn("REACHED_END", out)
