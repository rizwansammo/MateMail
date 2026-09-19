"""
NE7 — the Native Engine becomes a public Internet mail server.

The port policy tests read the deployment files, because which ports exist and
where they bind is decided entirely there, and "we'll close it after testing"
is how a plaintext IMAP port survives to production.

The fail2ban filters are tested by RUNNING their regexes against real log lines
copied from this system. A brute-force filter that does not match is the worst
kind of security control: it installs cleanly, reports itself healthy, shows
zero bans, and protects nothing. Checking that the file mentions `SASL` would
prove none of that.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[2]
NATIVE = REPO / "deploy" / "native-engine"
COMPOSE = NATIVE / "docker-compose.yml"
MAIN_CF = NATIVE / "postfix" / "main.cf"
MASTER_CF = NATIVE / "postfix" / "master.cf"
DOVECOT_CONF = NATIVE / "dovecot" / "dovecot.conf"
CERT_SCRIPT = NATIVE / "scripts" / "install-mail-cert.sh"
F2B = NATIVE / "fail2ban"
JAIL = F2B / "jail.local"
ACTION = F2B / "action.d" / "matemail-docker-user.conf"
COLLECTOR = REPO / "deploy" / "monitoring" / "collectors" / "matemail_collector.py"
RULES = REPO / "deploy" / "monitoring" / "prometheus" / "rules" / "matemail.rules.yml"

INTENDED_PUBLIC = {25, 587, 993}
FORBIDDEN = {110, 143, 465, 995}


def compose():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def published_ports():
    """(service, container_port, bind_expression) for every published port."""
    out = []
    for name, svc in compose()["services"].items():
        for mapping in svc.get("ports", []) or []:
            text = str(mapping)
            parts = text.rsplit(":", 2)
            container_port = int(parts[-1])
            bind = parts[0] if len(parts) == 3 else ""
            out.append((name, container_port, bind))
    return out


# ─── port policy ────────────────────────────────────────────────────────────

def test_exactly_the_three_intended_ports_are_published():
    ports = {p for _, p, _ in published_ports()}
    assert ports == INTENDED_PUBLIC, (
        f"published {sorted(ports)}, expected {sorted(INTENDED_PUBLIC)}")


def test_no_forbidden_mail_port_is_published():
    """
    POP3 and POP3S are not offered at all, plaintext IMAP would carry a
    password in clear, and 465 is a deliberate omission.
    """
    ports = {p for _, p, _ in published_ports()}
    assert not (ports & FORBIDDEN), sorted(ports & FORBIDDEN)


def test_public_ports_bind_an_explicit_address_never_every_interface():
    """
    Mailcow publishes the same port NUMBERS on 127.0.0.1 and must keep
    listening, because rollback is switching which one is public. An
    all-interfaces bind would collide or silently shadow it.
    """
    for service, port, bind in published_ports():
        assert bind, f"{service}:{port} publishes with no bind address"
        assert bind != "0.0.0.0", f"{service}:{port} binds every interface"
        assert "NATIVE_PUBLIC_IP" in bind, (
            f"{service}:{port} binds {bind!r} rather than the configured "
            f"production address")


def test_an_unset_public_address_refuses_to_start():
    """
    `${NATIVE_PUBLIC_IP}` unset renders as ":25:25", which Docker reads as
    every interface — the one mistake here that cannot be seen by reading the
    file. `:?` makes it a refusal instead.
    """
    for service, port, bind in published_ports():
        assert ":?" in bind, (
            f"{service}:{port} would fall back to every interface if "
            f"NATIVE_PUBLIC_IP were unset")


def test_the_production_address_is_configuration_not_a_literal():
    raw = COMPOSE.read_text(encoding="utf-8")
    directives = "\n".join(l for l in raw.splitlines()
                           if not l.lstrip().startswith("#"))
    assert not re.search(r"\n\s+-\s+\"?\d+\.\d+\.\d+\.\d+:\d+:\d+", directives), (
        "a literal IP address in a ports mapping")


def test_only_postfix_and_dovecot_publish_anything():
    services = {name for name, _, _ in published_ports()}
    assert services == {"postfix", "dovecot"}, services


# ─── port 25: the Internet MX ───────────────────────────────────────────────

def _service_overrides(name: str) -> str:
    lines = MASTER_CF.read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines)
                 if l.startswith(name) and "inet" in l)
    out = []
    for line in lines[start + 1:]:
        if line[:1] not in (" ", "\t"):
            break
        if line.strip().startswith("-o"):
            out.append(line.strip())
    return "\n".join(out)


def test_port_25_does_not_require_authentication():
    """
    An MX that demands AUTH receives no mail. Inbound is unauthenticated by
    definition; what protects it is the relay policy, not a password.
    """
    overrides = _service_overrides("smtp").replace(" ", "")
    assert "smtpd_sasl_auth_enable=yes" not in overrides
    main = MAIN_CF.read_text(encoding="utf-8")
    assert re.search(r"^smtpd_sasl_auth_enable\s*=\s*no", main, re.M), (
        "AUTH must be off by default; submission turns it on per-service")


def test_port_25_offers_tls_but_does_not_require_it():
    """
    Requiring STARTTLS on 25 silently drops mail from any sender that cannot
    negotiate it. That is an interoperability decision, not a security win —
    the alternative to an unencrypted delivery is no delivery.
    """
    overrides = _service_overrides("smtp").replace(" ", "")
    assert "smtpd_tls_security_level=encrypt" not in overrides
    main = MAIN_CF.read_text(encoding="utf-8")
    assert re.search(r"^smtpd_tls_security_level\s*=\s*may", main, re.M)
    assert re.search(r"^smtpd_tls_cert_file\s*=", main, re.M), (
        "STARTTLS must still be offered")


def test_inbound_relay_is_refused():
    main = MAIN_CF.read_text(encoding="utf-8")
    relay = main.split("smtpd_relay_restrictions", 1)[1].split("\n\n", 1)[0]
    assert "reject_unauth_destination" in relay
    assert relay.rindex("reject_unauth_destination") > relay.rindex(
        "permit_sasl_authenticated"), "reject must come last"


# ─── port 587: submission ───────────────────────────────────────────────────

def test_submission_requires_both_tls_and_authentication():
    overrides = _service_overrides("submission").replace(" ", "")
    assert "smtpd_tls_security_level=encrypt" in overrides
    assert "smtpd_sasl_auth_enable=yes" in overrides
    assert overrides.rstrip().endswith("reject")


def test_authentication_cannot_be_offered_before_tls():
    """
    `smtpd_tls_auth_only` is what stops the AUTH verbs being advertised on a
    plaintext connection. Without it a client can hand over a password before
    any encryption exists.
    """
    main = MAIN_CF.read_text(encoding="utf-8")
    assert re.search(r"^smtpd_tls_auth_only\s*=\s*yes", main, re.M)


def test_sender_ownership_survives_going_public():
    main = MAIN_CF.read_text(encoding="utf-8")
    assert "reject_sender_login_mismatch" in main
    assert "reject_authenticated_sender_login_mismatch" in main
    assert "smtpd_sender_login_maps" in main


def test_the_rate_limiter_still_sees_public_submission():
    main = MAIN_CF.read_text(encoding="utf-8")
    assert "check_policy_service" in main


def test_mynetworks_did_not_grow_when_the_ports_opened():
    """
    The most dangerous single edit available during NE7. Adding the container
    subnet here would make `permit_mynetworks` match the public listeners'
    clients and turn the MX into an open relay.
    """
    main = MAIN_CF.read_text(encoding="utf-8")
    line = next(l for l in main.splitlines() if l.startswith("mynetworks ="))
    assert "172." not in line and "10." not in line and "0.0.0.0" not in line
    assert "127.0.0.0/8" in line


# ─── IMAPS ──────────────────────────────────────────────────────────────────

def _dovecot_directives() -> str:
    return "\n".join(l for l in DOVECOT_CONF.read_text(encoding="utf-8").splitlines()
                     if not l.lstrip().startswith("#"))


def test_imap_requires_tls():
    d = _dovecot_directives()
    assert re.search(r"^ssl\s*=\s*required", d, re.M), (
        "ssl=required forbids authenticating over a plaintext connection")


def test_plaintext_imap_does_not_listen_at_all():
    """
    Not merely unpublished. Leaving 143 bound inside the container would put
    one careless `ports:` entry between a password and the Internet.
    """
    d = _dovecot_directives()
    block = d.split("service imap-login", 1)[1]
    imap = block.split("inet_listener imap ", 1)[1].split("}", 1)[0]
    assert re.search(r"port\s*=\s*0", imap), "plaintext IMAP still listens"


def test_imaps_listens_with_tls_from_the_first_byte():
    d = _dovecot_directives()
    block = d.split("service imap-login", 1)[1]
    imaps = block.split("inet_listener imaps", 1)[1].split("}", 1)[0]
    assert re.search(r"port\s*=\s*993", imaps)
    assert re.search(r"ssl\s*=\s*yes", imaps)


def test_dovecot_uses_the_real_certificate_and_modern_tls():
    d = _dovecot_directives()
    assert "ssl_server_cert_file = /etc/ssl/mail/cert.pem" in d
    assert "ssl_server_key_file = /etc/ssl/mail/key.pem" in d
    assert re.search(r"^ssl_min_protocol\s*=\s*TLSv1\.2", d, re.M), (
        "TLS 1.0 and 1.1 are withdrawn")


def test_dovecot_reads_the_certificate_read_only():
    tls = [v for v in compose()["services"]["dovecot"]["volumes"]
           if "/etc/ssl/mail" in v]
    assert tls and tls[0].endswith(":ro"), tls


def test_certificate_renewal_reloads_both_services():
    """
    Dovecot now serves public TLS from the same files. Reloading only Postfix
    would hand clients an expired certificate for up to ninety days with
    nothing looking wrong on the Postfix side.
    """
    body = CERT_SCRIPT.read_text(encoding="utf-8")
    assert "DOVECOT_CONTAINER" in body
    assert 'reload_service "$POSTFIX_CONTAINER"' in body
    assert 'reload_service "$DOVECOT_CONTAINER"' in body


# ─── abuse protection ───────────────────────────────────────────────────────

def test_bans_are_written_where_docker_traffic_is_actually_filtered():
    """
    The defect this avoids is silent and total. fail2ban's stock actions insert
    into INPUT; traffic to a Docker-published port is DNATed in PREROUTING and
    evaluated in FORWARD, so it never reaches INPUT. A default install would
    run, log bans, show them in `fail2ban-client status`, and block nothing.
    """
    body = ACTION.read_text(encoding="utf-8")
    assert "DOCKER-USER" in body
    assert "-I DOCKER-USER -j f2b-<name>" in body
    assert "actionban" in body and "-j DROP" in body
    jail = JAIL.read_text(encoding="utf-8")
    assert "banaction = matemail-docker-user" in jail


def test_a_typo_does_not_get_anyone_banned():
    jail = JAIL.read_text(encoding="utf-8")
    for match in re.finditer(r"^maxretry\s*=\s*(\d+)", jail, re.M):
        assert int(match.group(1)) >= 3, (
            "a jail that fires on a typo teaches operators to disable it")


def test_bans_expire():
    jail = JAIL.read_text(encoding="utf-8")
    assert re.search(r"^bantime\s*=\s*\d+[mhd]", jail, re.M), (
        "a permanent ban is how a customer's office IP stays blocked forever")


def test_ssh_is_not_jailed():
    """
    An SSH jail is how an operator loses a server at three in the morning, and
    hardening SSH is not what NE7 is for.
    """
    jail = JAIL.read_text(encoding="utf-8")
    block = jail.split("[sshd]", 1)[1].split("[", 1)[0]
    assert re.search(r"enabled\s*=\s*false", block)


def test_the_operator_address_is_recoverable():
    installer = (NATIVE / "scripts" / "install-abuse-protection.sh").read_text(
        encoding="utf-8")
    assert "SSH_CLIENT" in installer
    assert "ignoreip" in installer


def test_only_the_two_mail_jails_are_enabled():
    jail = JAIL.read_text(encoding="utf-8")
    enabled = re.findall(r"^\[([^\]]+)\]([\s\S]*?)(?=^\[|\Z)", jail, re.M)
    on = [name for name, body in enabled
          if re.search(r"^enabled\s*=\s*true", body, re.M)]
    assert sorted(on) == ["matemail-dovecot", "matemail-postfix"], on


# ─── fail2ban filters, driven against real log lines ────────────────────────

#: Copied verbatim from this system's logs.
POSTFIX_ATTACKS = [
    "Sep 19 17:12:33 mx postfix/submission/smtpd[399]: warning: unknown[203.0.113.9]: "
    "SASL LOGIN authentication failed: UGFzc3dvcmQ6",
    "Sep 19 17:12:33 mx postfix/submission/smtpd[399]: warning: host.example[203.0.113.9]: "
    "SASL PLAIN authentication failed: authentication failure",
    "Sep 20 02:01:05 mx postfix/smtpd[701]: NOQUEUE: reject: RCPT from "
    "unknown[203.0.113.9]: 554 5.7.1 <spam@example.net>: Relay access denied; "
    "from=<a@example.net> to=<b@example.org> proto=ESMTP",
]

#: Ordinary traffic that must NOT be treated as an attack.
POSTFIX_INNOCENT = [
    "Sep 19 17:12:33 mx postfix/submission/smtpd[399]: B3700106CFE: "
    "client=matemail-native-submission-gateway.matemail_native_engine[172.27.0.11], "
    "sasl_method=PLAIN, sasl_username=noreply@mail.matemail.online",
    "Sep 19 17:12:34 mx postfix/smtp[406]: B3700106CFE: to=<x@example.com>, "
    "relay=gmail-smtp-in.l.google.com[142.251.127.26]:25, dsn=2.0.0, status=sent (250 OK)",
    # Sender-login mismatch is misconfiguration, not an attack.
    "Sep 20 02:01:05 mx postfix/submission/smtpd[701]: NOQUEUE: reject: RCPT from "
    "unknown[203.0.113.9]: 553 5.7.1 <a@b.test>: Sender address rejected: "
    "not owned by user c@d.test",
    # An unknown recipient on the MX is ordinary Internet noise.
    "Sep 20 02:02:05 mx postfix/smtpd[701]: NOQUEUE: reject: RCPT from "
    "unknown[203.0.113.9]: 550 5.1.1 <nobody@matemail.online>: Recipient address "
    "rejected: User unknown",
]

DOVECOT_ATTACKS = [
    "Sep 20 02:10:11 imap-login: Info: Disconnected (auth failed, 1 attempts in 4 secs): "
    "user=<victim@matemail.online>, method=PLAIN, rip=203.0.113.9, lip=172.27.0.8, "
    "TLS, session=<abc>",
    "Sep 20 02:10:20 imap-login: Info: Aborted login (auth failed, 3 attempts in 9 secs): "
    "user=<victim@matemail.online>, method=PLAIN, rip=203.0.113.9, lip=172.27.0.8, TLS",
]

DOVECOT_INNOCENT = [
    "Sep 19 17:04:40 lmtp(noreply@mail.matemail.online)<16063><e9s>: Info: "
    "msgid=<x@y>: saved mail to INBOX",
    "Sep 20 02:11:00 imap-login: Info: Login: user=<ok@matemail.online>, "
    "method=PLAIN, rip=203.0.113.9, lip=172.27.0.8, mpid=1, TLS",
    # A client giving up without failing auth is not an attack.
    "Sep 20 02:12:00 imap-login: Info: Disconnected (no auth attempts in 0 secs): "
    "rip=203.0.113.9, lip=172.27.0.8, TLS handshaking",
]

#: What fail2ban substitutes for <HOST>, reduced to the IPv4 case.
HOST_RE = r"(?P<host>\d{1,3}(?:\.\d{1,3}){3})"


def _filter_regexes(path: Path):
    text = path.read_text(encoding="utf-8")
    body = text.split("failregex =", 1)[1].split("ignoreregex", 1)[0]
    patterns = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        patterns.append(re.compile(line.replace("<HOST>", HOST_RE)))
    assert patterns, f"{path.name} defines no failregex"
    return patterns


def _matches(patterns, line):
    for rx in patterns:
        m = rx.search(line)
        if m:
            return m
    return None


@pytest.mark.parametrize("line", POSTFIX_ATTACKS)
def test_the_postfix_filter_matches_real_attacks(line):
    m = _matches(_filter_regexes(F2B / "filter.d" / "matemail-postfix.conf"), line)
    assert m, f"filter did not match:\n{line}"
    assert m.group("host") == "203.0.113.9", "the client address was not captured"


@pytest.mark.parametrize("line", POSTFIX_INNOCENT)
def test_the_postfix_filter_ignores_ordinary_traffic(line):
    m = _matches(_filter_regexes(F2B / "filter.d" / "matemail-postfix.conf"), line)
    assert not m, f"filter would ban on ordinary traffic:\n{line}"


@pytest.mark.parametrize("line", DOVECOT_ATTACKS)
def test_the_dovecot_filter_matches_real_attacks(line):
    m = _matches(_filter_regexes(F2B / "filter.d" / "matemail-dovecot.conf"), line)
    assert m, f"filter did not match:\n{line}"
    assert m.group("host") == "203.0.113.9"


@pytest.mark.parametrize("line", DOVECOT_INNOCENT)
def test_the_dovecot_filter_ignores_ordinary_traffic(line):
    m = _matches(_filter_regexes(F2B / "filter.d" / "matemail-dovecot.conf"), line)
    assert not m, f"filter would ban on ordinary traffic:\n{line}"


def test_the_log_shipper_never_replays_history():
    """
    `--tail 0`. Without it, restarting the shipper re-reads the whole log and
    can ban somebody for failures from last week.
    """
    shipper = (NATIVE / "scripts" / "mail-log-shipper.sh").read_text(encoding="utf-8")
    assert "--tail 0" in shipper
    assert "sleep" in shipper, "it must reattach when a container is recreated"


# ─── monitoring understands the new policy ──────────────────────────────────

def test_the_collector_treats_the_three_ports_as_intended():
    body = COLLECTOR.read_text(encoding="utf-8")
    assert "INTENDED_PUBLIC_MAIL_PORTS = {25, 587, 993}" in body
    assert "FORBIDDEN_MAIL_PORTS = {110, 143, 465, 995}" in body
    assert "matemail_public_mail_ports" not in body, (
        "the pre-NE7 metric would now alert on the architecture itself")


def test_monitoring_still_alerts_on_ports_that_must_never_be_public():
    rules = RULES.read_text(encoding="utf-8")
    assert "ForbiddenMailPortPublic" in rules
    assert "matemail_forbidden_mail_ports_public > 0" in rules


def test_monitoring_notices_a_public_port_that_stops_listening():
    rules = RULES.read_text(encoding="utf-8")
    assert "PublicMailPortMissing" in rules


def test_monitoring_watches_the_abuse_protection_and_its_log_source():
    rules = RULES.read_text(encoding="utf-8")
    assert "AbuseProtectionDown" in rules
    assert "AbuseProtectionBlind" in rules, (
        "fail2ban reading a stale file reports itself perfectly healthy")
    body = COLLECTOR.read_text(encoding="utf-8")
    assert "matemail_fail2ban_log_shipper_up" in body


def test_no_attacker_address_becomes_a_metric_label():
    body = COLLECTOR.read_text(encoding="utf-8")
    section = body.split("def sec_abuse_protection", 1)[1].split("\ndef ", 1)[0]
    assert '{"jail": jail}' in section
    for forbidden in ('"ip"', '"address"', '"host": ip', '"banned_ip"'):
        assert forbidden not in section
