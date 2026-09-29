# MateMail — Monitoring and Observability

Written for the operator on call. It answers, in order: how do I look at this,
what am I looking at, and what do I do when it goes off.

---

## 1. The questions this exists to answer

| Question | Where |
|---|---|
| Is MateMail healthy? | Overview → *MateMail services*, *App health endpoint* |
| Is the Native Engine healthy? | Overview → *Native all-healthy* (11 services since NE6) |
| Is mail flowing / stuck? | Mail Operations → *Queue total*, *Oldest message* |
| Deferred or rejected? | Mail Operations → *Postfix events per hour* |
| Authentication failures abnormal? | Mail Operations → *Dovecot authentication* |
| Databases, Redis, DNS, scanners? | Overview stats, Infrastructure |
| Disks filling? | Overview → *Host*; Infrastructure → *Storage growth* |
| Backups current? | Overview → *Backup age*, *Last backup OK* |
| Certificates expiring? | Overview → *Certificate days left* |
| DNS/PTR drifted? | Overview → *MX correct*, *PTR correct* |
| Anything exposed that should not be? | Overview → *Public mail ports* |

---

## 2. Architecture, and why it is this shape

```
  root systemd timer (every 60s)
  matemail-collector.service
        │  writes  /var/lib/node_exporter/textfile_collector/matemail.prom
        ▼
  node-exporter ──┐
                  ├──► Prometheus ──► Alertmanager ──► (webhook, unconfigured)
  prometheus  ────┘         │
                            ▼
                         Grafana
```

Four containers and one host timer. All bound to `127.0.0.1`.

**Why a host collector rather than a shelf of exporters.** Everything worth
seeing here — Native's ten services, the Postfix queue, the Restic repository,
the certificate, the PTR record, whether a mail port has become public — is
knowable only from the host, as root. The conventional answer needs
`postgres_exporter` twice, `redis_exporter` twice, `blackbox_exporter` and
cAdvisor. MateServer already runs 72 containers and is into its swap.

**Why no Docker socket anywhere.** cAdvisor and similar want
`/var/run/docker.sock`. Giving that to a container gives it root on the host.
One root-owned collector on the host reads Docker directly and publishes plain
text; nothing in the monitoring stack has any privilege at all.

**Versions:** Prometheus v3.1.0, Alertmanager v0.28.0, Grafana 11.5.1,
node-exporter v1.8.2.

**Retention:** 15 days *and* 2 GB, whichever comes first. Both are set because
a time-only retention is a promise about disk that depends on how many series
appear later. Change `PROM_RETENTION_TIME` / `PROM_RETENTION_SIZE` in
`/opt/MateMailMonitoring/.env` and restart Prometheus.

---

## 3. Operator access — SSH tunnel

Nothing here is on the Internet and there is no nginx route for any of it. From
the Windows workstation:

```bash
ssh -N -L 3040:127.0.0.1:3040 \
       -L 9090:127.0.0.1:9090 \
       -L 9093:127.0.0.1:9093 \
       -L 9100:127.0.0.1:9100 root@169.58.114.252
```

Then, in a browser:

| | URL |
|---|---|
| Grafana | http://127.0.0.1:3040 |
| Prometheus | http://127.0.0.1:9090 |
| Alertmanager | http://127.0.0.1:9093 |
| node-exporter raw metrics | http://127.0.0.1:9100/metrics |

Grafana credentials: user `admin`; the password was generated at install and
never printed. Read it on the server:

```bash
sudo grep GRAFANA_ADMIN_PASSWORD /opt/MateMailMonitoring/.env
```

Do not create a public monitoring domain. Monitoring data says which services
are down and when backups last ran.

---

## 4. Dashboards

Provisioned from `deploy/monitoring/grafana/dashboards/` in the repository.
`allowUiUpdates` is false: edit the files, not the browser, or the change is
lost the next time the volume is recreated and nobody remembers how it was set.

- **MateMail Overview** — one screen: is anything wrong right now. Native
  10/10, MateMail 6/6, NE6 readiness, backups, certificate, DNS/PTR, public
  ports, host.
- **MateMail Mail Operations** — queue, quarantine, deliveries, bounces,
  rejects, 4xx/5xx, TLS failures, authentication, scanners.
- **MateMail Infrastructure** — host detail, PostgreSQL, Redis, per-volume
  storage growth, backup repository, and the health of the monitoring itself.

---

## 5. Useful Prometheus queries

```promql
# Which Native service is unhealthy
matemail_native_service_healthy == 0

# Is the engine fully healthy (9/10 is NOT healthy)
matemail_native_all_healthy

# Mail sitting in the queue, by state
matemail_native_queue_messages

# Oldest queued message, in hours
matemail_native_queue_oldest_age_seconds / 3600

# Delivery outcomes over the last hour
increase(matemail_postfix_events_total[1h])

# Authentication failures over 15 minutes (counts only, no usernames)
increase(matemail_dovecot_events_total{event="auth_failed"}[15m])

# Backup freshness, in hours
(time() - matemail_backup_latest_snapshot_timestamp_seconds) / 3600

# Which NE6 prerequisite is failing
matemail_ne6_readiness_check == 0

# Which collector section is failing (its metrics are stale, not healthy)
matemail_collector_section_ok == 0

# Per-volume growth over 24h
delta(matemail_volume_bytes[24h])
```

---

## 6. Alerts — what they mean

Full definitions: `deploy/monitoring/prometheus/rules/matemail.rules.yml`.

### Critical — mail is affected, or about to be

| Alert | Means | First thing to check |
|---|---|---|
| `NativeServiceDown` | A Native component stopped | `docker compose ps` in `/opt/MateMailNative/deploy/native-engine` |
| `NativeEngineNotFullyHealthy` | Not 10/10 | `matemail_native_service_healthy == 0` |
| `NativeApiDown` | Control plane cannot provision | `docker logs matemail-native-api` |
| `NativeSchemaMismatch` | Deployed schema ≠ code requirement | `/ready` on the Native API |
| `RspamdDown` / `ClamAVDown` | **Mail will queue.** NE3 defers rather than delivering unscanned | scanner container logs |
| `NativeRateLimitEnforcementUnavailable` | NE4 limiter fails closed; limited mailboxes defer | Native Redis and policy service |
| `NativeQueueGrowing` / `NativeQueuedMailTooOld` | Delivery is failing downstream | §8 below |
| `DnssecValidationLost` | Unbound accepts forgeries or rejects valid answers — the NE1 guarantee is gone | §9 |
| `MailDnsIdentityDrift` | MX/A/PTR changed. Nothing in MateMail changes these, so this is external | §9 |
| `BackupStale` / `BackupFailed` | §10 | |
| `PublicMailPortDetected` | A mail port is Internet-reachable before NE7 | §11 |
| `MonitoringCollectorStale` | **Every `matemail_*` metric is frozen.** Other alerts are now unreliable | §12 |

### Warning — look today

`NativeDeferredQueueGrowing`, `NativeQuarantineAbnormal`,
`AuthenticationFailureSpike`, `ClamAVSignaturesStale`, `DiskSpaceLow`,
`InodesLow`, `MemoryPressure`, `SwapPressure`, `CertificateExpiringSoon`,
`CeleryWorkerDown`, `NE6ReadinessRegressed`.

### Permanent, known, deliberate

`BackupOffsiteNotConfigured` is **currently firing and expected to**. The only
copy of the backups is on this host. That survives deletion, corruption and
operator error but not the loss of the machine: retention, not disaster
recovery. It re-notifies weekly rather than daily so it stays visible without
teaching anyone to ignore alerts. Do not silence it — fix it by setting
`OFFSITE_REPOSITORY` in `/opt/MateMailBackup/backup.env`.

### Why the `for:` windows exist

A two-second container restart must not page anyone, so availability alerts
wait 3–5 minutes. The exceptions are the exposure rules, which fire in one
minute: a mail port becoming public is not something to sit on.

---

## 7. Silencing an alert safely

A silence is for an incident you are already handling, or a maintenance window.
It is not a way to make a red panel green.

```bash
# See what is firing
curl -s http://127.0.0.1:9093/api/v2/alerts | python3 -m json.tool | less

# Silence via the UI (preferred — it records who and why):
#   http://127.0.0.1:9093  →  Silences  →  New Silence
```

Rules:
- **Always set an expiry.** A silence with no end is how an outage goes
  unnoticed for a month. Hours, not weeks.
- **Always write the reason and a ticket.** "noise" is not a reason.
- **Match narrowly.** Silence `alertname=X, service=clamav`, never
  `severity=critical`.
- **Never silence** `MonitoringCollectorStale` or `PrometheusTargetDown`.
  Silencing those blinds everything else.

---

## 8. Inspecting mail queues

```bash
cd /opt/MateMailNative/deploy/native-engine

# Human readable
docker compose exec -T postfix postqueue -p </dev/null

# Structured, which is what the collector parses
docker compose exec -T postfix postqueue -j </dev/null

# Held mail (quarantine) only
docker compose exec -T postfix sh -c 'postqueue -j' </dev/null | grep '"hold"'

# Flush deferred mail (retry now)
docker compose exec -T postfix postqueue -f </dev/null

```

Before NE6 the Native queue is normally **empty**. Anything persisting there
wants explaining.

---

## 9. Verifying DNS, PTR and TLS

```bash
dig +short MX matemail.online              # -> mx.matemail.online
dig +short A  mx.matemail.online           # -> 169.58.114.252
dig +short -x 169.58.114.252               # -> mx.matemail.online
dig +short TXT mail.matemail.online        # -> v=spf1 ...
dig +short TXT mm1._domainkey.mail.matemail.online
dig +short TXT _dmarc.mail.matemail.online

# The NE1 DNSSEC property, through the engine's own resolver
docker exec matemail-native-unbound dig @127.0.0.1 +dnssec cloudflare.com A | grep flags
docker exec matemail-native-unbound dig @127.0.0.1 dnssec-failed.org A | grep status
#   expected: "ad" present on the first, SERVFAIL on the second

# Certificate
openssl x509 -enddate -noout -in /etc/letsencrypt/live/mx.matemail.online/fullchain.pem
systemctl list-timers certbot.timer
```

Monitoring never changes any of these. Detection only.

---

## 10. Verifying backup freshness

```bash
systemctl list-timers matemail-backup.timer
systemctl status matemail-backup.service
journalctl -u matemail-backup.service -n 50

set -a; . /opt/MateMailBackup/backup.env; set +a
restic snapshots
restic check
```

The corresponding metrics are `matemail_backup_timer_enabled`,
`matemail_backup_last_result_ok`,
`matemail_backup_latest_snapshot_timestamp_seconds` and
`matemail_backup_offsite_configured`. Full procedure: `docs/BACKUP_RESTORE.md`.

**The tenant-facing `/api/backups/` feature is not a backup** and is not
represented anywhere in this monitoring. See `docs/TODO.md`.

---

## 11. Exposure checks

```bash
ss -lntu | grep -vE '127\.0\.0\.1|\[::1\]'      # what is publicly bound
ufw status numbered
cd /opt/MateMailNative/deploy/native-engine && docker compose ps --format '{{.Ports}}'
```

Expected public ports are **22, 80, 443** and **4000** (TalkRoom legacy).
Expected public mail ports before NE7: **none**. Expected published Native
container ports: **zero**.

Monitoring never edits a firewall rule. A monitoring system that rewrites the
firewall is one that can lock you out of the box at three in the morning.

---

## 12. Inspecting the collector and the targets

```bash
# Targets Prometheus is scraping
curl -s http://127.0.0.1:9090/api/v1/targets | python3 -m json.tool

# Rules loaded
curl -s http://127.0.0.1:9090/api/v1/rules | python3 -m json.tool | grep '"name"'

# The collector
systemctl status matemail-collector.timer
systemctl start matemail-collector.service      # run one collection now
journalctl -u matemail-collector.service -n 50

# What it published
head -40 /var/lib/node_exporter/textfile_collector/matemail.prom
```

**When a section fails it publishes nothing.** Its metrics go stale rather than
reporting a false healthy value, `matemail_collector_section_ok{section="…"}`
goes to 0, and `MonitoringCollectorSectionFailing` fires. A collector that said
"up 1" because it could not tell would be worse than one that said nothing.

---

## 13. Where to look for errors

| Subsystem | Command |
|---|---|
| Postfix | `docker logs --tail 200 matemail-native-postfix` |
| Dovecot | `docker logs --tail 200 matemail-native-dovecot` |
| Rspamd | `docker logs --tail 200 matemail-native-rspamd` |
| ClamAV | `docker logs --tail 200 matemail-native-clamav` |
| Unbound | `docker logs --tail 200 matemail-native-unbound` |
| Native API | `docker logs --tail 200 matemail-native-api` |
| MateMail | `cd /opt/MateMail && docker compose logs --tail 200 backend` |
| Backups | `journalctl -u matemail-backup.service` |
| Collector | `journalctl -u matemail-collector.service` |
| Alerts | Alertmanager UI, or `curl -s localhost:9093/api/v2/alerts` |

**Log rotation.** P7 found Docker on this host had no `daemon.json` and
therefore no rotation at all — container logs had reached 45 MB each and
nothing was going to reclaim them. Fixing it in `daemon.json` would require
restarting Docker across many unrelated production
production applications, so `/etc/logrotate.d/matemail-docker-containers` bounds
them instead: daily, 5 rotations, 50 MB cap, `copytruncate` because Docker holds
the file open.

No secret, password, API key, DKIM private key or message body is written to
any log or any metric. Metric labels are restricted to a bounded allowlist,
enforced by a test that reads the collector's syntax tree.

---

## 14. NE6 readiness

```promql
matemail_ne6_ready                 # 1 when every technical prerequisite holds
matemail_ne6_readiness_check == 0  # which one is failing
```

It covers: Native 10/10, MateMail healthy, app health endpoint, Native API,
schema agreement, sane queue, scanners, DNS and DNSSEC, DNS identity,
certificate, backups current, rate-limit enforcement, no public mail ports,
monitoring itself healthy.

It **deliberately excludes** offsite backup and the external alert receiver.
Both are genuine pre-beta requirements and both are currently unconfigured.
Folding them in here would either block NE6 on an unrelated purchasing decision
or, worse, tempt someone to mark them green. They are reported separately and
stay visible.

**NE6 technical readiness is not Private Beta readiness.** Private Beta is
stricter and includes both of those gaps.

---

## 15. Routine operations

```bash
cd /opt/MateMailMonitoring

docker compose ps
docker compose logs --tail 100 prometheus
docker compose restart grafana

# Reload Prometheus after editing rules, without dropping its data
curl -X POST http://127.0.0.1:9090/-/reload

# Validate rules BEFORE reloading
docker compose exec prometheus promtool check rules /etc/prometheus/rules/matemail.rules.yml
docker compose exec prometheus promtool test rules /etc/prometheus/tests/matemail.rules.test.yml

# Resource use
docker stats --no-stream matemail-prometheus matemail-grafana \
                         matemail-alertmanager matemail-node-exporter
```

Configuration is deployed from `deploy/monitoring/` in the repository by
`install.sh`. Editing files under `/opt/MateMailMonitoring/` directly means the
next install overwrites them.

---

## 16. Troubleshooting a deployment

Three failures were hit deploying this the first time. All are fixed in
`install.sh`; they are recorded because the symptoms are misleading.

**"permission denied" reading a config file, while the file looks fine.**
Prometheus and Alertmanager run as uid 65534 and Grafana as uid 472. They are
unprivileged on purpose. `install.sh` runs `umask 077` so the runtime `.env` is
private, which also made every copied config root-only. Configuration is now
explicitly 755/644 — none of it is secret, it is all in the repository — and
the `.env` is locked to 0600 separately.

**A container reads configuration that is no longer on disk.** `install.sh`
replaces config directories with `rm -rf` plus a copy, which makes new inodes.
A running container's bind mount was resolved at start and still points at the
deleted directory. The files are correct, the permissions are correct, and
inside the container the directory is unreadable. `install.sh` therefore uses
`--force-recreate`, which is safe because all state lives in named volumes.

**A metric looks stale right after a change.** The collector takes ~18s and the
scrape interval is 30s, so allow at least 60–75s after
`systemctl start matemail-collector.service` before concluding a value is
wrong. Prometheus also serves the previous sample for up to 5 minutes before
marking a series stale, which is what makes
`matemail_collector_last_run_timestamp_seconds` worth watching.

---

## 17. Known gaps

| Gap | Status |
|---|---|
| `OFFSITE_BACKUP_CONFIGURED = NO` | Pre-beta requirement. Local repository is retention, not disaster recovery. |
| `ALERT_RECEIVER_CONFIGURED = NO` | Pre-beta requirement. Alerts fire and are visible in Alertmanager over the tunnel, but nothing is delivered off the host. Set `ALERT_WEBHOOK_URL` in `/opt/MateMailMonitoring/.env` and re-run `install.sh`. The destination must not be served by Native Postfix or MateMail's transactional sender — those are the things being monitored. |

Neither blocks NE6 technical readiness. Both block Private Beta.


---

## 18. What changed at NE6

The engine gained an eleventh service, `submission-gateway`, and it is
monitored like the other ten — `matemail_native_service_up{service=
"submission-gateway"}`. An unmonitored component in the mail path would present
as "the application cannot send" with nothing pointing at the cause.

**The Native queue is no longer expected to be permanently zero.** Platform
transactional mail now flows through it. It should still drain to empty in
seconds; `NativeQueueNonEmpty` fires after 30 minutes, which remains the right
signal, but a brief non-zero reading is now normal rather than suspicious.

**Two Postfix counters were added**, `smtp_auth_ok` and `smtp_auth_failed`.
SMTP authentication is logged by Postfix, not Dovecot, so platform submissions
were not visible in the IMAP auth counter.

**The Postfix counters now have data at all.** Before NE6, Postfix wrote no
log — it defaults to syslog and the container has none — so
`matemail_postfix_events_total` had been reporting a confident zero since it
was written. `maillog_file = /dev/stdout` fixed the cause;
`smtp_tls_loglevel = 1` additionally records whether each delivery negotiated
TLS, which is the first question asked after a delivery and the one NE7 will
ask of every provider.

**`connection_lost` is trustworthy again.** The gateway's health checks used to
drop half-open sessions on Postfix about four times a minute, all counted as
`connection_lost`. The backend check now completes an SMTP conversation and
liveness is answered by HAProxy's own loopback endpoint, so that metric once
more means what it says.
