# MateMail — Current System Architecture

**Updated: 2026-10-09 · matemail.pro · NetaMate Solutions**

This is the **current production architecture**, replacing outdated descriptions
of a Mailcow-orchestrated engine. Older phases and alternatives remain in
`docs/DECISIONS.md` and `docs/NATIVE_MAIL_ENGINE.md` as migration history, **not
production instructions**.

## Four operational layers

1. **Product:** Next.js frontend (public, Hub, PostBox, Platform Console);
   Django REST backend; Celery worker and beat; dedicated app PostgreSQL + Redis.
2. **Native Mail Engine:** Postfix (SMTP + submission), Dovecot (IMAP + LMTP),
   Rspamd (spam + DKIM), Unbound (DNSSEC), ClamAV/Olefy (content scanning),
   HAProxy submission gateway, policy layer, Native API, **separate** PostgreSQL
   and Redis. The app talks to this layer through private interfaces.
3. **Edge:** host-native nginx, Certbot and the root-owned custom-host worker.
   Customer CNAMEs map to `custom.matemail.pro` and must pass DNS ownership,
   TLS and tenant/surface authorization; **Caddy is not installed**.
4. **Operations:** Prometheus/Grafana/Alertmanager, systemd collector and
   backup timers; encrypted **local** Restic and independent **Azure Blob**
   MateServer DR backups.

All organizational identity, domains, mailbox management and product
authorization belong to MateMail; customers never access engine administration.

## Canonical DNS and surfaces

| Identity | Hostname |
|---|---|
| Website | `matemail.pro` |
| Hub | `hub.matemail.pro` |
| PostBox | `postbox.matemail.pro` |
| Platform Console | `platform.matemail.pro` |
| Client discovery | `autodiscover.matemail.pro` |
| SMTP / IMAP server with PTR | `mx.matemail.pro` |
| Platform sender/report domain | `mail.matemail.pro` |
| Customer hostname CNAME target | `custom.matemail.pro` |

`portal.matemail.pro` is a legacy redirect to Hub, not an independent product.
Do not use retired domain records, certificates or redirects to provision a new
host. The internal platform sender is `noreply@mail.matemail.pro`. At audit
time its DKIM selector was `mm1` and DMARC policy was `p=none`.

## Deployment structure and isolation

```text
/opt/MateMail/
  app/                            # app compose, root-only .env
  engine/deploy/native-engine/    # native compose and root-only .env
  monitoring/                     # Prometheus / Grafana / Alertmanager
  backup/                         # local Restic repository + scripts
```

- Host HTTP proxies to `127.0.0.1:8020` (Django) and `127.0.0.1:3020`
  (Next.js). Native public ports are 25, 587, 993 on the configured server IP.
  Databases and caches are not exposed publicly.
- The app database is not the Native database; both are backed up as logical
  PostgreSQL dumps. Maildir and DKIM private material live in persistent
  named Docker volumes.
- Five app/engine/monitoring Docker networks and sixteen MateMail named
  volumes existed on 2026-10-09. Treat counts as *observations*, not contracts.
- Application releases and Native upgrades use independent GHCR image pins.
  Never re-create Native containers from unverified tag fallbacks.

## Verified vs unverified

On 2026-10-09, Postfix **3.10.13**, Dovecot **2.4.5**, Rspamd **4.2.1** and
Unbound **1.26.1** were running. All 21 containers reported healthy, live
outbound Gmail delivery passed SPF/DKIM/DMARC, and the Gmail reply was
retrieved through Native IMAP. Tenant-level TeamBox, Delegation and Forward
Groups still require real onboarding tests. This distinction is important.

See [deployment](DEPLOYMENT.md), [backup](BACKUP_RESTORE.md) and the
[Native security-upgrade report](NATIVE_ENGINE_SECURITY_UPGRADE_PHASE3_20261009.md).
