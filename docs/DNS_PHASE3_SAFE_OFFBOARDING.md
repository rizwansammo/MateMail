# DNS-Phase 3 — Cache-Safe Optional MTA-STS/TLS-RPT Offboarding

**Source-only controlled release. Do not activate customer self-service in this phase.**

## Invariants

1. Standard MX/SPF/DKIM/DMARC, Postfix, Dovecot, unrelated Nginx sites, and customer mail storage must never change during Advanced Security retirement.
2. Customers and platform admins cannot supply policy mode, tenant, host, certificate, grace period or worker filesystem paths. Authenticated Owner/Admin can request a staged disable only when `TRANSPORT_SECURITY_SELF_SERVICE_ENABLED=True`, which remains **OFF** in production through Phase 3.
3. The backend holds the owning Domain record throughout retirement. Domain DELETE fails closed during all active/pending/error/deactivating/draining states, and the OneToOne relation uses Django `PROTECT` to prevent accidental ORM cascade deletion. Only a disabled, fully retired configuration can be discarded during a normal Domain DELETE.
4. Host mutations use only the installed root-owned worker via a narrowly scoped loopback API and separate `TRANSPORT_SECURITY_PROVISIONER_SECRET`. Public Nginx denies `/api/internal/`. Hostnames derive from the verified tenant Domain object.
5. Do not ever enable `mode: enforce`. The only modes used by this implementation are `testing` while active and `none` for retirement. The HTTPS certificate and DNS policy must be maintained while any sender may have a cached policy.

## Retirement state machine

```
READY / ACTIVE / PROVISIONING / ERROR
        |
        | Owner/Admin POST {"enabled": false}
        v
  DEACTIVATING --root worker validates exact resource ownership-->
        |          verified HTTPS returns RFC8461 mode:none, max_age 86400
        v
     DRAINING <----- worker checks BOTH public TXT record absence on
        |             Cloudflare 1.1.1.1 and Google 8.8.8.8
        |             (any record reappearance resets the absence clock)
        |
        | Minimum 48h since both mode:none proof AND first continuous
        | confirmed DNS absence, then independent re-authorization
        v
    CLEANUP (root-only, narrowly scoped)
        |
        | guarded managed Nginx vhost disable with validation + rollback;
        | exactly matching single-domain certbot lineage deletion;
        | remove only validated public mode:none policy path;
        | backend completion acknowledgement
        v
     DISABLED
```

A never-installed/partially installed policy can use a no-edge alternative only if
no certificate, policy subtree, managed or unmanaged Nginx site, or published
security TXT is present. Unexpected state blocks cleanup for operator review.

The worker maintains a root-owned 0600 retirement journal for idempotent retries
if it crashes between Nginx reload, cert deletion, policy unlink and DB
acknowledgement. A failure must not erase the owning DB row or grant new tenant
claim to the same domain. Nginx rollback restores the original symlink if
validation or reload fails. Certificate deletion requires an exact single-host
Certbot lineage and an independent scan for Nginx references.

**The customer must remove both `_mta-sts` and `_smtp._tls` TXT records only
AFTER mode:none is confirmed, and keep the `mta-sts` CNAME until offboarding
is marked complete.** The worker does not modify DNS at the registrar. After
completion the customer may remove the CNAME. Retirements do not alter MX,
SPF, DKIM or DMARC.

## RFC 8461 and cache safety

`max_age` has been fixed to 86400 seconds in the current MateMail policy.
The final 48h window, additionally anchored to mode:none confirmation,
exceeds that cache lifetime. RFC 8461 Section 8.3 suggests publishing a new
MTA-STS TXT id when switching to mode:none to accelerate discovery; this flow
uses a conservative cache-expiry window after both TXT records have disappeared
instead of relying on remote providers to refresh an old id. Before changing
future policy max_age defaults, update the retirement grace rule; older policies
with longer cache lifetimes require operator-controlled extended retirement.

## Release & verification

1. Full CI: Django migrations/check/deploy, targeted and complete test suite,
   standalone root worker unit tests (no privileged operations), Next.js
   lint/build, production Compose isolation and runtime smoke.
2. Verify on test fixtures: ownership loss, separate tenants, in-progress
   provisioning, DNS SERVFAIL, missing/remnant TXT, cert mismatch/shared SAN,
   Nginx collision, atomic rollback and worker resume after partial cleanup.
3. Do not automatically install/activate worker changes with deployment
   workflow. Stage the exact SHA-matched release artifact and explicitly
   verify installed worker version under an approved maintenance operation.
4. Production `TRANSPORT_SECURITY_SELF_SERVICE_ENABLED=False` until DNS-Phase
   4 safe end-to-end test on a dedicated non-customer canary, verified cached
   HTTPS mode:none availability, DNS cutover and final clean removal.
5. Before accepting a new customer's live opt-out, prove that the installed
   worker supports the new retirement API; an old worker would leave their
   request in `deactivating`.

**This phase does not migrate, disable or offboard the existing production
`netamate.com` canary.**
