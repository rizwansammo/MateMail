# P4-C.C — Dynamic MTA-STS HTTPS worker (safe staging)

This is a **source-only** implementation. It does not run on MateServer until an
operator independently opts in during P4-C.F. Existing hardcoded sites for
`mail.matemail.pro`, `matedesk.pro`, `netamate.com`, `myrightbd.org`
are left untouched, including their Nginx vhosts, DNS and certificates.

## Trust boundaries

- Customer's `Domain` must be globally ownership-VERIFIED by its workspace
  before creating optional `DomainTransportSecurity` config. Owner/Admin only.
- Customer-facing `POST .../verify-dns/` rechecks the actual ownership TXT,
  an exact CNAME `mta-sts.<domain>` pointing to the explicitly configured
  policy gateway and **every** MX being `mx.matemail.pro`. Rate limited.
- A dedicated internal API under `/api/internal/transport-security/` is guarded
  by a **new, purpose-specific shared secret** (not API keys or customer JWT).
  Public Nginx already denies the complete `/api/internal/` prefix.
- Pending list + authorization are only available when
  `TRANSPORT_SECURITY_PROVISIONING_ENABLED=True`. Default OFF.
- Root host worker independently checks CNAME and MX, checks that policy
  gateway resolves to the operator-approved IP, re-authorizes **before and
  after** ACME and checks full job identity/policy revision.
- Worker writes only `matemail-transport-sts-<hostname>.conf` vhosts bearing
  its marker; refuses conflicting sites or symlinks, validates and reloads
  Nginx, restores previous file on failure; uses existing Certbot webroot and
  systemd infrastructure. HTTP serves only ACME; HTTPS serves only the exact
  `/.well-known/mta-sts.txt` policy, all other paths 404.
- Policy is **always** `mode: testing`, MX matches current provider hostname,
  TTL max_age 86400, file uses RFC 8461 CRLF. Real HTTPS fetch checks public
  hostname, CA trust chain, content-type and exact body. No TLS bypass.
- Successful worker reports `READY` (not ACTIVE); TXT publication, DNS status
  and TLS-RPT report ingestion are deferred to P4-C.D/E/F.

## Production gates (do NOT run during phase C)

1. Operate a dedicated `mta-sts-gateway.matemail.pro` A pointing at the
   intended public Nginx address (currently MateServer, to be confirmed).
2. Check MX, CNAME, ownership TXT for each opted-in client domain. Ensure
   canonical `mta-sts.<domain>` hostname is *not* a preexisting web/custom
   hostname. Cloudflare proxy on CNAME must be **DNS-only** for ACME.
3. Deploy code/migration, stage the worker via `install.sh --install-only`.
   Do NOT enable its timer yet.
4. Create root-only `/etc/matemail/transport-security.env` (0600) with
   `TRANSPORT_SECURITY_PROVISIONER_SECRET`, approved IP, internal API address.
   The corresponding Django/Compose secret must match; never print it.
5. After isolated Nginx/ACME tests and P4-C.D/E readiness, set feature gates
   and enable systemd timer in a separate operator-approved rollout.
6. The existing manual MTA-STS vhosts must be migrated individually and
   only during P4-C.F with explicit backups; the worker refuses collisions.

**No customer DNS record should be published** unless its specific field has
`publish_ready=true`; the TLS-RPT TXT remains withheld until P4-C.E. The
standard mail-domain onboarding and mail routing are unaffected.
