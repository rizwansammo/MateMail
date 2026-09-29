"""
The executable bit on every shell script in the repository.

WHY THIS EXISTS
    `deploy/backup/*.sh` were committed 100644. Four of them are invoked by
    path — systemd's ExecStart, the operator commands in the runbook, and the
    drill tests — so on Linux the kernel refused them and bash answered 126,
    "Permission denied". Eight tests went red the first time they ran on a
    Linux runner.

    It survived every local run because this repository is developed on Windows
    with `core.fileMode=false`. There, `os.access(path, os.X_OK)` is True for
    every file and Git Bash will execute anything, so the committed mode is
    invisible until a Linux checkout. That is why these assertions read the
    mode Git has RECORDED rather than the mode the filesystem reports: the
    recorded one is what a deployment or a CI runner actually receives.

WHY IT IS NOT "chmod +x everything"
    An executable bit is a statement about how a file is invoked, and half of
    these scripts are invoked in a way that makes it wrong:

      - `lib/guard.sh` is sourced, and its installer places it 0600.
      - the two container entrypoints get their mode from
        `COPY --chown=0:0 --chmod=0755` in the Dockerfile.
      - the certbot hook is placed by `install -m 750` at a different path.
      - two scripts are documented as `sudo bash scripts/...`.

    So each script is listed below with the reason, and a script that is in
    neither list fails the inventory test. A new one cannot be added without
    the question being answered.
"""

from __future__ import annotations

import pathlib
import subprocess

from django.test import SimpleTestCase

REPO = pathlib.Path(__file__).resolve().parents[2]


# Something executes these by path, with no chmod in between.
MUST_BE_EXECUTABLE = {
    "deploy/backup/install.sh":
        "operator entry point, run from a copy of the directory on the server",
    "deploy/backup/matemail-backup.sh":
        "systemd ExecStart=/opt/MateMailBackup/matemail-backup.sh",
    "deploy/backup/matemail-restore.sh":
        "docs/BACKUP_RESTORE.md invokes it by absolute path during an incident",
    "deploy/backup/matemail-restore-mailbox.sh":
        "docs/BACKUP_RESTORE.md invokes it by absolute path during an incident",
    "deploy/monitoring/install.sh":
        "operator entry point, run from a copy of the directory on the server",
    "deploy/native-engine/deploy.sh":
        "the README's deployment procedure is literally `./deploy.sh`",
    "deploy/native-engine/scripts/install-abuse-protection.sh":
        "operator entry point; deployed 755 on MateServer",
    "deploy/native-engine/scripts/install-mail-cert.sh":
        "operator entry point; deployed 755 on MateServer",
    "deploy/native-engine/scripts/mail-log-shipper.sh":
        "systemd ExecStart, and the installer's own chmod is a self-copy that "
        "cannot set it (source and destination are the same path)",
}


# These are invoked in a way that supplies the mode, or never executed at all.
MUST_NOT_BE_EXECUTABLE = {
    "deploy/backup/lib/guard.sh":
        "sourced, never executed; install.sh places it 0600 deliberately",
    "deploy/native-engine/images/dovecot/entrypoint.sh":
        "Dockerfile COPY --chmod=0755",
    "deploy/native-engine/images/postfix/entrypoint.sh":
        "Dockerfile COPY --chmod=0755",
}


def git_modes(pattern: str) -> dict[str, str]:
    """The modes Git has recorded, which is the only copy that decides anything."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-s", "--", pattern],
            cwd=REPO, capture_output=True, text=True,
        )
    except FileNotFoundError:
        raise AssertionError(
            "git is not on PATH, so the committed file modes cannot be read. "
            "This check fails rather than skips: a mode that is wrong in Git "
            "is invisible everywhere else."
        ) from None
    if result.returncode != 0:
        raise AssertionError(f"git ls-files failed: {result.stderr}")

    modes = {}
    for line in result.stdout.splitlines():
        meta, path = line.split("\t", 1)
        modes[path] = meta.split()[0]
    return modes


class ShellScriptModesTest(SimpleTestCase):
    def setUp(self):
        self.modes = git_modes("*.sh")
        self.assertTrue(self.modes, "no shell scripts are tracked; the glob is wrong")

    def test_every_shell_script_is_classified(self):
        """
        The inventory. Without this, a new script added tomorrow inherits
        whatever mode the author's filesystem happened to report, and nothing
        notices until it is executed on a server.
        """
        classified = set(MUST_BE_EXECUTABLE) | set(MUST_NOT_BE_EXECUTABLE)
        unclassified = sorted(set(self.modes) - classified)
        self.assertEqual(
            [], unclassified,
            "these shell scripts are in neither list. Decide how each one is "
            "invoked and add it to MUST_BE_EXECUTABLE or "
            "MUST_NOT_BE_EXECUTABLE in this file.",
        )

        stale = sorted(classified - set(self.modes))
        self.assertEqual(
            [], stale, "these are listed here but no longer tracked by Git",
        )

    def test_scripts_executed_by_path_are_committed_executable(self):
        wrong = {
            path: f"{self.modes.get(path)} — {why}"
            for path, why in MUST_BE_EXECUTABLE.items()
            if self.modes.get(path) != "100755"
        }
        self.assertEqual(
            {}, wrong,
            "these are executed by path and must be committed 100755. Record "
            "it with `git update-index --chmod=+x <path>`, which works even on "
            "a filesystem that has no executable bit.",
        )

    def test_the_rest_stay_non_executable(self):
        """
        The other half of the rule, so the fix can never be a blanket
        `chmod +x $(git ls-files '*.sh')` — that would satisfy the test above
        while contradicting an installer, a Dockerfile and two runbooks.
        """
        wrong = {
            path: f"{self.modes.get(path)} — {why}"
            for path, why in MUST_NOT_BE_EXECUTABLE.items()
            if self.modes.get(path) != "100644"
        }
        self.assertEqual({}, wrong, "these must stay 100644")
