#!/usr/bin/env python3
"""
MateMail monitoring collector.

WHY THIS EXISTS RATHER THAN A SHELF OF EXPORTERS
    Everything P7 needs to see — Native's ten services, the Postfix queue, the
    Restic repository, the certificate, the PTR record, whether a mail port has
    become public — is knowable only from the host, as root. The usual answer is
    a container per subsystem: postgres_exporter twice, redis_exporter twice,
    blackbox_exporter, cAdvisor. On this box that is several hundred megabytes
    added to a server already running 72 containers and into its swap.

    More importantly, cAdvisor and friends want /var/run/docker.sock. Handing
    the Docker socket to a container is handing it root on the host. One
    root-owned collector on the host, writing plain text that node_exporter
    publishes, gets the same measurements and gives nothing away.

WHAT IT REFUSES TO DO
    It never invents a healthy value. Each section is independent: if one
    fails, that section publishes no samples at all and its `section_ok` goes
    to 0. The metrics it would have written go STALE rather than wrong, which
    Prometheus can see and alert on. A collector that reports "up 1" because it
    could not tell is worse than one that reports nothing.

    It never puts a mailbox, address, message id, tenant, or IP into a label.
    Dovecot's logs are full of addresses and this reads them; only counts come
    out. Cardinality is bounded by construction, not by convention.

SINGLE FILE ON PURPOSE
    It runs as root from a systemd timer. A package split across modules adds
    an import path to get wrong for no benefit at this size.

OUTPUT
    A textfile-collector .prom, written to a temporary file and renamed, so
    node_exporter never reads a half-written one.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(os.environ.get(
    "MATEMAIL_COLLECTOR_OUT",
    "/var/lib/node_exporter/textfile_collector/matemail.prom"))
STATE = Path(os.environ.get(
    "MATEMAIL_COLLECTOR_STATE",
    "/var/lib/matemail-monitoring/state.json"))

NATIVE_DIR = os.environ.get("NATIVE_DIR", "/opt/MateMailNative/deploy/native-engine")
MATEMAIL_DIR = os.environ.get("MATEMAIL_DIR", "/opt/MateMail")
MAILCOW_DIR = os.environ.get("MAILCOW_DIR", "/opt/mailcow-dockerized")
BACKUP_ENV = os.environ.get("BACKUP_ENV", "/opt/MateMailBackup/backup.env")
MATEMAIL_HEALTH_URL = os.environ.get(
    "MATEMAIL_HEALTH_URL", "http://127.0.0.1:8020/api/internal/health/")

MAIL_HOSTNAME = os.environ.get("MAIL_HOSTNAME", "mx.matemail.online")
MAIL_DOMAIN = os.environ.get("MAIL_DOMAIN", "matemail.online")
SENDER_DOMAIN = os.environ.get("SENDER_DOMAIN", "mail.matemail.online")
DKIM_SELECTOR = os.environ.get("DKIM_SELECTOR", "mm1")
PUBLIC_IP = os.environ.get("PUBLIC_IP", "169.58.114.252")

#: The ten Native services, named exactly once. Everything that iterates the
#: engine iterates this, so a service added to the stack but not to this list
#: shows up as a count mismatch instead of being silently unmonitored.
#: NE6 added `submission-gateway`, the private path MateMail sends
#: through. It is listed here because an unmonitored component in the
#: mail path is worse than no component: its failure would present as
#: "the application cannot send" with nothing pointing at the cause.
NATIVE_SERVICES = ["api", "db", "redis", "dovecot", "postfix", "rspamd",
                   "clamav", "olefy", "unbound", "policy",
                   "submission-gateway"]
MATEMAIL_SERVICES = ["backend", "frontend", "celery-worker", "celery-beat",
                     "postgres", "redis"]

#: Every mail port, reported individually so an operator sees the whole
#: picture rather than one number.
MAIL_PORTS = [25, 110, 143, 465, 587, 993, 995]

#: Public BY DESIGN since NE7: the Internet MX, authenticated submission
#: and IMAPS. Before NE7 any public mail port was an incident; that is no
#: longer true, and a monitor that keeps alerting on an intended state
#: teaches people to ignore it.
INTENDED_PUBLIC_MAIL_PORTS = {25, 587, 993}

#: Must NEVER be public. POP3 and its TLS variant are not offered at all,
#: plaintext IMAP would carry a password in clear, and implicit-TLS
#: submission on 465 is a deliberate omission. If one of these appears,
#: something published a port nobody decided to publish.
FORBIDDEN_MAIL_PORTS = {110, 143, 465, 995}

#: Expected to be public. 4000 is TalkRoom legacy and is known.
EXPECTED_PUBLIC = {22, 80, 443, 4000} | INTENDED_PUBLIC_MAIL_PORTS

lines: list[str] = []
sections: dict[str, int] = {}
_declared: set[str] = set()
CONTAINERS: dict[str, dict] = {}


# ── emit ────────────────────────────────────────────────────────────────────

def _labels(labels: dict) -> str:
    if not labels:
        return ""
    return "{" + ",".join(
        '%s="%s"' % (k, str(v).replace("\\", "\\\\").replace('"', '\\"'))
        for k, v in sorted(labels.items())) + "}"


def metric(name, value, labels=None, help_text="", mtype="gauge"):
    if value is None:
        return
    if name not in _declared:
        _declared.add(name)
        if help_text:
            lines.append("# HELP %s %s" % (name, help_text))
        lines.append("# TYPE %s %s" % (name, mtype))
    if isinstance(value, bool):
        value = 1 if value else 0
    lines.append("%s%s %s" % (name, _labels(labels or {}), value))


def run(cmd, timeout=20, cwd=None, env=None, stdin_text=None):
    """Run a command, returning stdout. Never uses a shell."""
    p = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env,
        input=stdin_text,
        **({} if stdin_text is not None else {"stdin": subprocess.DEVNULL}))
    if p.returncode != 0:
        raise RuntimeError("%s rc=%d %s" % (cmd[0], p.returncode,
                                            p.stderr.strip()[:200]))
    return p.stdout


def try_run(cmd, timeout=20, cwd=None, env=None, stdin_text=None):
    """Same, but a non-zero exit is data rather than an error."""
    try:
        return run(cmd, timeout=timeout, cwd=cwd, env=env,
                   stdin_text=stdin_text)
    except RuntimeError as exc:
        # A non-zero exit still produced output worth having in some cases;
        # the caller decides what an empty string means.
        return getattr(exc, "stdout", "") or ""
    except Exception:                                   # noqa: BLE001
        return ""


def dexec(container, *args, timeout=20):
    return run(["docker", "exec", container, *args], timeout=timeout)


def dexec_try(container, *args, timeout=20):
    p = subprocess.run(["docker", "exec", container, *args],
                       capture_output=True, text=True, timeout=timeout,
                       stdin=subprocess.DEVNULL)
    return p.stdout


def dexec_script(container, script, timeout=25):
    """
    Feed a multi-line Python program in over stdin rather than cramming it into
    `-c`. The inline form needs its newlines and quotes escaped through two
    layers and is where this sort of probe usually breaks.
    """
    p = subprocess.run(["docker", "exec", "-i", container, "python3", "-"],
                       capture_output=True, text=True, timeout=timeout,
                       input=script)
    return p.stdout


def psql(container, sql, timeout=20):
    """
    Query a database through its own container, so no credential ever appears
    on this host's command line or in this host's process list.

    The SQL is wrapped in shell DOUBLE quotes so that ordinary SQL single
    quotes survive: wrapping in single quotes would end the shell string at the
    first `'active'` and silently change the query. Nothing here may contain a
    double quote, a backtick or a dollar sign.
    """
    assert not set(sql) & set('"`$'), "unsafe character in collector SQL"
    out = dexec(container, "sh", "-ec",
                'psql -qtAX -F: -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "'
                + sql + '"', timeout=timeout)
    return out.strip()


# ── state, for counters that survive restarts ───────────────────────────────

def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:                                   # noqa: BLE001
        return {}


state = load_state()


def save_state():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    os.chmod(tmp, 0o600)
    tmp.replace(STATE)


def section(name, fn):
    """
    Run one section, all-or-nothing.

    On failure everything the section already emitted is ROLLED BACK. Catching
    the exception is not enough on its own: a section that publishes four
    metrics and then fails would otherwise leave those four behind, which is
    precisely the half-truth this design exists to avoid. The backup section
    found this for real — it emitted the systemd timer state, then failed
    reading the repository configuration, and published a partial picture that
    looked like a complete one.

    `_declared` is rewound with the lines so that a metric whose HELP/TYPE was
    rolled back is declared properly if some later section emits it.
    """
    mark, declared = len(lines), set(_declared)
    try:
        fn()
        sections[name] = 1
    except Exception as exc:                            # noqa: BLE001
        del lines[mark:]
        _declared.clear()
        _declared.update(declared)
        sections[name] = 0
        print("section %s failed: %r" % (name, exc), file=sys.stderr)


# ── containers ──────────────────────────────────────────────────────────────

def inspect_all():
    """
    One `docker inspect` for every container rather than one call per service.
    With 72 containers on the box the difference is seconds per collection.
    """
    names = run(["docker", "ps", "-a", "--format", "{{.Names}}"]).split()
    if not names:
        return {}
    return {c["Name"].lstrip("/"): c
            for c in json.loads(run(["docker", "inspect", *names], timeout=60))}


def _parse_iso_offset(value):
    """
    Parse an ISO timestamp that carries an offset, to a real epoch.

    datetime.fromisoformat handles the offset; what it will not take is more
    than six fractional digits, which both Docker and restic emit.
    """
    text = value.strip().replace("Z", "+00:00")
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    return datetime.fromisoformat(text).timestamp()


def _parse_docker_time(value):
    if not value or value.startswith("0001"):
        return None
    try:
        return _parse_iso_offset(value)
    except Exception:                                   # noqa: BLE001
        return None


def container_metrics(prefix, expected, name_fn):
    healthy = 0
    now = time.time()
    for svc in expected:
        c = CONTAINERS.get(name_fn(svc))
        lb = {"service": svc}
        if c is None:
            metric(prefix + "_service_up", 0, lb,
                   "1 when the service's container is running")
            metric(prefix + "_service_healthy", 0, lb,
                   "1 when the container reports healthy")
            continue
        st = c.get("State", {})
        running = 1 if st.get("Running") else 0
        status = (st.get("Health") or {}).get("Status", "none")
        # A container with no healthcheck is judged on running alone; one that
        # has a healthcheck must actually pass it.
        ok = 1 if status == "healthy" else (1 if status == "none" and running else 0)
        healthy += 1 if (ok and running) else 0
        metric(prefix + "_service_up", running, lb,
               "1 when the service's container is running")
        metric(prefix + "_service_healthy", ok, lb,
               "1 when the service reports healthy")
        metric(prefix + "_service_restarts_total", c.get("RestartCount", 0), lb,
               "Container restart count", "counter")
        ts = _parse_docker_time(st.get("StartedAt", ""))
        if ts:
            metric(prefix + "_service_uptime_seconds", int(max(0, now - ts)), lb,
                   "Seconds since the container started")
    return healthy


# ── sections ────────────────────────────────────────────────────────────────

def sec_native():
    healthy = container_metrics("matemail_native", NATIVE_SERVICES,
                                lambda s: "matemail-native-" + s)
    metric("matemail_native_services_expected", len(NATIVE_SERVICES), {},
           "Native Engine services that must be healthy")
    metric("matemail_native_services_healthy", healthy, {},
           "Native Engine services currently healthy")
    # Deliberately all-or-nothing. An average would render 9/10 as 0.9 and look
    # almost fine; one dead scanner is not 90% of a working mail system.
    metric("matemail_native_all_healthy",
           1 if healthy == len(NATIVE_SERVICES) else 0, {},
           "1 only when every Native service is healthy")


def sec_matemail():
    healthy = container_metrics("matemail_app", MATEMAIL_SERVICES,
                                lambda s: "matemail-" + s + "-1")
    metric("matemail_app_services_expected", len(MATEMAIL_SERVICES), {},
           "MateMail services that must be healthy")
    metric("matemail_app_services_healthy", healthy, {},
           "MateMail services currently healthy")
    metric("matemail_app_all_healthy",
           1 if healthy == len(MATEMAIL_SERVICES) else 0, {},
           "1 only when every MateMail service is healthy")


def sec_app_health():
    """
    Application health through MateMail's own endpoint, not container status.
    A backend whose container is running and whose database is gone is 'up' to
    Docker and useless to a customer.
    """
    import urllib.request
    secret = ""
    for line in Path(MATEMAIL_DIR, ".env").read_text().splitlines():
        if line.startswith("INTERNAL_API_SECRET="):
            secret = line.split("=", 1)[1].strip()
            break
    req = urllib.request.Request(MATEMAIL_HEALTH_URL)
    if secret:
        req.add_header("X-Internal-Secret", secret)
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=10) as r:
        body = json.loads(r.read().decode())
    metric("matemail_app_health_request_seconds", round(time.time() - t0, 4), {},
           "Latency of MateMail's internal health endpoint")
    metric("matemail_app_health_up", 1 if body.get("status") in
           ("ok", "degraded") else 0, {},
           "1 when MateMail's health endpoint answered")
    metric("matemail_app_health_ok", 1 if body.get("status") == "ok" else 0, {},
           "1 when MateMail reports itself fully healthy")
    for name, data in (body.get("checks") or {}).items():
        if not isinstance(data, dict):
            continue
        metric("matemail_app_dependency_up",
               1 if data.get("status") == "ok" else 0, {"dependency": name},
               "1 when a MateMail dependency reports ok")
        if isinstance(data.get("latency_ms"), (int, float)):
            metric("matemail_app_dependency_latency_seconds",
                   round(data["latency_ms"] / 1000.0, 5), {"dependency": name},
                   "Dependency check latency")


def sec_engine_api():
    """Native API as MateMail actually reaches it, plus schema agreement."""
    # The probe runs INSIDE the API container: the engine network has no host
    # port, and P7 is not going to open one just so Prometheus can look. The
    # secret it uses is the container's own environment variable, so it is
    # never on a command line and never reaches this host.
    probe = (
        "import json, os, sys, time, urllib.error, urllib.request\n"
        "path, authed = sys.argv[1], sys.argv[2] == '1'\n"
        "req = urllib.request.Request('http://127.0.0.1:8451' + path)\n"
        "if authed:\n"
        "    req.add_header('X-Native-Api-Secret', os.environ['NATIVE_API_SECRET'])\n"
        "t0 = time.time()\n"
        "try:\n"
        "    body = json.loads(urllib.request.urlopen(req, timeout=8).read().decode())\n"
        "    code = 200\n"
        "except urllib.error.HTTPError as e:\n"
        "    code, body = e.code, {}\n"
        "except Exception:\n"
        "    code, body = 0, {}\n"
        "print(json.dumps({'c': code, 'b': body, 't': time.time() - t0}))\n"
    )

    def call(path, authed):
        p = subprocess.run(
            ["docker", "exec", "-i", "matemail-native-api",
             "python3", "-", path, "1" if authed else "0"],
            capture_output=True, text=True, timeout=25, input=probe)
        try:
            got = json.loads(p.stdout.strip().splitlines()[-1])
            return got["c"], got["b"], got["t"]
        except Exception:                               # noqa: BLE001
            return 0, {}, 0.0

    code, body, took = call("/health", False)
    metric("matemail_native_api_up", 1 if code == 200 else 0, {},
           "1 when the Native API answers its health route")
    metric("matemail_native_api_request_seconds", round(took, 4), {},
           "Native API health round-trip time")
    metric("matemail_native_api_database_reachable",
           1 if body.get("database") == "reachable" else 0, {},
           "1 when the Native API can reach its database")

    code, ready, _ = call("/ready", True)
    if code == 200:
        metric("matemail_native_api_ready", 1 if ready.get("ready") else 0, {},
               "1 when the Native API reports itself ready")
        metric("matemail_native_schema_version",
               ready.get("schema_version"), {}, "Native Engine schema version")
        metric("matemail_native_schema_version_required",
               ready.get("required_schema_version"), {},
               "Schema version the Native API requires")
        metric("matemail_native_schema_version_matches",
               1 if ready.get("schema_version") ==
               ready.get("required_schema_version") else 0, {},
               "1 when the deployed schema is the version the code requires")
        metric("matemail_native_dkim_storage_writable",
               1 if ready.get("dkim_storage_writable") else 0, {},
               "1 when DKIM key storage is writable")


def _queue_json(container):
    out = dexec_try(container, "postqueue", "-j", timeout=25)
    items = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                items.append(json.loads(line))
            except Exception:                           # noqa: BLE001
                pass
    return items


def _queue_metrics(prefix, container, help_prefix):
    items = _queue_json(container)
    buckets = {"active": 0, "deferred": 0, "hold": 0, "incoming": 0,
               "maildrop": 0, "corrupt": 0}
    oldest = 0
    now = time.time()
    for it in items:
        st = str(it.get("queue_name", "unknown")).lower()
        buckets[st] = buckets.get(st, 0) + 1
        arrival = it.get("arrival_time")
        if isinstance(arrival, (int, float)):
            oldest = max(oldest, now - arrival)
    for name, count in sorted(buckets.items()):
        metric(prefix + "_queue_messages", count, {"queue": name},
               help_prefix + " messages by queue")
    metric(prefix + "_queue_total", len(items), {},
           help_prefix + " total queued messages")
    metric(prefix + "_queue_oldest_age_seconds", int(oldest), {},
           help_prefix + " age of the oldest queued message")
    return buckets, len(items)


def sec_mail_queue():
    buckets, total = _queue_metrics("matemail_native", "matemail-native-postfix",
                                    "Native Postfix")
    # Held mail is quarantine. It is reported separately because "deferred"
    # and "awaiting a human" are opposite operational situations.
    metric("matemail_native_quarantine_held", buckets.get("hold", 0), {},
           "Messages held in the Native quarantine")
    metric("matemail_native_queue_empty", 1 if total == 0 else 0, {},
           "1 when the Native queue is empty, which is normal before NE6")


def sec_transitional():
    """
    Mailcow still carries Internet mail until NE8. This exists to see that
    transition safely and is deliberately thin: no product depends on it, and
    it is deleted with Mailcow.
    """
    running = sum(1 for n, c in CONTAINERS.items()
                  if n.startswith("mailcowdockerized-")
                  and c.get("State", {}).get("Running"))
    metric("matemail_mailcow_containers_running", running, {},
           "Running Mailcow containers (transitional, removed at NE8)")
    metric("matemail_mailcow_up", 1 if running > 0 else 0, {},
           "1 when Mailcow is running")
    bridge = CONTAINERS.get("mailcowdockerized-matemail-policy-bridge-1")
    if bridge is not None:
        st = bridge.get("State", {})
        healthy = (st.get("Health") or {}).get("Status") == "healthy"
        metric("matemail_p5_bridge_up", 1 if st.get("Running") else 0, {},
               "1 when the P5 policy bridge container is running")
        metric("matemail_p5_bridge_healthy", 1 if healthy else 0, {},
               "1 when the P5 policy bridge reports healthy")
    else:
        metric("matemail_p5_bridge_up", 0, {},
               "1 when the P5 policy bridge container is running")
        metric("matemail_p5_bridge_healthy", 0, {},
               "1 when the P5 policy bridge reports healthy")
    if "mailcowdockerized-postfix-mailcow-1" in CONTAINERS:
        _queue_metrics("matemail_mailcow", "mailcowdockerized-postfix-mailcow-1",
                       "Mailcow Postfix")


#: Postfix and Dovecot log shapes. Kept as a table so adding a signal is a row,
#: and so it is obvious at a glance that nothing here captures an address.
LOG_PATTERNS = {
    "postfix": [
        ("delivered", re.compile(r"status=sent")),
        ("deferred", re.compile(r"status=deferred")),
        ("bounced", re.compile(r"status=bounced")),
        ("rejected", re.compile(r"\breject:")),
        ("tls_failure", re.compile(r"TLS (?:handshake|library) (?:problem|error)")),
        ("connection_lost", re.compile(r"lost connection after")),
        # NE6: platform submissions are real traffic now. A successful
        # SASL login is logged by Postfix, not Dovecot, so it needs its
        # own signal here rather than being read off the IMAP counter.
        ("smtp_auth_ok", re.compile(r"sasl_method=\S+, sasl_username=")),
        ("smtp_auth_failed", re.compile(r"SASL \S+ authentication failed")),
        ("smtp_4xx", re.compile(r"said: 4\d\d")),
        ("smtp_5xx", re.compile(r"said: 5\d\d")),
    ],
    "dovecot": [
        # Dovecot 2.4 wording, verified against this server's own log rather
        # than remembered. 2.3 said "Aborted login" and "Login: user="; 2.4
        # says "Login aborted:" and "Logged in: user=". The 2.3 spellings are
        # kept as alternates, but it was the 2.4 ones that were missing, and
        # the counters read a confident zero through every real login.
        ("auth_failed", re.compile(r"auth[ _]failed|Login aborted:|"
                                   r"Aborted login|password mismatch",
                                   re.IGNORECASE)),
        ("auth_ok", re.compile(r"Logged in: user=|Login: user=")),
        ("lmtp_error", re.compile(r"lmtp.*(?:error|failed)", re.IGNORECASE)),
        ("quota_exceeded", re.compile(r"[Qq]uota exceeded")),
    ],
}


def sec_log_counters():
    """
    True cumulative counters, built by reading only log lines newer than the
    last run and adding the delta to a persisted total.

    A windowed gauge would have been less code, but `rate()` over a gauge that
    resets to zero every five minutes is meaningless, and that is the first
    thing anyone writes when a queue starts filling. The cursor is the log
    timestamp, so a restarted collector does not double count; losing the state
    file resets the counters, which Prometheus already handles as a counter
    reset.

    NOTHING from the log text is emitted. Dovecot's lines contain mailbox
    addresses; only the tally of matches leaves this function.
    """
    counters = state.setdefault("log_counters", {})
    cursors = state.setdefault("log_cursors", {})
    for svc, patterns in LOG_PATTERNS.items():
        container = "matemail-native-" + svc
        if container not in CONTAINERS:
            continue
        since = cursors.get(svc)
        cmd = ["docker", "logs", "--timestamps"]
        cmd += ["--since", since] if since else ["--since", "15m"]
        cmd += [container]
        # BOTH streams, merged.
        #
        # `docker logs` reproduces each stream on the corresponding handle:
        # Postfix writes its maillog to stdout, Dovecot writes to stderr. This
        # collector read only stdout, so every Dovecot line was invisible — no
        # cursor was ever stored for it and its auth counters reported a
        # confident zero from the day they were written, through every real
        # login and every real failure. Found in NE7, alongside the separate
        # bug that the patterns carried Dovecot 2.3's wording.
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                             stdin=subprocess.DEVNULL)
        out = (out.stdout or "") + (out.stderr or "")
        newest = since
        for line in out.splitlines():
            parts = line.split(" ", 1)
            if len(parts) != 2:
                continue
            ts, text = parts
            if since and ts <= since:
                continue
            if newest is None or ts > newest:
                newest = ts
            for key, rx in patterns:
                if rx.search(text):
                    ck = svc + "_" + key
                    counters[ck] = counters.get(ck, 0) + 1
        if newest:
            cursors[svc] = newest
        for key, _ in patterns:
            ck = svc + "_" + key
            metric("matemail_" + svc + "_events_total", counters.get(ck, 0),
                   {"event": key},
                   "Cumulative %s events observed in logs" % svc, "counter")

    sessions = dexec_try("matemail-native-dovecot", "doveadm", "who", timeout=15)
    rows = [r for r in sessions.splitlines()[1:] if r.strip()]
    metric("matemail_dovecot_active_sessions", len(rows), {},
           "Active Dovecot sessions (count only; no usernames)")


def sec_scanners():
    out = dexec_try("matemail-native-rspamd", "rspamc", "--json", "stat",
                    timeout=20)
    stat = json.loads(out[out.index("{"):]) if "{" in out else {}
    if stat:
        metric("matemail_rspamd_up", 1, {}, "1 when Rspamd answers rspamc")
        metric("matemail_rspamd_scanned_total", stat.get("scanned", 0), {},
               "Messages scanned by Rspamd", "counter")
        metric("matemail_rspamd_learned_total", stat.get("learned", 0), {},
               "Messages learned by Rspamd", "counter")
        metric("matemail_rspamd_uptime_seconds", stat.get("uptime", 0), {},
               "Rspamd uptime")
        for action, count in (stat.get("actions") or {}).items():
            metric("matemail_rspamd_actions_total", count,
                   {"action": action.replace(" ", "_")},
                   "Rspamd actions taken", "counter")
    else:
        metric("matemail_rspamd_up", 0, {}, "1 when Rspamd answers rspamc")

    # ClamAV's VERSION reply carries the signature database date, which is the
    # only thing that distinguishes a running scanner from a useful one.
    ver = dexec_try("matemail-native-clamav", "sh", "-ec",
                    "echo VERSION | nc -w 5 127.0.0.1 3310", timeout=20).strip()
    if "ClamAV" in ver:
        metric("matemail_clamav_up", 1, {}, "1 when clamd answers VERSION")
        parts = ver.split("/")
        if len(parts) >= 3:
            metric("matemail_clamav_signature_version",
                   int(re.sub(r"\D", "", parts[1]) or 0), {},
                   "ClamAV signature database version")
            try:
                when = datetime.strptime(parts[2].strip(), "%a %b %d %H:%M:%S %Y")
                age = time.time() - when.replace(tzinfo=timezone.utc).timestamp()
                metric("matemail_clamav_signature_age_seconds", int(age), {},
                       "Age of the ClamAV signature database")
            except Exception:                           # noqa: BLE001
                pass
    else:
        metric("matemail_clamav_up", 0, {}, "1 when clamd answers VERSION")

    olefy = CONTAINERS.get("matemail-native-olefy", {}).get("State", {})
    metric("matemail_olefy_up", 1 if olefy.get("Running") else 0, {},
           "1 when the Olefy document scanner is running")


def sec_dns():
    """
    Unbound, and the NE1 property that must survive: a signed domain validates
    and a deliberately broken one fails. A resolver that answers everything,
    including forgeries, is worse than one that is down.
    """
    metric("matemail_unbound_up",
           1 if CONTAINERS.get("matemail-native-unbound", {})
           .get("State", {}).get("Running") else 0, {},
           "1 when Unbound is running")
    t0 = time.time()
    ok = dexec_try("matemail-native-unbound", "dig", "@127.0.0.1", "+dnssec",
                   "+time=5", "cloudflare.com", "A", timeout=20)
    metric("matemail_dns_query_seconds", round(time.time() - t0, 4), {},
           "Time for a DNSSEC-validating query through Unbound")
    metric("matemail_dns_resolution_ok", 1 if "NOERROR" in ok else 0, {},
           "1 when Unbound resolves a known-good name")
    metric("matemail_dnssec_validating",
           1 if re.search(r"flags:[^;]*\bad\b", ok) else 0, {},
           "1 when Unbound sets AD on a signed answer")
    bad = dexec_try("matemail-native-unbound", "dig", "@127.0.0.1", "+time=5",
                    "dnssec-failed.org", "A", timeout=20)
    metric("matemail_dnssec_rejects_invalid", 1 if "SERVFAIL" in bad else 0, {},
           "1 when Unbound refuses a deliberately broken DNSSEC domain")


def sec_postgres():
    for label, container in (("matemail", "matemail-postgres-1"),
                             ("native", "matemail-native-db")):
        lb = {"instance": label}
        try:
            row = psql(container,
                       "select (select count(*) from pg_stat_activity), "
                       "(select pg_database_size(current_database())), "
                       "(select count(*) from pg_stat_activity where "
                       "state = 'active' and now() - query_start > "
                       "interval '60 seconds'), "
                       "(select coalesce(sum(deadlocks),0) from pg_stat_database), "
                       "(select count(*) from pg_locks where not granted)")
            conns, size, longq, deadlocks, waiting = row.split(":")
            metric("matemail_postgres_up", 1, lb, "1 when PostgreSQL answers")
            metric("matemail_postgres_connections", int(conns), lb,
                   "Current backend connections")
            metric("matemail_postgres_database_bytes", int(size), lb,
                   "Size of the application database")
            metric("matemail_postgres_long_running_queries", int(longq), lb,
                   "Queries active for more than 60 seconds")
            metric("matemail_postgres_deadlocks_total", int(deadlocks), lb,
                   "Deadlocks detected", "counter")
            metric("matemail_postgres_locks_waiting", int(waiting), lb,
                   "Lock requests not yet granted")
        except Exception:                               # noqa: BLE001
            metric("matemail_postgres_up", 0, lb, "1 when PostgreSQL answers")


def _redis_info(container):
    out = dexec(container, "redis-cli", "info", timeout=15)
    info = {}
    for line in out.splitlines():
        if ":" in line and not line.startswith("#"):
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    return info


def sec_redis():
    for label, container in (("matemail", "matemail-redis-1"),
                             ("native", "matemail-native-redis")):
        lb = {"instance": label}
        try:
            info = _redis_info(container)
            metric("matemail_redis_up", 1, lb, "1 when Redis answers INFO")
            metric("matemail_redis_memory_bytes",
                   int(info.get("used_memory", 0)), lb, "Redis memory in use")
            metric("matemail_redis_connected_clients",
                   int(info.get("connected_clients", 0)), lb,
                   "Connected Redis clients")
            metric("matemail_redis_evicted_keys_total",
                   int(info.get("evicted_keys", 0)), lb,
                   "Keys evicted by Redis", "counter")
            metric("matemail_redis_rdb_last_save_timestamp_seconds",
                   int(info.get("rdb_last_save_time", 0)), lb,
                   "Unix time of the last RDB save")
        except Exception:                               # noqa: BLE001
            metric("matemail_redis_up", 0, lb, "1 when Redis answers INFO")

    # The Native policy service is what enforces per-mailbox rate limits, and
    # NE4 made it fail CLOSED: if its counters are gone, mail defers rather
    # than escaping unmetered. That makes Native Redis a mail-flow dependency,
    # not a cache, which is why it gets its own explicit signal.
    policy = CONTAINERS.get("matemail-native-policy", {}).get("State", {})
    metric("matemail_policy_service_up", 1 if policy.get("Running") else 0, {},
           "1 when the Native policy service is running")
    metric("matemail_rate_limit_enforcement_available",
           1 if (policy.get("Running") and
                 CONTAINERS.get("matemail-native-redis", {})
                 .get("State", {}).get("Running")) else 0, {},
           "1 when per-mailbox rate limiting can be enforced; 0 means "
           "configured limits fail closed and mail defers")


def _volume_bytes(volume):
    out = run(["docker", "volume", "inspect", volume,
               "--format", "{{.Mountpoint}}"]).strip()
    return int(run(["du", "-sb", out], timeout=120).split()[0])


def sec_storage():
    """
    Volumes individually, because free space on / hides everything. A mailbox
    doubling in a day is invisible in 152 GB of headroom right up to the point
    where it is not.
    """
    for label, volume in (("vmail", "matemail_native_vmail"),
                          ("dkim", "matemail_native_dkim"),
                          ("native_db", "matemail_native_pgdata"),
                          ("matemail_db", "matemail_postgres_data")):
        try:
            metric("matemail_volume_bytes", _volume_bytes(volume),
                   {"volume": label}, "Bytes used by a MateMail data volume")
        except Exception:                               # noqa: BLE001
            pass
    try:
        metric("matemail_backup_repository_bytes",
               int(run(["du", "-sb", "/opt/MateMailBackup/repo"],
                       timeout=120).split()[0]), {},
               "Bytes used by the Restic backup repository")
    except Exception:                                   # noqa: BLE001
        pass


def sec_backups():
    """
    P6 made backups real; this makes them observable. It reports the local
    repository honestly: offsite is a separate, explicitly false signal, and a
    same-host repository is retention, not disaster recovery.
    """
    enabled = try_run(["systemctl", "is-enabled", "matemail-backup.timer"]).strip()
    metric("matemail_backup_timer_enabled", 1 if enabled == "enabled" else 0, {},
           "1 when the backup timer is enabled")
    props = try_run(["systemctl", "show", "matemail-backup.service",
                     "-p", "ExecMainStatus", "-p", "ExecMainExitTimestamp",
                     "-p", "Result"])
    vals = dict(l.split("=", 1) for l in props.splitlines() if "=" in l)
    metric("matemail_backup_last_exit_status",
           int(vals.get("ExecMainStatus", "-1") or -1), {},
           "Exit status of the last backup run")
    metric("matemail_backup_last_result_ok",
           1 if vals.get("Result") == "success" else 0, {},
           "1 when the last backup run succeeded")
    stamp = vals.get("ExecMainExitTimestamp", "").strip()
    if stamp:
        secs = try_run(["date", "-d", stamp, "+%s"]).strip()
        if secs.isdigit():
            metric("matemail_backup_last_success_timestamp_seconds", int(secs), {},
                   "Unix time the last backup finished")

    env = {}
    for line in Path(BACKUP_ENV).read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    offsite = bool(env.get("OFFSITE_REPOSITORY"))
    # Deliberately a persistent, visible zero rather than an absent metric.
    metric("matemail_backup_offsite_configured", 1 if offsite else 0, {},
           "1 when an OFFSITE backup repository is configured; 0 means the "
           "only copy is on this host, which is retention, not disaster "
           "recovery")

    # restic reads its repository and password from the environment. This is
    # the only place the password FILE PATH is handed over; the password itself
    # is never read here, never logged and never emitted.
    renv = dict(os.environ)
    renv.update({k: v for k, v in env.items()
                 if k in ("RESTIC_REPOSITORY", "RESTIC_PASSWORD_FILE",
                          "RESTIC_CACHE_DIR")})
    snaps = try_run(["restic", "snapshots", "--json", "--tag", "matemail"],
                    timeout=60, env=renv)
    try:
        data = json.loads(snaps)
    except Exception:                                   # noqa: BLE001
        data = []
    if data:
        # restic stamps an offset ("...+02:00"); parsing only the first 19
        # characters would silently read it as local time and report an age
        # that is wrong by the UTC offset.
        newest = max(_parse_iso_offset(s["time"]) for s in data)
        metric("matemail_backup_snapshots", len(data), {},
               "Snapshots retained in the backup repository")
        metric("matemail_backup_latest_snapshot_timestamp_seconds", int(newest), {},
               "Unix time of the newest backup snapshot")
        metric("matemail_backup_latest_snapshot_age_seconds",
               int(time.time() - newest), {},
               "Age of the newest backup snapshot")


def sec_tls():
    out = run(["openssl", "x509", "-enddate", "-noout", "-in",
               "/etc/letsencrypt/live/%s/fullchain.pem" % MAIL_HOSTNAME])
    when = out.split("=", 1)[1].strip()
    secs = try_run(["date", "-d", when, "+%s"]).strip()
    if secs.isdigit():
        expiry = int(secs)
        lb = {"host": MAIL_HOSTNAME}
        metric("matemail_certificate_expiry_timestamp_seconds", expiry, lb,
               "Unix time the certificate expires")
        metric("matemail_certificate_days_remaining",
               int((expiry - time.time()) / 86400), lb,
               "Days until the certificate expires")
        metric("matemail_certificate_valid",
               1 if expiry > time.time() else 0, lb,
               "1 while the certificate has not expired")
    t = try_run(["systemctl", "is-enabled", "certbot.timer"]).strip()
    metric("matemail_certbot_timer_enabled", 1 if t == "enabled" else 0, {},
           "1 when the certbot renewal timer is enabled")


def _dig(*args):
    return try_run(["dig", "+short", "+time=5", "+tries=2", *args],
                   timeout=20).strip()


def sec_dns_identity():
    """
    The production mail identity, checked rather than assumed. Drift here is
    how a mail system quietly stops being deliverable, and it is checked
    without changing anything.
    """
    mx = _dig("MX", MAIL_DOMAIN)
    metric("matemail_dns_mx_correct",
           1 if MAIL_HOSTNAME in mx else 0, {},
           "1 when the domain's MX points at the expected mail host")
    a = _dig("A", MAIL_HOSTNAME)
    metric("matemail_dns_a_correct", 1 if PUBLIC_IP in a else 0, {},
           "1 when the mail host resolves to the expected address")
    ptr = _dig("-x", PUBLIC_IP)
    metric("matemail_dns_ptr_correct",
           1 if MAIL_HOSTNAME in ptr else 0, {},
           "1 when the reverse record matches the mail host")
    spf = try_run(["dig", "+short", "TXT", SENDER_DOMAIN], timeout=20)
    metric("matemail_dns_spf_present", 1 if "v=spf1" in spf else 0, {},
           "1 when the sender domain publishes SPF")
    dkim = try_run(["dig", "+short", "TXT",
                    "%s._domainkey.%s" % (DKIM_SELECTOR, SENDER_DOMAIN)],
                   timeout=20)
    metric("matemail_dns_dkim_present", 1 if "v=DKIM1" in dkim else 0, {},
           "1 when the sender domain publishes a DKIM public key")
    dmarc = try_run(["dig", "+short", "TXT", "_dmarc." + SENDER_DOMAIN],
                    timeout=20)
    metric("matemail_dns_dmarc_present", 1 if "v=DMARC1" in dmarc else 0, {},
           "1 when the sender domain publishes DMARC")


def sec_exposure():
    """
    Detection only. This never edits a firewall rule: a monitoring system that
    rewrites the firewall is a monitoring system that can lock you out of the
    box at three in the morning.
    """
    listeners = try_run(["ss", "-lntH"], timeout=15)
    public_ports = set()
    for line in listeners.splitlines():
        cols = line.split()
        if len(cols) < 4:
            continue
        addr = cols[3]
        port = addr.rsplit(":", 1)[-1]
        host = addr.rsplit(":", 1)[0]
        if not port.isdigit():
            continue
        if host in ("127.0.0.1", "[::1]") or host.startswith("127."):
            continue
        public_ports.add(int(port))

    for port in MAIL_PORTS:
        metric("matemail_mail_port_public", 1 if port in public_ports else 0,
               {"port": str(port)}, "1 when this mail port is publicly bound")

    # The signal that still means "something is wrong". NE7 made 25, 587
    # and 993 intended, so counting every public mail port would now alert
    # on the design itself.
    forbidden = sorted(p for p in public_ports if p in FORBIDDEN_MAIL_PORTS)
    metric("matemail_forbidden_mail_ports_public", len(forbidden), {},
           "Mail ports that must never be public (110, 143, 465, 995) that "
           "are; must be 0")

    # The other half, which did not exist before NE7: an intended port that
    # has STOPPED listening is an outage, and nothing could say so.
    missing = sorted(INTENDED_PUBLIC_MAIL_PORTS - public_ports)
    metric("matemail_intended_mail_ports_missing", len(missing), {},
           "Public mail ports that should be listening and are not")

    metric("matemail_unexpected_public_ports",
           len(public_ports - EXPECTED_PUBLIC), {},
           "Publicly bound ports beyond the expected set")

    # Native now publishes exactly three. Anything beyond that is a port
    # somebody added without deciding to.
    published = try_run(["docker", "compose", "ps", "--format", "{{.Ports}}"],
                        timeout=30, cwd=NATIVE_DIR)
    native_public = 0
    for line in published.splitlines():
        for mapping in line.split(","):
            mapping = mapping.strip()
            if "->" not in mapping:
                continue
            bind = mapping.split("->", 1)[0]
            if bind.startswith("127.") or not bind:
                continue
            port = bind.rsplit(":", 1)[-1]
            if port.isdigit() and int(port) not in INTENDED_PUBLIC_MAIL_PORTS:
                native_public += 1
    metric("matemail_native_unintended_published_ports", native_public, {},
           "Native container ports published publicly that are not one of "
           "the three NE7 intended; must be 0")

    ufw = try_run(["ufw", "status"], timeout=15)
    metric("matemail_ufw_active", 1 if "Status: active" in ufw else 0, {},
           "1 when the host firewall is active")


def sec_abuse_protection():
    """
    NE7 opened public submission and IMAPS, so credential stuffing is now part
    of normal traffic. What matters operationally is whether the protection is
    RUNNING: a jail that silently stopped looks exactly like a quiet week.

    Counts only, never the banned addresses. One time series per attacker is an
    unbounded label and a list of IP addresses living in a metrics store.
    """
    active = try_run(["systemctl", "is-active", "fail2ban"], timeout=10).strip()
    metric("matemail_fail2ban_up", 1 if active == "active" else 0, {},
           "1 when the abuse-protection service is running")

    # If the shipper dies, fail2ban keeps running against a file nobody writes
    # to: it bans nothing while reporting itself perfectly healthy.
    shipper = try_run(["systemctl", "is-active", "matemail-maillog"],
                      timeout=10).strip()
    metric("matemail_fail2ban_log_shipper_up", 1 if shipper == "active" else 0,
           {}, "1 when Native mail logs are reaching the file fail2ban reads")

    if active != "active":
        return

    for jail in ("matemail-postfix", "matemail-dovecot"):
        out = try_run(["fail2ban-client", "status", jail], timeout=15)
        metric("matemail_fail2ban_jail_up", 1 if out.strip() else 0,
               {"jail": jail}, "1 when this jail is loaded")
        if not out.strip():
            continue
        for line in out.splitlines():
            value = line.rsplit(":", 1)[-1].strip()
            if not value.isdigit():
                continue
            if "Currently banned:" in line:
                metric("matemail_fail2ban_current_banned", int(value),
                       {"jail": jail}, "Addresses currently banned")
            elif "Currently failed:" in line:
                metric("matemail_fail2ban_current_failed", int(value),
                       {"jail": jail}, "Addresses with recent failures")
            elif "Total banned:" in line:
                metric("matemail_fail2ban_banned_total", int(value),
                       {"jail": jail}, "Bans issued since start", "counter")


def sec_celery():
    """
    Celery through its own ping, not just a running container. A worker that
    has lost its broker keeps its process alive and stops doing work.
    """
    out = dexec_try("matemail-celery-worker-1", "celery", "-A", "config",
                    "inspect", "ping", "-t", "10", timeout=30)
    metric("matemail_celery_workers_responding",
           len(re.findall(r"->.*: OK", out)), {},
           "Celery workers answering a ping")
    beat = CONTAINERS.get("matemail-celery-beat-1", {}).get("State", {})
    metric("matemail_celery_beat_up", 1 if beat.get("Running") else 0, {},
           "1 when Celery beat is running")
    try:
        depth = dexec("matemail-redis-1", "redis-cli", "llen", "celery",
                      timeout=15).strip()
        metric("matemail_celery_queue_depth", int(depth), {},
               "Tasks waiting in the default Celery queue")
    except Exception:                                   # noqa: BLE001
        pass


def sec_readiness():
    """
    One number an operator can act on: is the mail engine technically ready for
    NE6?

    It deliberately EXCLUDES offsite backup and the external alert receiver.
    Both are real pre-beta requirements and both are currently unconfigured;
    folding them in here would either block NE6 on an unrelated purchase
    decision or, worse, tempt someone to mark them green. They are reported
    separately, as warnings, and stay visible.
    """
    emitted = {}
    for line in lines:
        if line.startswith("#") or " " not in line:
            continue
        name = line.split("{")[0].split(" ")[0]
        try:
            emitted.setdefault(name, float(line.rsplit(" ", 1)[1]))
        except Exception:                               # noqa: BLE001
            pass

    def got(name, want=1):
        return 1 if emitted.get(name) == want else 0

    checks = {
        "native_all_healthy": got("matemail_native_all_healthy"),
        "matemail_healthy": got("matemail_app_all_healthy"),
        "app_health_ok": got("matemail_app_health_ok"),
        "engine_api": got("matemail_native_api_up"),
        "schema": got("matemail_native_schema_version_matches"),
        "queue_sane": 1 if emitted.get("matemail_native_queue_total", 0) < 50 else 0,
        "scanners": 1 if (got("matemail_rspamd_up") and got("matemail_clamav_up")
                          and got("matemail_olefy_up")) else 0,
        "dns": 1 if (got("matemail_dns_resolution_ok")
                     and got("matemail_dnssec_validating")
                     and got("matemail_dnssec_rejects_invalid")) else 0,
        "dns_identity": 1 if (got("matemail_dns_mx_correct")
                              and got("matemail_dns_a_correct")
                              and got("matemail_dns_ptr_correct")) else 0,
        "certificate": 1 if emitted.get(
            "matemail_certificate_days_remaining", -1) > 14 else 0,
        "backups_current": 1 if 0 <= emitted.get(
            "matemail_backup_latest_snapshot_age_seconds", 1e9) < 172800 else 0,
        "rate_limit": got("matemail_rate_limit_enforcement_available"),
        # NE7 inverted this. Before it, readiness meant NO mail port was
        # public; after it, the three intended ports being public IS the
        # working state, and what would be wrong is a forbidden port appearing
        # or an intended one disappearing. Leaving the old check in place would
        # have pinned readiness at 0 permanently the moment NE7 deployed.
        "no_forbidden_mail_ports": 1 if emitted.get(
            "matemail_forbidden_mail_ports_public", 1) == 0 else 0,
        "public_mail_ports_listening": 1 if emitted.get(
            "matemail_intended_mail_ports_missing", 1) == 0 else 0,
        "abuse_protection": 1 if (got("matemail_fail2ban_up")
                                  and got("matemail_fail2ban_log_shipper_up")) else 0,
        "monitoring_ok": 1 if all(sections.values()) else 0,
    }
    for name, ok in sorted(checks.items()):
        metric("matemail_ne6_readiness_check", ok, {"check": name},
               "1 when an NE6 technical prerequisite is satisfied")
    metric("matemail_ne6_ready", 1 if all(checks.values()) else 0, {},
           "1 when every technical prerequisite for operating the mail engine "
           "is satisfied. Named for NE6, where it began as a go/no-go gate; "
           "since NE7 it is the engine's continuous operating-readiness "
           "signal, and its checks changed accordingly. Still excludes "
           "offsite backup and the external alert receiver, which are "
           "reported separately as pre-beta warnings")
    metric("matemail_ne6_readiness_checks_failed",
           sum(1 for v in checks.values() if not v), {},
           "NE6 prerequisites not currently satisfied")


def main():
    global CONTAINERS
    started = time.time()
    try:
        CONTAINERS = inspect_all()
    except Exception as exc:                            # noqa: BLE001
        print("cannot inspect containers: %r" % exc, file=sys.stderr)
        CONTAINERS = {}

    for name, fn in [
        ("native_services", sec_native), ("matemail_services", sec_matemail),
        ("app_health", sec_app_health), ("engine_api", sec_engine_api),
        ("mail_queue", sec_mail_queue), ("transitional", sec_transitional),
        ("log_counters", sec_log_counters), ("scanners", sec_scanners),
        ("dns", sec_dns), ("postgres", sec_postgres), ("redis", sec_redis),
        ("storage", sec_storage), ("backups", sec_backups), ("tls", sec_tls),
        ("abuse_protection", sec_abuse_protection),
        ("dns_identity", sec_dns_identity), ("exposure", sec_exposure),
        ("celery", sec_celery),
    ]:
        section(name, fn)

    # Readiness last: it reads what the other sections produced.
    section("readiness", sec_readiness)

    for name, ok in sorted(sections.items()):
        metric("matemail_collector_section_ok", ok, {"section": name},
               "1 when this collector section completed; its metrics are "
               "absent and go stale when it did not")
    metric("matemail_collector_sections_failed",
           sum(1 for v in sections.values() if not v), {},
           "Collector sections that failed this run")
    metric("matemail_collector_duration_seconds",
           round(time.time() - started, 3), {}, "Collection duration")
    metric("matemail_collector_last_run_timestamp_seconds", int(time.time()), {},
           "Unix time of the last completed collection")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.chmod(tmp, 0o644)
    tmp.replace(OUT)
    save_state()
    return 0


if __name__ == "__main__":
    sys.exit(main())
