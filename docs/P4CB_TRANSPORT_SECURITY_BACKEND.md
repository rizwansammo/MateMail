# P4-C.B — Multi-Tenant Transport Security Backend (2026-10-09)

This phase deliberately implements **configuration intent only**. It does **not** change production DNS, Nginx, Certbot, Postfix, Dovecot, Celery schedules, or report routing. Deploying this backend component alone is *not* an MTA-STS/TLS-RPT rollout.

## Design contract

- Standard mail-domain onboarding remains MX/SPF/DKIM/DMARC; advanced MTA-STS/TLS-RPT is optional and never blocks mail.
- One `DomainTransportSecurity` configuration per existing verified `Domain` enforced by DB OneToOne; the `Domain` already has one verified owner/tenant globally.
- GET `/api/domains/<uuid>/transport-security/` is tenant-scoped, read-only (no implicit DB row).
- POST to the same URL accepts **exactly** `{"enabled":true}` or `{"enabled":false}`. It uses Owner/Admin permission, email verification for writes, verified domain ownership, approved mail-capable tenant, and an atomic domain row lock. Cross-tenant lookups return 404.
- API opt-in requires `TRANSPORT_SECURITY_SELF_SERVICE_ENABLED=true`; it defaults to **false** until P4-C.C/D/E have produced verified policy hosting, ingress and reporting. The API can be merged/deployed with zero behaviour change.
- Successful opt-in enters `pending_dns`, never `active`, never triggers automation. Subsequent opt-ins are idempotent; a new opt-in cycle rotates the STS `id`.
- `policy_mode=testing`, `max_age_seconds=86400`, dynamic hostname `mta-sts.<domain>` and current Mail Engine MX `mx.matemail.pro`. Neither policy mode nor MX/SSL state can be set by the customer.
- `dns_records` are an **internal preview**, every record explicitly `publish_ready=false` and top-level `can_publish_dns=false` in Phase B. The CNAME target is null until the separately configured `MTA_STS_POLICY_EDGE_TARGET` is approved/verified. Do **not** reuse the Hub/PostBox CNAME target without a real policy-serving HTTPS vhost.
- TLS-RPT destination defaults to the verified dedicated `tlsrpt@mail.matemail.pro` mailbox, but publishing `_smtp._tls` must wait for P4-C.E JSON/GZIP ingestion, privacy and tenant isolation. It must not be sent to the DMARC XML parser.
- Once edge provisioning/activation begins, a tenant cannot disable via this API. P4-C.F will coordinate removal of HTTPS and published records; never silently deactivate an MTA-STS host while remote MTAs cache a policy.
- DB migration `transport_security/0001_initial` is additive. Existing Domain, DNSCheck, mailbox and tenant tables remain unchanged.

## Subsequent sub-phases

- **P4-C.C:** Root-owned dynamic provisioning worker, hostname ownership verification, shared host-native Nginx/Certbot, HTTPS policy validation, policy-version updates and rollback. Strict authorization on internal API, all edge changes fail-closed.
- **P4-C.D:** Premium optional Advanced Transport Security section in Hub Domain Details. DNS zone-aware record display, statuses, Copy, Verify, opt-in UI; no effect on Standard Mail Setup.
- **P4-C.E:** Independent secure TLS-RPT JSON/GZIP parser from a dedicated read-only mailbox, dedupe/bounds/retention, per-tenant read APIs.
- **P4-C.F:** Gradual production migration, actual MX/certificate/DNS and report receiver proof, authoritative DNS checks, cleanup of the fixed four-domain installer, end-to-end tests. Never auto-enforce.

## Commands to validate in CI

```bash
cd backend
python manage.py makemigrations --check --dry-run
python manage.py check
python manage.py test tests.test_transport_security -v 2
```

No `enforce` mode, no DNS records and no cross-tenant report access are authorised by this stage.
