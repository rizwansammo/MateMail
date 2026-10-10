# DNS-Phase 2 — Customer-facing Advanced DNS Security (source-only)

This is an additive implementation, not permission to release customer self-service.

## Safety boundary

- Standard mail onboarding remains MX/SPF/DKIM/DMARC. MTA-STS and TLS-RPT remain OPTIONAL.
- `TRANSPORT_SECURITY_SELF_SERVICE_ENABLED=False` must remain unchanged in production until DNS-Phase 3 offboarding and DNS-Phase 4 multi-tenant rollout tests complete.
- Both records use the verified existing MateMail policy gateway and dedicated TLS report recipient. Never change customer DNS, mail routing or TLS policy mode automatically.
- The MTA-STS policy remains fixed to `testing`; no customer input can set `enforce`.

## New DNS-publication verification

Existing `POST /api/domains/{uuid}/transport-security/verify-dns/` remains backwards-compatible:

1. Before HTTPS is ready, verify ownership TXT, exact policy CNAME and all MX hosts; queue the separate root-only HTTPS provisioner, never claim an active policy.
2. Once the existing root worker has independently verified HTTPS and reported `ready`, the same button rechecks ownership/CNAME/MX **and** checks the exact published `_mta-sts` and `_smtp._tls` TXT records.
3. Only an actual matching `_mta-sts` TXT advances the lifecycle from `ready` to `active`. TLS-RPT TXT has an independently tracked result and does not masquerade as verified when missing.
4. A public-DNS timeout returns 503 and leaves prior TXT evidence unchanged; missing/incorrect records return `missing`, not `verified`.
5. The database stores the last check time and evidence timestamps. A prior check is **not** continuous DNS monitoring, and the Hub explicitly identifies it as last-checked evidence.
6. `publish_ready` remains separate from `verification_status`. Customers never receive copyable TXT instructions until their own backend publication gate is ready.
7. All mutating API calls require tenant ownership, Owner/Admin and verified user email. Cross-tenant access is 404. Rate limits apply.

## Guided Hub UX

Domain Details > Advanced security retains the established design system and shows:

- Explanation of the optional service, approval/readiness and fixed testing mode.
- Four clear steps: CNAME, certificate, individual TXT publication, final record verification.
- Individual per-record `Ready to publish` versus `DNS verified / Missing / Not checked` labels.
- Verification timestamps and manual recheck button only when self-service is released.
- Existing DNS zone/full FQDN caution and links to tenant-isolated TLS report summaries.
- No provider API writes, automatic DNS edits or certificate operations from the browser.

## Defense-in-depth deletion guard

The normal Domain DELETE now refuses while the domain has an enabled Advanced Security request or any potentially provisioned lifecycle, including partial failures. This prevents deleting the database ownership pointer while leaving a cached HTTPS MTA-STS policy at the edge. The guard also holds an atomic domain row lock.

DNS-Phase 3 must implement the **actual, carefully staged offboarding** flow, with DNS transition, cached policy lifetime, Nginx/ACME cleanup and rollback proof. This defensive 409 is NOT a replacement for that work.

## Release steps

1. Review source diff and CI: backend migrations, transport tests, domain-deletion durability tests, frontend lint/build, deployment checks.
2. Merge only after all CI is green and approved.
3. Manual operator deployment only; verify migrations and images by actual production SHA.
4. Keep `TRANSPORT_SECURITY_SELF_SERVICE_ENABLED=False`. Never test unsafe tenant opt-in against real customer domains.
5. Phase 3 must complete before any customer-facing activation.

