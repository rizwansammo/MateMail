# DNS-Phase 4 — Controlled Customer Advanced Transport Security Release

## Non-negotiable safety gates

- Phase 2–3 are present in Main. **This runbook is a release plan, not an instruction to make unattended changes.**
- The existing mail service, MX, SPF, DKIM, DMARC, Postfix/Dovecot and other tenants must not be touched.
- Customer self-service remains `TRANSPORT_SECURITY_SELF_SERVICE_ENABLED=False` until the final independent canary has verified a full start, DNS publication, mode:none retirement and resource cleanup.
- Never use the live `netamate.com` policy or another active tenant for a destructive canary.
- Staging a new root-owned worker is not the same as installing it. The manual GitHub Actions `Deploy to MateServer` workflow installs only the app images and stages the worker under `/opt/MateMail/app/.deploy/transport-security.<SHA>`.
- Never automatically promote an untested worker. An old worker and new Backend must not receive live retirements.

## Release preflight

1. Select the exact **current Main SHA** (not a previous static SHA in these docs). Check all Main CI jobs and the GHCR image-publishing job are successful for that SHA.
2. Review active parallel changes (especially the separate P4-D security remediation conversation). If other work has advanced Main, review those commits; never deploy a mismatched SHA or change old configs in-place.
3. Verify current MateServer containers, mail queue, host Nginx, UFW, certificate renewal timer, MTA-STS canary HTTPS, backing database, verified Azure backup and restore path.
4. Prepare a DB dump / rollback plan for additive migrations `transport_security 0002` and `0003`. Rollback across a DB schema upgrade requires operator review; never run destructive migration rollback blindly.
5. Capture a protected backup of the installed root worker, systemd unit, renew hook, and exact-revision staged artifact checksums before touching them.
6. Verify the dedicated test domain **under a zone whose DNS the operator controls**; determine how it can satisfy the real mail-domain ownership/MX eligibility gates without altering business domains.

## Controlled installation (manual approval required)

1. The human operator manually runs GitHub Actions > `Deploy to MateServer`, sets `image_tag` to the exact approved Main commit SHA, and types `MateServer` as the confirmation input.
2. Verify Docker health, release-image digests, new migrations, public HTTPS and Mail Engine after the deployment. **Do not activate customer self-service.**
3. Explicitly pause `matemail-transport-sts-provisioner.timer`. Back up installed root worker and hook with restrictive root permissions.
4. Validate exact SHA worker bundle under `/opt/MateMail/app/.deploy/transport-security.<SHA>/`; compare hash to the approved GitHub file. `python3 -m py_compile`, `sh -n` on scripts, `nginx -t`, and root-owned file permissions must pass.
5. Explicit operator approval to stage the new root worker via its provided `install.sh --install-only` (does not enable/start timer). Verify no unexpected unit or hook changes and that the installed binary contains the new retirement functions.
6. Resume the existing worker timer only after worker/backend compatibility, rollback path, Nginx validity and mail health checks pass.
7. **Do not alter the existing NetaMate canary policy.** It must continue returning RFC8461 `mode: testing` with valid HTTPS.

## Isolated domain-UUID canary

The operator selects ONE independent, non-customer, verified mail-domain ID as the canary. Its new DNS zone must be under the operator's control and must not replace any current customer MX or domain.
While the global self-service toggle remains False, authorize exactly that domain by setting:

`TRANSPORT_SECURITY_CANARY_DOMAIN_IDS=<verified-canary-Domain-UUID>`

This allowlist defaults to empty. Never put a tenant ID, domain-name wildcard or real customer ID into it. An exact UUID is insufficient by itself to bypass verified email, tenant ownership and Owner/Admin permissions.

1. After approved configuration/restart, verify only the canary sees the opt-in control. Unapproved tenant mutations must return 503/404; all existing mail continues.
2. Canary owner/admin opts in, adds its CNAME and required TXT records to their controlled DNS zone, verifies ownership/MX/CNAME, then waits for root worker to issue an independent HTTPS certificate and serve `mode: testing`.
3. After certificate success, publish `_mta-sts` and `_smtp._tls` TXT only when individually marked ready; re-verify publicly via several resolvers. Backend must not claim STS Active without exact TXT match.
4. Perform a controlled offboarding request **only on the isolated canary**. Ensure HTTPS serves valid `mode: none` and the client sees the draining state. Follow the current guided DNS removal instructions; leave CNAME intact.
5. Observe both TXT absent using independent public resolvers, then monitor the full **48+ hour** cache-draining window; maintain HTTPS and certificate availability throughout. Any record reappearance resets the timer. Read the worker journal; untrusted or malformed sites must fail closed.
6. After the timer, prove cleanup affects only the exact canary Nginx vhost, certificate lineage and policy subtree; confirm root worker retry/idempotency, disabled state, and normal domain Delete API still respects mail-engine DKIM cleanup safety.
7. Verify all unrelated tenant HTTPS sites, live NetaMate MTA-STS policy, backend, SMTP/IMAPS, mail queue, DNS and certificate renewal remain unaffected.

## Global release decision (a distinct approved change)

Only after an independently observed full canary path, documented logs, no High/Critical lifecycle findings, tenant-isolation test, and at least 48 hours of real wait:
- Obtain explicit owner approval to set `TRANSPORT_SECURITY_SELF_SERVICE_ENABLED=True`.
- Remove or clear the canary UUID allowlist once global release is controlled.
- Perform short observation window and verify alerts, opt-in, verify, offboarding and data isolation.
- Retain the ability to turn global access OFF without destroying any already-active policy (outstanding retirements remain durable and must continue processing).

**Exit rule:** If the manual GitHub workflow, operator DNS edits, root-worker install, mandatory 48h observation or final consent is pending, **DNS-Phase 4 is NOT complete**. Record exact blockers and leave customer self-service OFF.

## Security and RFC notes

RFC8461 §8.3 recommends a new `_mta-sts` TXT policy id when publishing `mode:none` to prompt early refresh. The existing implementation conservatively waits beyond the fixed 24-hour policy max_age; this is intentionally less immediate than the RFC recommendation. A future protocol improvement can introduce a managed retirement policy id and transition steps, but may not reduce the cache waiting safety interval.
