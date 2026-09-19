"""
Tests for the P7 monitoring stack.

The alert RULES are tested for real by promtool, against the unit tests in
deploy/monitoring/prometheus/tests/. Those prove the thing that matters about a
rule — when it fires and when it stays quiet — and are run during deployment.

What is worth pinning down here is different: the properties that no amount of
running the stack would reveal until it was too late. Chief among them is
cardinality and privacy. A collector that one day puts a mailbox address in a
label would keep working perfectly, publish customer data into a metrics store,
and blow up Prometheus's memory a month later. That is checked by reading the
collector's syntax tree, not by grepping it.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[2]
MON = REPO / "deploy" / "monitoring"

COMPOSE = MON / "docker-compose.yml"
PROM = MON / "prometheus" / "prometheus.yml"
RULES = MON / "prometheus" / "rules" / "matemail.rules.yml"
RULE_TESTS = MON / "prometheus" / "tests" / "matemail.rules.test.yml"
ALERTMANAGER = MON / "alertmanager" / "alertmanager.yml"
COLLECTOR = MON / "collectors" / "matemail_collector.py"
INSTALL = MON / "install.sh"
ENV_EXAMPLE = MON / ".env.example"
DATASOURCES = MON / "grafana" / "provisioning" / "datasources" / "prometheus.yml"
DASH_PROVIDER = MON / "grafana" / "provisioning" / "dashboards" / "dashboards.yml"
DASHBOARDS = sorted((MON / "grafana" / "dashboards").glob("*.json"))


def load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _collector_module():
    """
    Import the collector, for the tests better asked of the real objects than
    of the source text.

    Safe to import: it depends only on the standard library, and its module
    level does nothing but define constants and read a few environment
    variables. Nothing runs until main() is called.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "matemail_collector_under_test", COLLECTOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ─── everything exists and parses ───────────────────────────────────────────

def test_every_monitoring_file_is_present():
    for path in (COMPOSE, PROM, RULES, RULE_TESTS, ALERTMANAGER, COLLECTOR,
                 INSTALL, ENV_EXAMPLE, DATASOURCES, DASH_PROVIDER):
        assert path.is_file(), f"{path.relative_to(REPO)} is missing"
    assert len(DASHBOARDS) >= 3, "the three operator dashboards must exist"


@pytest.mark.parametrize(
    "path", [PROM, RULES, RULE_TESTS, ALERTMANAGER, DATASOURCES, DASH_PROVIDER,
             COMPOSE],
    ids=lambda p: p.name)
def test_yaml_parses(path):
    assert load(path) is not None


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.name)
def test_dashboard_json_parses(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["panels"], f"{path.name} has no panels"


def test_collector_is_valid_python():
    ast.parse(COLLECTOR.read_text(encoding="utf-8"))


# ─── nothing here may be reachable from the Internet ────────────────────────

def test_every_published_port_is_loopback_only():
    """
    Monitoring data says which services are down and when backups last ran.
    A public dashboard is a gift to anyone looking for a way in.
    """
    compose = load(COMPOSE)
    published = []
    for name, svc in compose["services"].items():
        for mapping in svc.get("ports", []) or []:
            published.append((name, str(mapping)))
    assert published, "no ports published at all; the tunnel would not work"
    for name, mapping in published:
        assert mapping.startswith("127.0.0.1:"), (
            f"{name} publishes {mapping}, which is not loopback-only")


def test_grafana_does_not_take_the_port_another_app_already_uses():
    """
    127.0.0.1:3000 belongs to another application on this shared host, which
    is why Grafana is on 3040. Reverting to the default would collide.
    """
    compose = load(COMPOSE)
    ports = [str(p) for p in compose["services"]["grafana"].get("ports", [])]
    assert any(p.startswith("127.0.0.1:3040:") for p in ports), ports
    assert not any(p.startswith("127.0.0.1:3000:") for p in ports)


def test_no_container_is_given_the_docker_socket():
    """
    Mounting /var/run/docker.sock into a container is handing it root on the
    host. Container state comes from the root-owned host collector instead.
    """
    for path in list(MON.rglob("*.yml")) + list(MON.rglob("*.yaml")):
        # Comments are stripped first: these files explain at length why they
        # mount no socket, and that explanation necessarily names it.
        config = "\n".join(line for line in
                           path.read_text(encoding="utf-8").splitlines()
                           if not line.lstrip().startswith("#"))
        assert "docker.sock" not in config, path


def test_no_public_nginx_route_is_added_for_monitoring():
    nginx = REPO / "deploy" / "nginx"
    if not nginx.exists():
        pytest.skip("no nginx configuration in the repository")
    for path in nginx.rglob("*"):
        if path.is_file():
            body = path.read_text(encoding="utf-8", errors="ignore")
            for token in ("grafana", "prometheus", "alertmanager"):
                assert token not in body.lower(), (
                    f"{path.name} appears to route {token} publicly")


# ─── resource limits, because this box is already swapping ──────────────────

def test_every_service_has_a_memory_limit():
    compose = load(COMPOSE)
    for name, svc in compose["services"].items():
        limits = ((svc.get("deploy") or {}).get("resources") or {}).get("limits")
        assert limits and limits.get("memory"), (
            f"{name} has no memory limit; MateServer runs 72 containers and is "
            f"already into its swap")


def test_the_whole_stack_is_capped_well_under_a_gigabyte():
    compose = load(COMPOSE)
    total = 0
    for svc in compose["services"].values():
        mem = svc["deploy"]["resources"]["limits"]["memory"]
        total += int(re.sub(r"[^0-9]", "", mem))
    assert total <= 900, f"monitoring may consume up to {total}M, which is too much"


def test_services_restart_and_are_health_checked():
    compose = load(COMPOSE)
    for name, svc in compose["services"].items():
        assert svc.get("restart") == "unless-stopped", name
        assert svc.get("healthcheck"), f"{name} has no healthcheck"


def test_monitoring_state_survives_a_container_restart():
    compose = load(COMPOSE)
    assert compose.get("volumes"), "no named volumes; all history would be lost"
    names = set(compose["volumes"])
    assert {"prometheus_data", "grafana_data", "alertmanager_data"} <= names


def test_retention_is_bounded_by_both_time_and_size():
    """
    A time-only retention is a promise about disk that depends on how many
    series turn up later. The size cap is the one that protects the filesystem.
    """
    command = " ".join(load(COMPOSE)["services"]["prometheus"]["command"])
    assert "--storage.tsdb.retention.time=" in command
    assert "--storage.tsdb.retention.size=" in command
    env = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "PROM_RETENTION_TIME=" in env and "PROM_RETENTION_SIZE=" in env


# ─── secrets ────────────────────────────────────────────────────────────────

def test_no_runtime_secret_is_committed():
    assert not (MON / ".env").exists(), "a runtime .env must never be committed"
    gitignore = (MON / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore


def test_no_credential_literal_anywhere_in_the_monitoring_tree():
    """
    A secret scan that understands the difference between naming a variable and
    setting one. `GRAFANA_ADMIN_PASSWORD=` with nothing after it is a template;
    with a value after it, it is a leak.
    """
    # The value must look like a secret, not like shell or a regex. Restricting
    # it to a credential alphabet is what separates a real leak from
    # `sed "s|^X_PASSWORD=.*|X_PASSWORD=${PW}|"`, which is code that WRITES a
    # secret without ever containing one.
    assignment = re.compile(
        r"(?i)(password|secret|token|api[_-]?key)\s*[:=]\s*['\"]?"
        r"([A-Za-z0-9_+/=-]{8,})")
    allowed = {"GRAFANA_ADMIN_PASSWORD", "ALERT_WEBHOOK_URL",
               "INTERNAL_API_SECRET", "NATIVE_API_SECRET"}
    for path in MON.rglob("*"):
        if not path.is_file() or path.suffix == ".pyc":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8",
                                                errors="ignore").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            # `${VAR:?explanation}` is a required-variable check whose message
            # is prose, not a value. Expansions are removed before scanning so
            # that explanation cannot read as a committed credential.
            line = re.sub(r"\$\{[^}]*\}", "${}", line)
            m = assignment.search(line)
            if m and m.group(2) not in allowed:
                raise AssertionError(
                    f"possible committed credential at {path.name}:{n}")


def test_grafana_password_comes_from_the_environment_and_is_required():
    compose = load(COMPOSE)
    env = compose["services"]["grafana"]["environment"]
    value = env["GF_SECURITY_ADMIN_PASSWORD"]
    assert value.startswith("${GRAFANA_ADMIN_PASSWORD"), value
    # `:?` makes a missing password fail the deploy rather than silently
    # falling back to Grafana's default of admin/admin.
    assert ":?" in value, "a missing password must fail the deploy"


def test_install_generates_the_password_without_printing_it():
    body = INSTALL.read_text(encoding="utf-8")
    assert "openssl rand" in body
    assert 'if [ -e "$DEST/.env" ]' in body, (
        "re-running install must not lock the operator out by regenerating "
        "the password")
    assert not re.search(r'echo .*\$(\{)?PW', body), "the password is printed"


def test_grafana_is_locked_down():
    env = load(COMPOSE)["services"]["grafana"]["environment"]
    assert env["GF_USERS_ALLOW_SIGN_UP"] == "false"
    assert env["GF_AUTH_ANONYMOUS_ENABLED"] == "false"


# ─── the privacy and cardinality property, checked in the syntax tree ───────

#: Every label key the collector is allowed to emit. All are bounded sets:
#: ten services, six services, two instances, a handful of queues and events.
ALLOWED_LABEL_KEYS = {
    "service", "instance", "dependency", "queue", "event", "action",
    "volume", "section", "check", "host", "port",
}

#: Label keys that would be a privacy incident, an unbounded cardinality
#: explosion, or both.
FORBIDDEN_HINTS = ("address", "mailbox", "recipient", "sender", "user",
                   "tenant", "domain", "message", "msgid", "ip", "email",
                   "uuid", "queue_id")


def _collector_metric_calls():
    tree = ast.parse(COLLECTOR.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "metric"):
            yield node


def _label_dicts():
    """
    Every label dict that can reach metric(), resolved through simple local
    variables.

    Yields (lineno, ast.Dict). Labels are frequently built once as `lb = {...}`
    and reused across several metric() calls; demanding a literal at every call
    site would only push people to inline and duplicate them. A label argument
    that cannot be resolved to a literal is itself a failure: this test exists
    to bound cardinality and cannot bound what it cannot read.
    """
    tree = ast.parse(COLLECTOR.read_text(encoding="utf-8"))
    assigned: dict[str, list] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned.setdefault(target.id, []).append(node.value)

    for call in _collector_metric_calls():
        arg = call.args[2] if len(call.args) >= 3 else None
        if arg is None:
            continue
        if isinstance(arg, ast.Dict):
            yield call.lineno, arg
        elif isinstance(arg, ast.Name):
            found = assigned.get(arg.id)
            assert found, (
                f"metric() at line {call.lineno} passes labels as {arg.id!r}, "
                f"which is never assigned a literal dict, so cardinality "
                f"cannot be bounded")
            for literal in found:
                yield call.lineno, literal
        else:
            raise AssertionError(
                f"metric() at line {call.lineno} builds labels dynamically, "
                f"which this test cannot bound")


def test_the_collector_emits_only_bounded_label_keys():
    """
    Read from the syntax tree rather than by grepping, so a label added inside
    a loop or behind a variable is still seen.
    """
    calls = list(_collector_metric_calls())
    assert len(calls) > 40, f"only found {len(calls)} metric calls; parser wrong?"
    examined = 0
    for lineno, labels in _label_dicts():
        for key in labels.keys:
            examined += 1
            assert isinstance(key, ast.Constant), (
                f"non-literal label key at line {lineno}")
            assert key.value in ALLOWED_LABEL_KEYS, (
                f"label {key.value!r} at line {lineno} is not in the bounded "
                f"allowlist")
    assert examined > 10, "no label keys examined; the resolver is not working"


def test_no_label_key_resembles_personal_data():
    for lineno, labels in _label_dicts():
        for key in labels.keys:
            low = str(getattr(key, "value", "")).lower()
            for hint in FORBIDDEN_HINTS:
                assert hint not in low, (
                    f"label {low!r} at line {lineno} looks like personal data "
                    f"or unbounded cardinality")


def test_log_parsing_emits_counts_and_never_log_text():
    """
    The collector reads Dovecot logs, which are full of mailbox addresses. The
    only thing that may leave that function is a tally.
    """
    source = COLLECTOR.read_text(encoding="utf-8")
    body = source.split("def sec_log_counters", 1)[1].split("\ndef ", 1)[0]
    # The event label is a fixed key from the pattern table, never log text.
    assert '{"event": key}' in body
    for bad in ("line)", "text)", "match.group"):
        assert f'metric("matemail_" + svc, {bad}' not in body
    assert "counters.get(ck, 0)" in body, "values must come from the tally"


def test_the_pattern_table_captures_nothing():
    """
    A regex with a capture group invites someone to use the captured text as a
    label, and a log line is exactly where a mailbox address would come from.

    The compiled objects are asked directly rather than the source re-parsed:
    `groups == 0` is the actual property, and unlike a text scan it cannot be
    fooled by how a pattern happens to be split across lines.
    """
    patterns = _collector_module().LOG_PATTERNS
    assert patterns, "the pattern table is empty"
    for service, entries in patterns.items():
        assert entries, f"{service} has no patterns"
        for key, rx in entries:
            assert rx.groups == 0, (
                f"{service}/{key} has {rx.groups} capture group(s); nothing "
                f"captured from a log line may become a metric")


# ─── the collector must not fake health ─────────────────────────────────────

def test_a_failing_section_publishes_nothing_rather_than_a_default():
    source = COLLECTOR.read_text(encoding="utf-8")
    body = source.split("def section(", 1)[1].split("\ndef ", 1)[0]
    assert "sections[name] = 0" in body
    # The failure path must not emit anything; the metrics go stale instead.
    assert "metric(" not in body, (
        "a failing section must publish no samples at all, so its metrics go "
        "stale rather than reporting a false healthy value")


def test_the_collector_publishes_its_own_liveness():
    source = COLLECTOR.read_text(encoding="utf-8")
    assert "matemail_collector_last_run_timestamp_seconds" in source
    assert "matemail_collector_section_ok" in source


def test_the_output_file_is_written_atomically():
    """node_exporter must never read a half-written textfile."""
    source = COLLECTOR.read_text(encoding="utf-8")
    assert "tmp.replace(OUT)" in source


def test_native_service_health_is_all_or_nothing():
    """
    9 of 10 is not healthy. An average would render it as 0.9 and look almost
    fine; one dead scanner is not 90% of a working mail system.
    """
    source = COLLECTOR.read_text(encoding="utf-8")
    assert "matemail_native_all_healthy" in source
    assert "1 if healthy == len(NATIVE_SERVICES) else 0" in source
    rules = RULES.read_text(encoding="utf-8")
    assert "NativeEngineNotFullyHealthy" in rules


def test_the_collector_never_puts_a_database_credential_on_a_command_line():
    source = COLLECTOR.read_text(encoding="utf-8")
    assert "PGPASSWORD" not in source
    assert 'psql -qtAX -F: -U "$POSTGRES_USER"' in source, (
        "credentials must be read inside the container from its own environment")


def test_collector_sql_cannot_break_out_of_its_shell_quoting():
    """
    Regression guard. The SQL is wrapped in shell DOUBLE quotes so ordinary SQL
    single quotes survive; a stray double quote, backtick or dollar sign would
    change the command rather than the query.
    """
    source = COLLECTOR.read_text(encoding="utf-8")
    assert 'assert not set(sql) & set(' in source


# ─── alert rules: structure, and the windows that stop alert storms ─────────

def _all_rules():
    for group in load(RULES)["groups"]:
        for rule in group.get("rules", []):
            if "alert" in rule:
                yield group["name"], rule


def test_every_alert_has_a_severity_and_a_summary():
    for group, rule in _all_rules():
        labels = rule.get("labels") or {}
        assert labels.get("severity") in ("warning", "critical"), (
            f"{rule['alert']} in {group} has no usable severity")
        assert (rule.get("annotations") or {}).get("summary"), (
            f"{rule['alert']} has no summary; an operator would see only a name")


def test_availability_alerts_wait_long_enough_to_survive_a_restart():
    """
    A rule with no `for:` pages on every deploy. The exceptions are the
    security rules, which are supposed to be immediate.
    """
    immediate_by_design = {
        "PublicMailPortDetected", "NativePortPublished",
        "NativeServiceRestartLoop", "MailDeliveryFailureSpike",
        "MailRejectionSpike", "AuthenticationFailureSpike",
        "MailStorageGrowingFast",
    }
    for group, rule in _all_rules():
        name = rule["alert"]
        window = rule.get("for")
        if name in immediate_by_design:
            continue
        assert window, f"{name} has no `for:` window and will alert on a blip"
        assert window != "0m", f"{name} has a zero `for:` window"


def test_security_alerts_are_not_made_to_wait():
    rules = {r["alert"]: r for _, r in _all_rules()}
    for name in ("PublicMailPortDetected", "NativePortPublished"):
        window = rules[name].get("for", "0m")
        minutes = int(re.sub(r"[^0-9]", "", window) or 0)
        assert minutes <= 2, (
            f"{name} waits {window}; a mail port becoming public should be "
            f"reported at once")
        assert rules[name]["labels"]["severity"] == "critical"


def test_the_required_alerts_all_exist():
    """The P7 list, checked by name so none is quietly dropped."""
    required = {
        "NativeServiceDown", "MateMailServiceDown", "MailcowDown",
        "P5PolicyBridgeDown", "NativeQueueGrowing", "NativeQueuedMailTooOld",
        "NativeDeferredQueueGrowing", "RedisDown", "PostgresDown",
        "UnboundDown", "RspamdDown", "ClamAVDown", "OlefyDown",
        "BackupStale", "BackupFailed", "DiskSpaceLow", "InodesLow",
        "CertificateExpiringSoon", "MailDnsIdentityDrift",
        "PublicMailPortDetected", "PrometheusTargetDown",
        "MonitoringCollectorStale", "NativeRateLimitEnforcementUnavailable",
    }
    present = {rule["alert"] for _, rule in _all_rules()}
    assert required <= present, f"missing alerts: {sorted(required - present)}"


def test_the_offsite_gap_is_a_permanent_visible_warning():
    """
    It is true right now and it is a pre-beta requirement. It must not be
    silently absent from monitoring, and it must not page anyone nightly.
    """
    rules = {r["alert"]: r for _, r in _all_rules()}
    rule = rules["BackupOffsiteNotConfigured"]
    assert rule["labels"]["severity"] == "warning"
    assert rule["labels"].get("persistent") == "true"
    route = load(ALERTMANAGER)["route"]["routes"][0]
    assert 'persistent="true"' in route["matchers"][0]
    assert route["repeat_interval"] == "168h", (
        "a permanent known gap must not re-notify often enough to be ignored")


def test_alertmanager_groups_alerts_so_one_incident_is_one_notification():
    am = load(ALERTMANAGER)
    assert set(am["route"]["group_by"]) >= {"alertname"}
    assert am["route"]["group_wait"], "no group_wait; a burst becomes a storm"
    assert am.get("inhibit_rules"), "no inhibitions; dead services alert twice"


def test_alert_delivery_does_not_depend_on_the_mail_system_it_watches():
    """
    Monitoring that emails you to say mail is down is not monitoring.
    """
    am = ALERTMANAGER.read_text(encoding="utf-8")
    config = load(ALERTMANAGER)
    for receiver in config["receivers"]:
        assert "email_configs" not in receiver, (
            "alerts must not be delivered through the mail system under test")
    assert "smtp_smarthost" not in am
    env = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert re.search(r"^ALERT_WEBHOOK_URL=\s*$", env, re.M), (
        "no provider may be chosen on the operator's behalf")


# ─── Grafana provisioning ───────────────────────────────────────────────────

def test_dashboards_are_provisioned_from_files_not_clicked_into_the_ui():
    provider = load(DASH_PROVIDER)["providers"][0]
    assert provider["type"] == "file"
    assert provider["allowUiUpdates"] is False, (
        "UI edits would drift from the repository and never be reviewed")


def test_every_dashboard_panel_points_at_the_pinned_datasource():
    """
    The datasource uid is pinned in provisioning precisely so dashboards can
    reference it. A mismatch renders every panel as 'datasource not found'.
    """
    uid = load(DATASOURCES)["datasources"][0]["uid"]
    assert uid == "matemail-prom"
    for path in DASHBOARDS:
        data = json.loads(path.read_text(encoding="utf-8"))
        for panel in data["panels"]:
            for target in panel.get("targets", []) or []:
                ds = target.get("datasource") or {}
                assert ds.get("uid") == uid, (
                    f"{path.name}/{panel.get('title')} points at {ds}")


def test_the_three_operator_dashboards_cover_what_p7_asks_for():
    seen = {}
    for path in DASHBOARDS:
        data = json.loads(path.read_text(encoding="utf-8"))
        seen[data["uid"]] = json.dumps(data)
    assert set(seen) == {"matemail-overview", "matemail-mail-ops",
                         "matemail-infra"}

    overview = seen["matemail-overview"]
    for expr in ("matemail_native_services_healthy", "matemail_app_all_healthy",
                 "matemail_backup_latest_snapshot_age_seconds",
                 "matemail_certificate_days_remaining",
                 "matemail_dns_ptr_correct", "matemail_mailcow_up",
                 "matemail_ne6_ready"):
        assert expr in overview, f"overview does not show {expr}"

    mail = seen["matemail-mail-ops"]
    for expr in ("matemail_native_queue_messages",
                 "matemail_native_queue_oldest_age_seconds",
                 "matemail_native_quarantine_held",
                 "matemail_postfix_events_total",
                 "matemail_dovecot_events_total", "matemail_rspamd_up",
                 "matemail_clamav_up"):
        assert expr in mail, f"mail operations does not show {expr}"

    infra = seen["matemail-infra"]
    for expr in ("matemail_postgres_up", "matemail_redis_up",
                 "matemail_volume_bytes", "matemail_backup_repository_bytes",
                 "matemail_collector_section_ok", "node_load15"):
        assert expr in infra, f"infrastructure does not show {expr}"


def test_no_dashboard_displays_a_per_mailbox_or_per_address_breakdown():
    for path in DASHBOARDS:
        body = path.read_text(encoding="utf-8").lower()
        for token in ("by (address)", "by (mailbox)", "by (recipient)",
                      "by (sender)", "by (user)", "{{address}}", "{{mailbox}}",
                      "{{recipient}}", "{{sender}}"):
            assert token not in body, f"{path.name} breaks down by {token}"


# ─── promtool, when it can be run ───────────────────────────────────────────

DOCKER = shutil.which("docker")


def _promtool(*args):
    return subprocess.run(
        [DOCKER, "run", "--rm", "--network", "none",
         "-v", f"{MON.as_posix()}:/w:ro", "-w", "/w",
         "--entrypoint", "promtool", "prom/prometheus:v3.1.0", *args],
        capture_output=True, text=True, timeout=180)


needs_docker = pytest.mark.skipif(
    DOCKER is None, reason="docker is not available; promtool runs on the server")


@needs_docker
def test_prometheus_config_is_valid():
    result = _promtool("check", "config", "prometheus/prometheus.yml")
    assert result.returncode == 0, result.stdout + result.stderr


@needs_docker
def test_alert_rules_are_valid():
    result = _promtool("check", "rules", "prometheus/rules/matemail.rules.yml")
    assert result.returncode == 0, result.stdout + result.stderr


@needs_docker
def test_alert_rule_unit_tests_pass():
    """
    The real semantic test: each rule is driven through a failure and asserted
    to be silent during its `for:` window and firing after it.
    """
    result = _promtool("test", "rules",
                       "prometheus/tests/matemail.rules.test.yml")
    assert result.returncode == 0, result.stdout + result.stderr
