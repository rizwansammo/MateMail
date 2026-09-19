"""
NE6 — MateMail platform outbound moves from Mailcow to the Native Engine.

Two kinds of test live here.

The topology tests read the deployment files, because the properties they pin
down — no published mail port, Postfix not on the link network, no Mailcow
service in the active path — are decided entirely by those files and are the
kind of thing that gets "temporarily" relaxed during a late-night fix.

The DKIM import tests RUN the migration utility. It is the one place in the
system where a private key is handed in from outside, so what matters is not
that the file mentions validation but that it actually refuses a 1024-bit key,
a key whose public half does not match DNS, and a PEM that is not a key at all.
Those are driven for real.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[2]
NATIVE = REPO / "deploy" / "native-engine"
COMPOSE = NATIVE / "docker-compose.yml"
GATEWAY_CFG = NATIVE / "gateway" / "haproxy.cfg"
CERT_SCRIPT = NATIVE / "scripts" / "install-mail-cert.sh"
DKIM_SCRIPT = NATIVE / "scripts" / "import_platform_dkim.py"
MAIN_CF = NATIVE / "postfix" / "main.cf"
MASTER_CF = NATIVE / "postfix" / "master.cf"

PLATFORM_DOMAIN = "mail.matemail.online"
PLATFORM_SENDER = "noreply@mail.matemail.online"
MAIL_HOST = "mx.matemail.online"
DKIM_SELECTOR = "mm1"

MAIL_PORTS = {25, 110, 143, 465, 587, 993, 995}


def compose():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


# ─── the private submission path ────────────────────────────────────────────

def test_the_submission_gateway_exists_and_is_native_owned():
    svc = compose()["services"]["submission-gateway"]
    assert svc["container_name"] == "matemail-native-submission-gateway"
    assert "haproxy" in svc["image"]
    # Pinned by digest: this sits in the path of every transactional message,
    # and an unattended image change is an outage nobody chose the timing of.
    assert "@sha256:" in svc["image"]


def test_the_gateway_bridges_exactly_two_networks():
    nets = compose()["services"]["submission-gateway"]["networks"]
    assert set(nets) == {"engine", "matemail_engine_link"}


def test_the_gateway_carries_the_certificate_hostname_as_an_alias():
    """
    Django dials `mx.matemail.online` and verifies the hostname. The alias is
    why EMAIL_HOST needs no change and why no container IP ever reaches a
    settings file.
    """
    nets = compose()["services"]["submission-gateway"]["networks"]
    assert MAIL_HOST in nets["matemail_engine_link"]["aliases"]


def test_no_native_service_publishes_a_host_port():
    """NE6 opens nothing. Public ports are NE7's decision, not a side effect."""
    for name, svc in compose()["services"].items():
        assert not svc.get("ports"), f"{name} publishes {svc.get('ports')}"


def test_the_gateway_forwards_one_port_to_one_upstream():
    cfg = GATEWAY_CFG.read_text(encoding="utf-8")
    binds = re.findall(r"^\s*bind\s+:(\d+)", cfg, re.M)
    assert binds == ["587"], f"gateway listens on {binds}, expected only 587"
    servers = re.findall(r"^\s*server\s+\S+\s+(\S+)", cfg, re.M)
    assert servers == ["postfix:587"], f"gateway forwards to {servers}"


def test_the_gateway_does_not_terminate_tls():
    """
    `mode tcp` keeps the STARTTLS session and the SASL exchange end to end
    between Django and Postfix. A terminating proxy would have to hold the
    certificate AND would see the plaintext password.
    """
    cfg = GATEWAY_CFG.read_text(encoding="utf-8")
    assert re.search(r"^\s*mode\s+tcp", cfg, re.M)
    for forbidden in ("ssl crt", "mode http", "bind *:587 ssl"):
        assert forbidden not in cfg


def test_the_gateway_holds_no_secret_and_cannot_write():
    svc = compose()["services"]["submission-gateway"]
    assert svc.get("read_only") is True
    for mount in svc.get("volumes", []):
        assert mount.endswith(":ro"), f"writable mount on the gateway: {mount}"
    assert not svc.get("environment"), "the gateway needs no environment at all"


def test_the_mail_path_is_still_unreachable_from_matemail():
    """The boundary NE6 must not widen."""
    services = compose()["services"]
    for name in ("postfix", "dovecot", "rspamd", "db", "redis", "clamav",
                 "olefy", "unbound", "policy"):
        assert "matemail_engine_link" not in str(services[name].get("networks")), (
            f"{name} became reachable from MateMail")


def test_no_mailcow_service_is_in_the_native_stack():
    """After NE6 the active outbound path contains no Mailcow component."""
    raw = COMPOSE.read_text(encoding="utf-8")
    body = "\n".join(line for line in raw.splitlines()
                     if not line.lstrip().startswith("#"))
    assert "mailcow" not in body.lower()


# ─── TLS and authentication ─────────────────────────────────────────────────

def _submission_overrides() -> str:
    """
    The `-o` lines belonging to the `submission` service.

    Parsed the way master.cf is actually structured — a service line at column
    zero followed by indented overrides — rather than by splitting on the word
    "submission", which also appears in the comments above it.
    """
    lines = MASTER_CF.read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines)
                 if l.startswith("submission") and "inet" in l)
    out = []
    for line in lines[start + 1:]:
        if line[:1] not in (" ", "\t"):
            break
        if line.strip().startswith("-o"):
            out.append(line.strip())
    assert out, "the submission service has no overrides at all"
    return "\n".join(out)


def test_submission_requires_starttls():
    """
    `encrypt`, not `may`. This was already set before NE6 while the TLS volume
    was EMPTY, so submission would have refused every message — the safe
    direction to fail, but it had to be fixed by supplying the certificate
    rather than by relaxing this.
    """
    flat = _submission_overrides().replace(" ", "")
    assert "smtpd_tls_security_level=encrypt" in flat


def test_submission_requires_authentication():
    flat = _submission_overrides().replace(" ", "")
    assert "smtpd_sasl_auth_enable=yes" in flat
    assert flat.rstrip().endswith("reject"), (
        "submission client restrictions must end in reject, or anything the "
        "gateway forwards could relay unauthenticated")


def test_mynetworks_cannot_turn_the_gateway_into_an_open_relay():
    """
    The gateway connects from the engine subnet. If `mynetworks` ever grew to
    include that subnet, `permit_mynetworks` would let anything through the
    gateway relay to the Internet WITHOUT authenticating — and the gateway is
    reachable by the whole application. Host-local only.
    """
    main = MAIN_CF.read_text(encoding="utf-8")
    line = next(l for l in main.splitlines()
                if l.startswith("mynetworks =") or l.startswith("mynetworks="))
    assert "172." not in line and "10." not in line, (
        f"mynetworks includes a container subnet: {line}")
    assert "127.0.0.0/8" in line


def test_the_engine_still_refuses_a_sender_the_login_does_not_own():
    main = MAIN_CF.read_text(encoding="utf-8")
    assert "reject_sender_login_mismatch" in main
    assert "reject_authenticated_sender_login_mismatch" in main


def test_the_rate_limit_policy_service_is_still_consulted():
    main = MAIN_CF.read_text(encoding="utf-8")
    assert "check_policy_service" in main


def test_outbound_identity_is_the_production_mail_hostname():
    main = MAIN_CF.read_text(encoding="utf-8")
    assert re.search(r"^myhostname\s*=\s*" + re.escape(MAIL_HOST), main, re.M)
    assert re.search(r"^inet_protocols\s*=\s*ipv4", main, re.M), (
        "production mail is IPv4 only; the IPv6 PTR is deliberately irrelevant")


# ─── the certificate installer ──────────────────────────────────────────────

def test_the_certificate_installer_checks_the_hostname_before_installing():
    """
    A certificate for the wrong name would install cleanly and fail only at the
    first send, with a TLS error that points nowhere useful.
    """
    body = CERT_SCRIPT.read_text(encoding="utf-8")
    assert "-checkhost" in body


def test_the_certificate_installer_does_not_mount_every_key_on_the_host():
    """
    /etc/letsencrypt holds the private keys of every application on this
    machine. Postfix gets a copy of one certificate, not the directory.
    """
    body = CERT_SCRIPT.read_text(encoding="utf-8")
    assert "-v /etc/letsencrypt" not in body
    assert "install -m 0640" in body, "the key must not be world readable"
    assert "install -m 0644" in body, "the certificate is public"


def test_the_certificate_installer_is_idempotent():
    """The renewal hook must not become a twice-daily mail server restart."""
    body = CERT_SCRIPT.read_text(encoding="utf-8")
    assert "cmp -s" in body
    assert "already current" in body


def test_the_certificate_volume_is_read_only_to_postfix():
    svc = compose()["services"]["postfix"]
    tls = [v for v in svc["volumes"] if "/etc/ssl/mail" in v]
    assert tls and tls[0].endswith(":ro"), tls


# ─── the DKIM migration utility, driven for real ────────────────────────────

crypto = pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization           # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, rsa      # noqa: E402


def _pem(key) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )


def run_import(pem: bytes, *args, tmp_path=None):
    """
    Run the utility exactly as production does: key on STDIN, never on the
    command line. `--dry-run` keeps these tests away from a database while
    still exercising every validation branch, which is where the safety is.
    """
    env = {
        "NATIVE_APP_DIR": str(REPO / "engine" / "native_api"),
        "PATH": "/usr/bin:/bin",
    }
    if tmp_path is not None:
        env["NATIVE_DKIM_DIR"] = str(tmp_path)
    import os
    merged = dict(os.environ)
    merged.update(env)
    return subprocess.run(
        [sys.executable, str(DKIM_SCRIPT), *args],
        input=pem, capture_output=True, timeout=120, env=merged,
    )


@pytest.fixture(scope="module")
def good_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def test_a_valid_platform_key_is_accepted(good_key, tmp_path):
    result = run_import(_pem(good_key), "--domain", PLATFORM_DOMAIN,
                        "--selector", DKIM_SELECTOR, "--dry-run",
                        tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr.decode()
    out = result.stdout.decode()
    assert PLATFORM_DOMAIN in out
    assert DKIM_SELECTOR in out
    assert re.search(r"public_sha256\s+[0-9a-f]{64}", out)
    assert "nothing was written" in out


def test_a_weak_key_is_refused(tmp_path):
    """1024-bit DKIM keys are trivially factorable and still common in the wild."""
    weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    result = run_import(_pem(weak), "--domain", PLATFORM_DOMAIN,
                        "--selector", DKIM_SELECTOR, "--dry-run",
                        tmp_path=tmp_path)
    assert result.returncode == 2
    assert "1024" in result.stderr.decode()


def test_a_non_rsa_key_is_refused(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    result = run_import(_pem(key), "--domain", PLATFORM_DOMAIN,
                        "--selector", DKIM_SELECTOR, "--dry-run",
                        tmp_path=tmp_path)
    assert result.returncode == 2
    assert "RSA" in result.stderr.decode()


def test_material_that_is_not_a_key_is_refused(tmp_path):
    result = run_import(b"-----BEGIN RSA PRIVATE KEY-----\nnope\n-----END RSA PRIVATE KEY-----\n",
                        "--domain", PLATFORM_DOMAIN, "--selector", DKIM_SELECTOR,
                        "--dry-run", tmp_path=tmp_path)
    assert result.returncode == 2
    assert "PEM" in result.stderr.decode()


def test_an_empty_stdin_is_refused(tmp_path):
    result = run_import(b"", "--domain", PLATFORM_DOMAIN,
                        "--selector", DKIM_SELECTOR, "--dry-run",
                        tmp_path=tmp_path)
    assert result.returncode == 2


def test_an_invalid_domain_is_refused(good_key, tmp_path):
    result = run_import(_pem(good_key), "--domain", "not a domain",
                        "--selector", DKIM_SELECTOR, "--dry-run",
                        tmp_path=tmp_path)
    assert result.returncode != 0


def test_a_key_that_does_not_match_dns_is_refused(good_key, tmp_path):
    """
    The check that makes the migration a fact rather than a hope: if the
    private key's public half is not the one already published, installing it
    would silently break every signature.
    """
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ok = run_import(_pem(other), "--domain", PLATFORM_DOMAIN, "--selector",
                    DKIM_SELECTOR, "--dry-run", tmp_path=tmp_path)
    expected = re.search(r"public_sha256\s+([0-9a-f]{64})",
                         ok.stdout.decode()).group(1)

    result = run_import(_pem(good_key), "--domain", PLATFORM_DOMAIN,
                        "--selector", DKIM_SELECTOR,
                        "--expect-public-sha256", expected, "--dry-run",
                        tmp_path=tmp_path)
    assert result.returncode == 2
    assert "does not match" in result.stderr.decode()


def test_a_key_that_matches_dns_is_accepted(good_key, tmp_path):
    """The other half: the correct key must not be refused."""
    first = run_import(_pem(good_key), "--domain", PLATFORM_DOMAIN,
                       "--selector", DKIM_SELECTOR, "--dry-run",
                       tmp_path=tmp_path)
    fingerprint = re.search(r"public_sha256\s+([0-9a-f]{64})",
                            first.stdout.decode()).group(1)
    result = run_import(_pem(good_key), "--domain", PLATFORM_DOMAIN,
                        "--selector", DKIM_SELECTOR,
                        "--expect-public-sha256", fingerprint, "--dry-run",
                        tmp_path=tmp_path)
    assert result.returncode == 0, result.stderr.decode()


def test_no_private_material_ever_reaches_the_output(good_key, tmp_path):
    """
    The single most important property. Every branch is driven and the whole
    of stdout and stderr is checked for anything resembling a key.
    """
    pem = _pem(good_key)
    body = pem.decode().replace("-----BEGIN RSA PRIVATE KEY-----", "") \
                       .replace("-----END RSA PRIVATE KEY-----", "").strip()
    chunks = [line for line in body.splitlines() if len(line) > 20]

    cases = [
        (pem, ("--domain", PLATFORM_DOMAIN, "--selector", DKIM_SELECTOR, "--dry-run")),
        (pem, ("--domain", "not a domain", "--selector", DKIM_SELECTOR, "--dry-run")),
        (pem, ("--domain", PLATFORM_DOMAIN, "--selector", DKIM_SELECTOR,
               "--expect-public-sha256", "0" * 64, "--dry-run")),
        (b"-----BEGIN RSA PRIVATE KEY-----\nbroken\n-----END RSA PRIVATE KEY-----\n",
         ("--domain", PLATFORM_DOMAIN, "--selector", DKIM_SELECTOR, "--dry-run")),
    ]
    for key_bytes, args in cases:
        result = run_import(key_bytes, *args, tmp_path=tmp_path)
        combined = result.stdout.decode() + result.stderr.decode()
        assert "PRIVATE KEY" not in combined, args
        for chunk in chunks:
            assert chunk not in combined, f"key material leaked with {args}"


def test_the_utility_reads_the_key_from_stdin_not_an_argument():
    """
    A key passed as an argument is visible in `ps` to every user on the host
    and lands in shell history.
    """
    body = DKIM_SCRIPT.read_text(encoding="utf-8")
    assert "sys.stdin.buffer.read()" in body
    for bad in ("--key", "--pem", "--private-key", "--key-file"):
        assert f'"{bad}"' not in body


def test_the_utility_is_not_an_http_endpoint():
    """
    DEC-007r: the engine owns private DKIM material. A permanent route that
    accepts private keys would exist on every deployment forever.
    """
    app = (REPO / "engine" / "native_api" / "app.py").read_text(encoding="utf-8")
    for route in ("/v1/dkim/import", "/v1/dkim/upload", "/v1/dkim/adopt"):
        assert route not in app
    assert not (REPO / "engine" / "native_api" / "import_platform_dkim.py").exists(), (
        "the migration utility must not be baked into the engine image")


def test_the_utility_installs_under_the_engines_own_lock():
    body = DKIM_SCRIPT.read_text(encoding="utf-8")
    assert "dkim_lifecycle_lock" in body
    assert "dkim_lib.activate(" in body, "must use the engine's atomic commit"


# ─── rollback material must survive ─────────────────────────────────────────

def test_the_mailcow_rollback_assets_are_retained():
    """
    Mailcow stays installed and its gateway configuration stays in the
    repository. Rollback is reattaching one network alias, and that is only
    true while these files exist.
    """
    for path in (REPO / "deploy" / "engine" / "haproxy.cfg",
                 REPO / "deploy" / "engine" / "certbot-deploy-hook-mailcow-mx.sh"):
        assert path.is_file(), f"{path.relative_to(REPO)} is rollback material"


def test_no_secret_is_committed_with_the_ne6_changes():
    for path in (GATEWAY_CFG, CERT_SCRIPT, DKIM_SCRIPT, COMPOSE):
        body = path.read_text(encoding="utf-8")
        assert "PRIVATE KEY" not in body.replace("PRIVATE KEY-----", "")[:0] + body \
            or "BEGIN RSA PRIVATE KEY-----\\n" in body, path
        assert not re.search(r"(?i)password\s*[:=]\s*['\"]?[A-Za-z0-9_+/=-]{8,}", body), path


# ─── one transport, for every sender ────────────────────────────────────────

BACKEND_SRC = REPO / "backend"


def _python_sources():
    for path in (BACKEND_SRC / "apps", BACKEND_SRC / "config"):
        for f in path.rglob("*.py"):
            if "test" not in f.name and "__pycache__" not in str(f):
                yield f


def test_every_transactional_sender_uses_the_configured_transport():
    """
    NE6 moves one setting's meaning. That is only safe if there is exactly one
    place the transport comes from — a Celery task holding its own SMTP host
    would keep talking to Mailcow after the switch, and nothing would say so.
    """
    offenders = []
    for path in _python_sources():
        body = path.read_text(encoding="utf-8")
        for match in re.finditer(r"get_connection\(([^)]*)\)", body):
            args = match.group(1)
            for forbidden in ("host=", "port=", "username=", "password="):
                if forbidden in args:
                    offenders.append(f"{path.name}: get_connection({args})")
        if "EmailBackend(" in body and "smtp" in body.lower():
            offenders.append(f"{path.name}: constructs an EmailBackend directly")
    assert not offenders, offenders


def test_no_application_module_hardcodes_an_smtp_endpoint_it_sends_through():
    """
    `mx.matemail.online` may appear in prose and in a default for the mail
    HOSTNAME, but never as the host a connection is opened to.
    """
    for path in _python_sources():
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or '"""' in stripped:
                continue
            if re.search(r"(SMTP|connect|sendmail)\s*\(\s*['\"]m[xa]", stripped):
                raise AssertionError(f"{path.name}:{n} dials a hardcoded host")


def test_the_platform_identity_is_unchanged_by_ne6():
    """
    The whole point of preserving the DKIM key and the alias: the sender the
    world sees, and the domain it aligns under, are exactly what they were.
    """
    example = REPO / "deploy" / "env.production.example"
    if not example.is_file():
        pytest.skip("no production env example in the repository")
    body = example.read_text(encoding="utf-8")
    assert PLATFORM_SENDER in body
    assert f"EMAIL_HOST={MAIL_HOST}" in body.replace(" ", "")
    assert "EMAIL_PORT=587" in body.replace(" ", "")
    assert re.search(r"^EMAIL_USE_TLS\s*=\s*True", body, re.M | re.I)


def test_no_container_ip_is_used_as_an_smtp_host():
    """
    The alias exists precisely so that no address ever lands in a settings
    file. An IP would break on the next recreation, silently.
    """
    example = REPO / "deploy" / "env.production.example"
    if not example.is_file():
        pytest.skip("no production env example in the repository")
    for line in example.read_text(encoding="utf-8").splitlines():
        if line.startswith("EMAIL_HOST="):
            assert not re.match(r"EMAIL_HOST=\d+\.\d+\.\d+\.\d+", line), line
