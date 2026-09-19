"""
Tests for the P6 backup and restore scripts.

Most of what these scripts do needs a host with Docker, restic and a populated
repository, and that end-to-end proof is the restore drill, which runs on the
server. What can be pinned down here is the part that decides whether a real
restore destroys anything: the guard that classifies a restore target, the
refusal to point the drill at production, and the invariants that must not
quietly disappear from the scripts during a later edit.

The guard is tested by running it, not by reading it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKUP_DIR = REPO / "deploy" / "backup"

BACKUP = BACKUP_DIR / "matemail-backup.sh"
RESTORE = BACKUP_DIR / "matemail-restore.sh"
RESTORE_MAILBOX = BACKUP_DIR / "matemail-restore-mailbox.sh"
GUARD = BACKUP_DIR / "lib" / "guard.sh"
INSTALL = BACKUP_DIR / "install.sh"
ENV_EXAMPLE = BACKUP_DIR / "backup.env.example"
DOC = REPO / "docs" / "BACKUP_RESTORE.md"

SCRIPTS = [BACKUP, RESTORE, RESTORE_MAILBOX, INSTALL, GUARD]

BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash is not available")


def test_this_environment_can_actually_run_the_script_tests():
    """
    Twelve tests here drive the real scripts through bash. Without bash they
    skip, and a skip is how this whole file spent four phases reporting nothing
    at all. Locally that is a fair trade; on CI it is the bug, so there the
    absence fails instead.
    """
    if os.environ.get("CI") != "true":
        pytest.skip("local run; the guarantee is only meaningful on CI")
    assert BASH, "CI must provide bash, or twelve script tests silently vanish"


def run_bash(script: str, *, env: dict | None = None, cwd: Path | None = None):
    return subprocess.run(
        [BASH, "-c", script],
        capture_output=True, text=True, env=env, cwd=str(cwd) if cwd else None,
    )


def sh_path(p: Path) -> str:
    """A path bash on this machine will accept, on Windows as well as Linux."""
    s = p.as_posix()
    if re.match(r"^[A-Za-z]:/", s):
        s = "/" + s[0].lower() + s[2:]
    return s


# ─── The scripts exist and parse ────────────────────────────────────────────

def test_every_script_is_present():
    for script in SCRIPTS:
        assert script.is_file(), f"{script.relative_to(REPO)} is missing"


@needs_bash
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_parses(script):
    result = run_bash(f"bash -n {sh_path(script)}")
    assert result.returncode == 0, result.stderr


# ─── Invariants that must not quietly disappear ─────────────────────────────

@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_fail_fast_and_stay_private(script):
    body = script.read_text()
    if script is not GUARD:  # a sourced library must not set the caller's flags
        assert "set -euo pipefail" in body, f"{script.name} does not fail fast"
        assert "umask 077" in body, f"{script.name} does not restrict its umask"


def test_backup_runs_one_at_a_time():
    body = BACKUP.read_text()
    assert "flock -n 9" in body, "concurrent runs would corrupt each other's staging"


def test_backup_wipes_its_staging_on_every_exit():
    body = BACKUP.read_text()
    assert "trap cleanup EXIT" in body
    assert 'rm -rf "$STAGE_ROOT"' in body


def test_backup_stages_secrets_only_in_tmpfs():
    """
    Database dumps and DKIM keys are unencrypted while they are being staged.
    They may live in /run, which is tmpfs and root-only, and nowhere else.
    """
    body = BACKUP.read_text()
    assert "STAGE_ROOT=/run/matemail-backup" in body
    assert "/opt/MateMailBackup/stage" not in body


def test_backup_validates_each_dump_before_trusting_it():
    """
    A backup that captured 40 bytes of an error message must not be recorded as
    a success, so the dump has to parse as a PostgreSQL archive.
    """
    body = BACKUP.read_text()
    assert "pg_restore --list" in body
    assert "is not a readable PostgreSQL archive" in body


def test_backup_verifies_the_snapshot_it_just_wrote():
    """`restic backup` exiting 0 is not evidence the snapshot holds anything."""
    body = BACKUP.read_text()
    assert "restic ls" in body
    assert "is missing" in body
    assert "restic check" in body


def test_backup_never_puts_database_credentials_on_the_host_command_line():
    body = BACKUP.read_text()
    assert "PGPASSWORD=" not in body
    # `--password-file` takes a path and is the safe form; a bare `--password`
    # would put the secret itself into every process listing on the host.
    assert not re.search(r"--password(?!-file)[ =]", body)
    assert not re.search(r"--from-password(?!-file)[ =]", body)
    # The database credentials are read inside the container, from its own
    # environment, and never cross onto the host command line at all.
    assert 'pg_dump -U "$POSTGRES_USER"' in body


def test_offsite_is_not_hardcoded_to_a_provider():
    env = ENV_EXAMPLE.read_text()
    assert re.search(r"^OFFSITE_REPOSITORY=\s*$", env, re.M), \
        "the example must ship with no provider chosen"
    for provider in ("s3.amazonaws.com", "b2:", "backblaze", "wasabi"):
        assert provider not in BACKUP.read_text()


def test_missing_offsite_is_reported_honestly():
    """
    A repository on the production host is retention, not disaster recovery,
    and the operator has to be told so rather than left to assume.
    """
    body = BACKUP.read_text()
    assert "not disaster recovery" in body
    assert '"offsite": bool' in body or "bool(os.environ.get(\"OFFSITE_REPOSITORY\"))" in body


def test_prune_is_scoped_to_the_dedicated_repository():
    body = BACKUP.read_text()
    prune_lines = [ln for ln in body.splitlines() if "--prune" in ln]
    assert prune_lines, "retention never prunes"
    for line in prune_lines:
        assert "restic forget" in line or "restic" in body


def test_install_never_overwrites_an_existing_repository_password():
    """Overwriting it would make every existing snapshot unreadable forever."""
    body = INSTALL.read_text()
    assert 'if [ -e "$DEST/restic-password" ]' in body
    assert "keeping it" in body


def test_install_never_prints_the_password():
    body = INSTALL.read_text()
    assert "cat " not in body.replace("cat backup.env", "")
    assert not re.search(r"echo .*\$\(.*restic-password", body)


# ─── The restore guard, tested by running it ────────────────────────────────

@needs_bash
def _state(tmp_path: Path, build) -> str:
    target = tmp_path / "mailbox"
    build(target)
    result = run_bash(
        f'. {sh_path(GUARD)}; mailbox_target_state "{sh_path(target)}"'
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@needs_bash
def test_guard_reports_absent_when_there_is_no_mailbox(tmp_path):
    assert _state(tmp_path, lambda t: None) == "absent"


@needs_bash
def test_guard_reports_empty_for_a_provisioned_but_unused_mailbox(tmp_path):
    def build(t: Path):
        for sub in ("cur", "new", "tmp"):
            (t / sub).mkdir(parents=True)
    assert _state(tmp_path, build) == "empty"


@needs_bash
def test_guard_reports_active_when_the_mailbox_holds_mail(tmp_path):
    def build(t: Path):
        (t / "cur").mkdir(parents=True)
        (t / "cur" / "1700000000.M1P1.host,S=42:2,S").write_text("a message")
    assert _state(tmp_path, build) == "active"


@needs_bash
def test_guard_reports_active_for_mail_in_a_subfolder(tmp_path):
    """
    A mailbox whose INBOX is empty but whose .Archive holds ten years of mail is
    active. Only looking at the top level would let that be overwritten.
    """
    def build(t: Path):
        (t / "cur").mkdir(parents=True)
        (t / ".Archive" / "cur").mkdir(parents=True)
        (t / ".Archive" / "cur" / "1700000000.M2P2.host,S=9:2,S").write_text("old")
    assert _state(tmp_path, build) == "active"


@needs_bash
def test_guard_ignores_uncommitted_deliveries_in_tmp(tmp_path):
    """
    tmp/ holds deliveries that have not been committed by the rename into new/,
    so a file there is not yet mail. Counting it would block restores into
    mailboxes that are in fact empty.
    """
    def build(t: Path):
        (t / "cur").mkdir(parents=True)
        (t / "tmp").mkdir(parents=True)
        (t / "tmp" / "1700000000.M3P3.host").write_text("half-delivered")
    assert _state(tmp_path, build) == "empty"


# ─── Refusals, tested by running them ───────────────────────────────────────

@pytest.fixture
def fake_env(tmp_path: Path) -> Path:
    """A backup.env that names nothing real, so the scripts get past loading."""
    env = tmp_path / "backup.env"
    env.write_text(
        "RESTIC_REPOSITORY=/nonexistent/repo\n"
        "RESTIC_PASSWORD_FILE=/nonexistent/pw\n"
        "NATIVE_DIR=/nonexistent\n"
        "NATIVE_DB_SERVICE=db\n"
        "VMAIL_VOLUME=nonexistent_volume\n"
    )
    return env


@needs_bash
@pytest.mark.parametrize(
    "workdir",
    ["/", "/opt/MateMail", "/opt/MateMail/deploy", "/opt/MateMailNative",
     "/var/lib/docker/volumes", "/etc"],
)
def test_drill_refuses_to_aim_at_production(fake_env, workdir):
    result = run_bash(
        f"MATEMAIL_BACKUP_ENV={sh_path(fake_env)} "
        f"{sh_path(RESTORE)} --workdir {workdir}"
    )
    assert result.returncode != 0
    assert "that is production, not a drill" in result.stdout + result.stderr


@needs_bash
def test_drill_accepts_a_real_drill_area(fake_env, tmp_path):
    """
    The refusal must reject production and nothing else; a guard that rejects
    every path would pass the test above while making the drill unrunnable.
    """
    area = tmp_path / "drill"
    result = run_bash(
        f"MATEMAIL_BACKUP_ENV={sh_path(fake_env)} "
        f"{sh_path(RESTORE)} --workdir {sh_path(area)}"
    )
    out = result.stdout + result.stderr
    assert "that is production, not a drill" not in out


@needs_bash
def test_mailbox_restore_rejects_a_non_address(fake_env):
    result = run_bash(
        f"MATEMAIL_BACKUP_ENV={sh_path(fake_env)} "
        f"{sh_path(RESTORE_MAILBOX)} 'not-an-address'"
    )
    assert result.returncode == 64
    assert "not an email address" in result.stdout + result.stderr


@needs_bash
def test_mailbox_restore_requires_an_address(fake_env):
    result = run_bash(
        f"MATEMAIL_BACKUP_ENV={sh_path(fake_env)} {sh_path(RESTORE_MAILBOX)}"
    )
    assert result.returncode == 64


def test_mailbox_restore_defaults_to_not_touching_the_live_mailbox():
    body = RESTORE_MAILBOX.read_text()
    assert 'if [ "$IN_PLACE" -eq 0 ]' in body
    assert ".restored-$STAMP" in body
    assert "The live mailbox was not touched" in body


def test_mailbox_restore_refuses_to_overwrite_an_active_mailbox():
    body = RESTORE_MAILBOX.read_text()
    assert 'if [ "$STATE" = active ] && [ "$FORCE" -eq 0 ]' in body
    assert "Restoring in place would overwrite it" in body


def test_mailbox_restore_never_deletes_the_mailbox_it_replaces():
    """
    Even --force must be survivable: the Maildir is moved aside, not removed.
    """
    body = RESTORE_MAILBOX.read_text()
    assert ".replaced-$STAMP" in body
    assert 'mv "$LIVE_PATH" "$ASIDE"' in body
    assert 'rm -rf "$LIVE_PATH"' not in body


def test_mailbox_restore_rebuilds_the_indexes_that_are_not_backed_up():
    """
    The Dovecot index volume is deliberately excluded. That is only coherent if
    restoring mail rebuilds the indexes for it.
    """
    body = RESTORE_MAILBOX.read_text()
    assert "force-resync" in body


def test_mailbox_restore_resolves_retired_storage():
    """A deleted mailbox's mail is retained by NE4 and must be restorable."""
    body = RESTORE_MAILBOX.read_text()
    assert "retired_mailbox_storage" in body


# ─── The exclusion list is a decision, not an omission ──────────────────────

EXCLUDED_RE = re.compile(r'"(matemail_[a-z_]+)":', re.M)


def _excluded_in_backup() -> set[str]:
    body = BACKUP.read_text()
    block = body.split("excluded_volumes", 1)[1]
    return set(EXCLUDED_RE.findall(block))


def test_every_excluded_volume_carries_a_reason():
    body = BACKUP.read_text()
    block = body.split('"excluded_volumes": {', 1)[1].split("    },", 1)[0]
    entries = re.findall(r'"(matemail_[a-z_]+)":\s*(.+?)(?=\n\s*"matemail|\n\s*\}|$)',
                         block, re.S)
    assert entries, "no exclusions are recorded"
    for name, reason in entries:
        assert len(reason.strip().strip('",')) > 10, f"{name} has no stated reason"


def test_the_documented_exclusions_match_the_ones_the_script_records():
    """
    The manifest's exclusion list and the documentation are two statements of
    the same decision. If they drift, one of them is lying to an operator during
    an incident.
    """
    assert DOC.is_file(), "docs/BACKUP_RESTORE.md is missing"
    documented = set(re.findall(r"`(matemail_[a-z_]+)`", DOC.read_text()))
    recorded = _excluded_in_backup()
    missing = recorded - documented
    assert not missing, f"excluded in the script but undocumented: {sorted(missing)}"


def test_manifest_records_what_a_restore_needs_to_find_things():
    body = BACKUP.read_text()
    for key in ('"roots"', '"staged_files"', '"contents"', '"offsite"',
                '"excluded_volumes"'):
        assert key in body, f"manifest does not record {key}"


def test_restore_drill_verifies_checksums_rather_than_trusting_restic():
    body = RESTORE.read_text()
    assert "checksum mismatch" in body
    assert "staged_files" in body


def test_restore_drill_proves_the_dumps_load_and_hold_the_right_rows():
    body = RESTORE.read_text()
    assert "pg_restore -h 127.0.0.1 -U drill" in body
    assert "manifest said" in body
    assert "schema_version" in body


def test_restore_drill_checks_dkim_keys_are_usable_keys():
    body = RESTORE.read_text()
    assert "openssl rsa" in body or "openssl pkey" in body
    assert "did not restore as usable keys" in body


def test_restore_drill_database_is_unreachable():
    """
    The throwaway server runs with trust auth. That is only acceptable because
    nothing can reach it.
    """
    body = RESTORE.read_text()
    assert "--network none" in body
    assert "POSTGRES_HOST_AUTH_METHOD=trust" in body


# ─── Regressions ────────────────────────────────────────────────────────────

def test_manifest_counts_are_not_built_with_sql_string_concatenation():
    """
    Regression. The first version counted rows with `count(*)||':'||count(*)`
    written as `||\":\"||` through two layers of shell quoting. In PostgreSQL
    `":"` is a quoted IDENTIFIER, so every query failed with `column ":" does
    not exist`, the error was sent to /dev/null, and the manifest recorded "?"
    for every count. Backups looked perfect and recorded nothing, and the
    restore drill had nothing to check the restored rows against.

    psql's own field separator has no quoting problem to get wrong.
    """
    body = BACKUP.read_text()
    assert "psql -qtAX -F:" in body, "counts must use psql's field separator"
    assert '||\":\"||' not in body
    assert "||':'||" not in body


def test_failing_to_count_rows_fails_the_backup():
    """
    Regression. The counts were previously wrapped in `2>/dev/null || echo
    "?:?:?"`, so the only consequence of the broken query was a silent manifest.
    If the manifest cannot say what was captured, nothing downstream can verify
    the restore, and that has to be a failure rather than a shrug.
    """
    body = BACKUP.read_text()
    assert 'die "could not count $label for the manifest"' in body
    assert '"?:?:?"' not in body
    assert '"?:?:?:?"' not in body


def test_drill_fails_when_a_snapshot_cannot_be_verified():
    """
    Regression. The drill logged "manifest recorded no expectation" and carried
    on to report PASS. A drill that passes without comparing anything is the
    failure mode this whole phase exists to remove.
    """
    body = RESTORE.read_text()
    assert "cannot be verified" in body
    assert "manifest recorded no expectation for" not in body.replace(
        "the manifest recorded no expectation, so this snapshot cannot be verified", "")


@pytest.mark.parametrize("script", [BACKUP, RESTORE_MAILBOX], ids=lambda p: p.name)
def test_container_commands_do_not_steal_the_callers_stdin(script):
    """
    Regression. `docker compose exec -T` inherits stdin, so a run invoked from a
    heredoc or a pipe silently consumed the rest of its caller's input. Every
    such call that is not deliberately being fed a stream gets `< /dev/null`.
    """
    body = script.read_text()
    calls = body.count("docker compose exec -T")
    guards = body.count("< /dev/null")
    assert calls > 0
    assert guards == calls, f"{calls} container call(s) but {guards} stdin guard(s)"


def test_backup_requires_the_dkim_volume_to_already_exist():
    """
    Regression, found by the failure tests. `docker run -v name:/path` CREATES a
    named volume that does not exist instead of failing, so pointing the backup
    at a renamed or lost DKIM volume produced a snapshot that was reported as
    completely successful and contained no keys whatsoever.
    """
    body = BACKUP.read_text()
    assert 'docker volume inspect "$DKIM_VOLUME"' in body
    inspect_at = body.index('docker volume inspect "$DKIM_VOLUME"')
    mount_at = body.index('-v "$DKIM_VOLUME":/src:ro')
    assert inspect_at < mount_at, "existence must be checked before mounting"


def test_backup_records_the_dkim_key_count_and_the_drill_checks_it():
    assert '"keys": int(os.environ["DKIM_KEYS"])' in BACKUP.read_text()
    drill = RESTORE.read_text()
    assert "WANT_KEYS=" in drill
    assert "restored usably" in drill


def test_drill_waits_for_the_real_database_not_the_initdb_one():
    """
    Regression. The postgres image runs a temporary server during initdb with
    TCP disabled. A `pg_isready` probe on the unix socket answers during that
    phase, so the drill proceeded while the server was about to restart and
    `createdb` failed with "No such file or directory". The first drill passed
    only by winning the race, which is the worst outcome of the three.
    """
    body = RESTORE.read_text()
    assert "pg_isready -h 127.0.0.1" in body, "readiness must be probed over TCP"
    assert "pg_isready -U drill" not in body, "no socket-only readiness probe may remain"
    assert '[ "$READY" -ge 2 ]' in body, "one success is not enough to rule out the race"


def test_drill_clients_use_the_connection_they_waited_on():
    body = RESTORE.read_text()
    for cmd in ("createdb -h 127.0.0.1", "pg_restore -h 127.0.0.1", "psql -h 127.0.0.1"):
        assert cmd in body, f"{cmd} must use the same TCP connection as the probe"


def test_backup_refuses_to_run_without_a_restic_cache():
    """
    Regression, found by running the unit rather than the script. systemd units
    have no HOME, so restic fell back to no cache at all and logged "running
    prune without a cache, this may be very slow!". Correct, and fine at a
    megabyte; once there is real mail in the repository it degrades the nightly
    prune into a full walk, which is noticed months later as "backups take all
    night now".
    """
    assert "RESTIC_CACHE_DIR" in ENV_EXAMPLE.read_text()
    body = BACKUP.read_text()
    assert '[ -n "${RESTIC_CACHE_DIR:-}" ]' in body


def test_install_adds_the_cache_setting_to_an_existing_config():
    """
    install.sh keeps a local backup.env, so an upgrade would otherwise leave the
    timer running cacheless forever.
    """
    body = INSTALL.read_text()
    line = next(l for l in body.splitlines()
                if "RESTIC_CACHE_DIR" in l and "grep -q" in l)
    # The append must be conditional on one line; a broken line continuation
    # here would append a duplicate key on every single install.
    assert line.rstrip().endswith('>> "$DEST/backup.env"')
    assert "||" in line
    assert chr(92) + "n" not in line


def test_the_restic_cache_is_root_only():
    """It holds decrypted index and tree metadata, including file names."""
    body = INSTALL.read_text()
    assert 'chmod 700 "${CACHE:-/var/cache/restic}"' in body
    assert 'chown root:root "${CACHE:-/var/cache/restic}"' in body
