> **CURRENT DOCUMENTATION NOTICE (2026-10-09):** Historical cleanup runbook. Some safety prohibitions were specific to the migration window, not an order to retain retired resources forever. See current architecture.
> Authoritative current references: [Architecture](ARCHITECTURE.md),
> [Deployment](DEPLOYMENT.md), and [Backup/Azure DR](BACKUP_RESTORE.md).
> Sections below may describe historical migration states or retired domains.

---

# Phase E4 — Legacy dependency retirement (pre-E5)

## Scope and safety

E1–E3 replaced public routes, Native MTA identity, transactional sender, customer
MX/SPF/SRV and verified custom-host CNAMEs. E4 reconciles operational monitoring,
prepares aggregate DMARC report ingestion, and inventories remaining legacy
dependencies **without destroying old mailbox mail or backup history**.

Do not delete `mail.matemail.online` or its two Native mailboxes, remove the
legacy SAN certificate, delete old web redirects, turn off legacy DNS compatibility,
or erase old private DKIM material until E5 validates all references and mail
archives. No changes in E4 should affect customers' existing DKIM TXT keys.

## Monitoring change (deployed)

The monitoring collector previously looked up `mx.matemail.online` and reported
a PTR failure after the E2 migration although live Postfix and PTR were healthy.
It now checks:

| Property | Value |
| --- | --- |
| `MAIL_HOSTNAME` | `mx.matemail.pro` |
| `MAIL_DOMAIN` (product apex) | `matemail.pro` |
| `SENDER_DOMAIN` | `mail.matemail.pro` |
| `MX_CHECK_DOMAIN` | `mail.matemail.pro` (the apex is not an MX target) |
| `MAIL_CERT_NAME` | `matemail-mail-dual` (renewal lineage) |
| `DKIM_SELECTOR` | `mm1` |

The certificate must be selected by renewal lineage, not SMTP DNS name.
Collector config: `/opt/MateMailMonitoring/collector.env`. The existing
`matemail-collector.timer` continues to run, no new scheduler needed.

## Dynamic onboarding and optional DMARC reporting

**Normal onboarding does not require a new TXT record in the MateMail provider
zone for each new customer domain.** Hub generates MX, SPF, DKIM, DMARC and
optional Autodiscover instructions from the domain's actual name, its DKIM key,
and the platform-wide SMTP/SPF configuration. There is no list of company names
or provider-side per-customer authorization records in application code.

By default (`DMARC_AGGREGATE_REPORTING_ENABLED=False`), the DNS instruction
for any new customer domain is simply:

```text
Host: _dmarc
Type: TXT
Value: v=DMARC1; p=none
```

This is valid DMARC, with no external aggregate report destination to authorize.
The existing `rua` records at the three owned domains are *not* removed by
this application change; their cleanup remains a distinct DNS edit during E5.
Changing a domain's records must be coordinated, not inferred from the UI.

### Future centralized aggregate reporting — one-time provider setup

Native Engine already contains `dmarc@mail.matemail.pro`, a 128 MB
receiving-only, login-disabled mailbox. The report domain
`mail.matemail.pro` has MX `mx.matemail.pro`; the product apex
`matemail.pro` does **not** have an MX.

When aggregate ingestion, retention, abuse-volume limits and report
authorization are operational, the platform operator may **once** publish a
wildcard TXT under the `matemail.pro` zone:

| DNS zone | Host | Type | TXT |
| --- | --- | --- | --- |
| `matemail.pro` | `*._report._dmarc.mail` | TXT | `v=DMARC1` |

This publishes `*._report._dmarc.mail.matemail.pro`. RFC 7489 §7.1 and
RFC 9990 permit this wildcard to authorize external aggregate reporting
for *any* sending domain. It removes per-customer manual provider-side
records, but **accepts reports from third parties too**. Deploy only with
volume/rate monitoring, bounds on incoming report sizes, safe parsing and
retention policy. DNS authorization alone neither validates the report's
sender nor processes its contents.

After the wildcard is published **and tested** with arbitrary DNS names,
and the ingestion controls are ready, set
`DMARC_AGGREGATE_REPORTING_ENABLED=True`. The platform-wide configurable
`DMARC_REPORT_ADDRESS` (currently `dmarc@mail.matemail.pro`) then makes
Hub generate this record for **any** new customer domain:

```text
Host: _dmarc
Type: TXT
Value: v=DMARC1; p=none; rua=mailto:dmarc@mail.matemail.pro
```

Existing customers can then edit *their own* existing `_dmarc` TXT records
if they want aggregate reporting; no additional MateMail-provider-zone record
is needed per domain. Never add duplicate DMARC policy records.

**No immediate DNS action** is required solely for E4 code and monitoring
cleanup, and no mailbox/data deletion is authorized by this phase.

## E5 retirement gates

Before removing any `.online` alias, DNS record, Native domain/mailbox, old
DKIM selector/map entry or TLS SAN:
1. Verify 3 owned domain MX/SPF/SRV and all four custom CNAMEs resolve only
   to the new targets.
2. Ensure any existing owned-domain DMARC `rua` values referencing .online are
   either removed while retaining `v=DMARC1; p=none`, or migrated to a proven
   optional reporting service. Do not block ordinary onboarding on reports.
   Confirm no live platform sender uses .online.
3. Inventory old Native mailbox message counts, forwarders and attachment
   retention; export/archive before deleting. Confirm no client still uses
   `mx.matemail.online`.
4. Verify current and historical backups; avoid deleting backup archives.
5. Check website redirects and certificates, then retire old routes and
   DNS entries in a controlled order; final clean restart and smoke tests.

## Rollback

E4 Native DB and private DKIM snapshot plus pre-change monitor files are stored
under `/opt/MateMailMigration/phase-e4/`, mode 0700. The E1 full snapshot
and E2 sender cutover snapshots remain available separately.
