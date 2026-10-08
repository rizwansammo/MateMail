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

## Aggregate DMARC reporting

Native Engine has a separate **receiving-only, login-disabled** mailbox
`dmarc@mail.matemail.pro` with 128 MB quota under the existing
`mail.matemail.pro` transport domain (MX 10 `mx.matemail.pro`).
Do not use `dmarc@matemail.pro`: apex `matemail.pro` does not have an MX.

To authorize external aggregate DMARC reports from all three owned domains,
publish the following three **TXT** records at the Spaceship
`matemail.pro` zone. Use `v=DMARC1` as the complete TXT value.
Spaceship automatically appends `.matemail.pro` to each Host.

| Host in Spaceship | Type | Value |
| --- | --- | --- |
| `netamate.com._report._dmarc.mail` | TXT | `v=DMARC1` |
| `matedesk.pro._report._dmarc.mail` | TXT | `v=DMARC1` |
| `rizwansammo.me._report._dmarc.mail` | TXT | `v=DMARC1` |

After all three authorization records are publicly verified, edit **only**
the existing DMARC TXT record on each domain (Namecheap, Cloudflare,
Spaceship respectively):

| Domain | Host | Type | Complete new TXT value |
| --- | --- | --- | --- |
| `netamate.com` | `_dmarc` | TXT | `v=DMARC1; p=none; rua=mailto:dmarc@mail.matemail.pro` |
| `matedesk.pro` | `_dmarc` | TXT | `v=DMARC1; p=none; rua=mailto:dmarc@mail.matemail.pro` |
| `rizwansammo.me` | `_dmarc` | TXT | `v=DMARC1; p=none; rua=mailto:dmarc@mail.matemail.pro` |

Do NOT add duplicate DMARC TXT records, change DKIM, delete the original
mailbox, change policy `p=none`, or change web A records. Confirmation gate:
authoritative DNS (not a screenshot only) shows all six changes. Then
run a fresh MateMail DNS health sweep. The old `rua` is a separate .online
dependency, so E5 cannot retire it until these checks pass.

Authentication-Results at Gmail or Microsoft and a delivered aggregate report
are additional deliverability/reporting checks, not established solely by DNS.

## E5 retirement gates

Before removing any `.online` alias, DNS record, Native domain/mailbox, old
DKIM selector/map entry or TLS SAN:
1. Verify 3 owned domain MX/SPF/SRV and all four custom CNAMEs resolve only
   to the new targets.
2. Verify all reporting TXT recipients and authorization records are published,
   the recipient mailbox exists, and no live platform sender uses .online.
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
