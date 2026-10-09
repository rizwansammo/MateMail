# P4 — Mail Deliverability, DNS and Transport Hardening

Audit date: **2026-10-09**. Public DNS data and production mail/monitoring configuration were inspected read-only; **no DNS records, DKIM key material, mailbox contents or mail routes were changed**.

## Current production evidence

| Check | Observed status | Operational note |
| --- | --- | --- |
| Outbound MX identity | `mx.matemail.pro` → `169.58.114.252` | Postfix `myhostname=mx.matemail.pro` and IPv4 transport |
| Reverse DNS | `169.58.114.252` → `mx.matemail.pro` | Forward-confirmed reverse DNS succeeds |
| SMTP TLS | Public SMTP STARTTLS certificate for `mx.matemail.pro` verified | Observed validity Oct 8 2026 through Jan 6 2027; Certbot renewal timer monitored |
| SPF provider record | `_spf.matemail.pro TXT "v=spf1 ip4:169.58.114.252 -all"` | Verified with authoritative Spaceship nameserver and Cloudflare/Google/Quad9 resolvers |
| Transactional sender SPF | `mail.matemail.pro TXT "v=spf1 include:_spf.matemail.pro -all"` | Valid source chain, only current outbound IPv4 authorised |
| Customer SPF examples | `netamate.com` and `matedesk.pro` include `_spf.matemail.pro` and end with `~all` | Do NOT replace customer SPF blindly if another sender service is in use |
| Tenant DKIM | Published `mm1._domainkey.netamate.com` and `mm1._domainkey.matedesk.pro` | Verified selector map matches current Native Rspamd per-domain selector; key data remains private |
| Platform DKIM | Published `mm1._domainkey.mail.matemail.pro` | Rspamd `use_esld=false`, header-domain signing |
| DMARC | Platform sender `p=none; adkim=s; aspf=s`, checked tenant examples `p=none` | **Monitoring-only**, no `rua=` reports in checked policies. Enforcement needs evidence and opt-in, not blind rollout |
| Outbound TLS policy | Postfix `smtp_tls_security_level=dane`, `smtp_dns_support_level=dnssec` | Actual receiver coverage depends on DNSSEC/TLSA; does not imply every delivery is DANE-protected |
| Inbound MTA-STS / TLS-RPT | No records observed for the checked domains; no working `mta-sts.matemail.pro` endpoint | Optional improvement; need a valid HTTPS policy service, monitoring phase and per-domain MX validation **before** any enforce policy |
| Mail events | Sampled Postfix logs: 6 sent / 0 deferred / 0 bounced / 6 rejected | A short sample is not evidence of long-term inbox placement or reputation |
| Queue | Empty at inspection | Follow normal queue and bounce metrics |

**DNS propagation caveat:** On the first public resolver checks, `_spf.matemail.pro` TXT temporarily had no answer. Repeated checks via the authoritative nameserver and 1.1.1.1/8.8.8.8/9.9.9.9 all subsequently returned the correct policy. Do **not** claim a permanently missing SPF record, change customer TXT records, or remove the legitimate include because of a transient negative cache response.

**Important distinction:** `matemail.pro` **apex** intentionally has no inbound MX; the platform transactional sender is `mail.matemail.pro`, which owns its own mail authentication records. Do not “fix” this by inventing an MX at the apex.

## P4-A — DNS correctness / monitoring (this PR)

- Existing collector checked only that the sender SPF contained `v=spf1`. That yields **false green** when its `include:_spf.matemail.pro` target disappears or stops authorising the SMTP IPv4; receivers can produce SPF PermError while the collector reports success.
- New read-only aggregate metrics:
  - `matemail_dns_spf_provider_usable`: exactly one strict source SPF TXT record, with explicit `ip4:` mechanism authorising configured `PUBLIC_IP` and terminal `-all`.
  - `matemail_dns_spf_sender_chain_usable`: transactional sender has exactly one SPF TXT record with an **exact** `include:` mechanism for the healthy source.
- Prometheus alert `ProviderSpfAuthorizationBroken` fires only after **20 minutes** of confirmed failure. A short resolver/negative-cache incident is expected not to fire. We deliberately do not alter any tenant `domain.status`, DNS-health score, or eligibility to log in: provider DNS cannot be allowed to mark all tenants inactive through a monitoring side-effect.
- Tests reject missing, duplicated, malformed, wrong-IP and permissive source policies, false include substring matches and temporary DNS misses. CI now uses **real `promtool`** rule parsing and firing-time unit tests, not just YAML syntax.

## P4-B — Authentication/reporting decisions (requires design/ownership)

1. Keep existing per-domain DKIM private keys and selector maps. Verify signed message **headers** at an independent recipient (Gmail/Outlook/Zoho) for `spf=pass`, `dkim=pass`, `dmarc=pass` and identifier alignment; do not infer actual inbox placement from public DNS alone.
2. Select an authorised, monitored DMARC aggregate report inbox/processor before adding `rua=mailto:`. If the report mailbox is hosted outside the evaluated DMARC domain, publish the required external report receiver DNS authorization.
3. Wait for legitimate sources and forwarding/mailing-list flows to be understood from real DMARC aggregate reports; then migrate **per domain** from `p=none` → `p=quarantine` with controlled `pct` → `p=reject` only when safe. Never enforce globally merely to score higher.
4. Do not add a second SPF record. Preserve third-party sender authorisations, particularly if CRM/marketing providers are used by a tenant.

## P4-C — Transport integrity

- Existing outbound Postfix DANE/DNSSEC, inbound TLS 1.2+, dedicated hostname/PTR and certificate are working configuration controls; maintain certificate renewal and test against actual external peers.
- **MTA-STS** is recipient-domain-specific, not a single universal `mta-sts.matemail.pro` record for every customer domain. Only offer it when an HTTPS policy host and valid TLS chain are reachable for that domain, its MX set is correct, and policy can be renewed/updated. Start `mode: testing`; use `mode: enforce` only after observing and resolving reports.
- **SMTP TLS Reporting** (`_smtp._tls`) must point to an operational receiver; publishing a fake mailbox is worse than leaving reporting unconfigured. Treat certificates and DNSSEC/TLSA separately from MTA-STS. Do not claim DANE inbound without DNSSEC/TLSA proof.

## P4-D — Verification and rollout policy

- Source update uses branch → CI → promtool → explicit monitoring-only deploy, with backup/rollback of **collector script and Prometheus alert rules**. Do not run the full mail-engine deploy to introduce metrics.
- Maintain checks for DKIM key-selection integrity, public IP/PTR, MX/TLS, SMTP rejection/deferral/bounce trend and abuse limits without putting mailbox addresses, secret key material or customer names in Prometheus labels.
- Domain DNS changes require human confirmation of the DNS provider/zone and exact TXT/MX host names. Test DNS from the **authoritative** server plus two public resolvers after propagation; do not send transactional test mail to unconsenting destinations.
- A green SPF/DKIM/DMARC configuration does **not** guarantee inbox delivery. Use real sender reputation, recipient-specific SMTP responses, feedback loops and bounce classification as complementary evidence.

No production DNS action is implied by this document.
