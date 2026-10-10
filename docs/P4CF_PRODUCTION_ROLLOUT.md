# P4-C.F — Controlled Production Transport-Security Rollout

## Scope and hard stops
This phase promotes P4-C.B–E development from reviewed GitHub source to a *staged* production candidate. Never conflate source merge, image publication, app deployment, worker installation, DNS publication or an operational MTA-STS policy.

The following were observed during preflight on 2026-10-09:
- All active MateMail application and Native Engine containers healthy.
- Deployed app image: 2d4f38353380f4b1994e7ae4e49d2facd3c2476b, older than merged P4-C.B–E.
- Gateway A mta-sts-gateway.matemail.pro resolves to 169.58.114.252 on public and authoritative resolvers.
- Certbot 4.0.0 and Nginx configuration validation successful.
- Existing manual MTA-STS Nginx vhosts: mail.matemail.pro, matedesk.pro, myrightbd.org and netamate.com.
- mta-sts.matedesk.pro serves mode: testing HTTPS policy; several other legacy hostnames were not reachable over HTTPS in the sample checks.
- New dynamic worker was not installed; new application TLS-RPT/transport models were not deployed.
- Dedicated tlsrpt@mail.matemail.pro Dovecot user/INBOX exists; mailbox access via application and real report ingestion NOT yet tested.
- MateMail and MateServer last backup service executions succeeded. Restore proof still required.

## Deployment staging changes
The manual deployment workflow now archives deploy/transport-security from the requested **exact Git SHA**, independently of custom-host worker and Docker images. It copies only the archive to root-protected /opt/MateMail/app/.deploy/ and checks Python syntax / installer shell syntax. It **never calls install.sh, creates a production credential, changes Nginx, enables a timer, or sets any feature flag**. Previous SHA commits without a transport worker still deploy/roll back normally.

## Gates before any live changes
1. Validate SHA-pinned GHCR backend/frontend availability and CI; compare with running revision.
2. Confirm Azure and local backups, integrity checks, and a workable restore rehearsal; snapshot existing Nginx policy site files and certificate names before touching any hostname.
3. Obtain user approval for the manual deploy workflow. After deployment ensure migration applied, containers healthy, PostBox send/receive unchanged, tenant endpoints return 404 cross-tenant, and all features OFF.
4. Review the staged bundle SHA, run the files-only installer under root approval. Keep transport worker timer **disabled**. Create root-only 0600 /etc/matemail/transport-security.env with purpose-scoped secret matching the backend, approved public IP, and loopback API. Never reveal credentials in chat/logs.
5. Independently verify all tenant DNS prerequisites. Initial CNAME is mta-sts.<tenant-domain> -> mta-sts-gateway.matemail.pro, but only if the tenant is ownership-verified and no legacy Nginx vhost collision exists. No MX/SPF/DKIM/DMARC changes.
6. Use an explicitly authorized **test/canary** tenant-domain with no conflicting MTA-STS vhost; enable opt-in, verify DNS. Confirm only that domain is eligible before allowing the worker timer and isolated ACME issuance. Hostname-specific HTTPS certificate, policy body, CRLF, MX and HTTP/HTTPS behavior must validate independently.
7. Verify dedicated TLS-RPT receiver end-to-end using controlled RFC8460 JSON/GZIP mail. First enable ingest alone while DNS-publication remains OFF. Verify read-only polling, report deduplication, privacy, per-tenant access and platform health.
8. Only with evidence and separate operator authorization, enable receiver-verified / DNS-publication gates. Publish customer _mta-sts and _smtp._tls TXT one-by-one, respecting each backend publish_ready flag. Start MTA-STS in **testing** only, never auto-enforce.
9. Migrate manually configured legacy hostnames one by one after backup, exact ownership and certificate verification. Never delete or overwrite an unmanaged Nginx vhost to bypass worker collision checks. Preserve existing DNS + HTTPS caches until safe rotation and rollback have been proven.
10. Capture external validation, production smoke tests and recovery evidence before marking phase complete. P4-D final audit then scores /100.

## Not permitted by code staging
- Changing production DNS, SPF/DKIM/DMARC, mail routing or existing customer vhosts.
- Enabling transport security by default or auto-issuing certificates.
- Migrating the four legacy policy hosts without a separate per-domain plan.
- Assuming a DNS A gateway proves HTTPS: each policy hostname needs its own trusted certificate.

## Status
Source-only deployment preparation. The live rollout remains gated by verified backup/restore evidence and operator approval.

## 2026-10-10 Operational verification checkpoint

- P4-C.F canary `netamate.com`: public DNS CNAME and `_mta-sts` TXT verified, HTTPS policy `mode: testing` and hostname-specific certificate validated.
- Docker applications deployed at `43f2f86eae7eb8f1b3433249b4bb4e785fc3fc2a` and healthy before this patch; signed TLS-RPT parser running in read-only polling mode.
- Certbot systemd timer is enabled and active; per-certificate staging renewal dry-run must finish before renewal is marked tested.
- Customer opt-in self-service and MTA-STS provisioning timer are intentionally still OFF until safe offboarding and lifecycle controls are production-verified.
- A domain in `READY` cannot be disabled by the customer API (409 Managed removal required); preserving the HTTPS policy and honoring cached max_age protects continued mail delivery. Full customer offboarding remains an **operator-run** workflow, not a completed automatic process.
- No external independent TLS-RPT report for `netamate.com` has yet been received; do not mark this validation as passed or attest `TLS_RPT_RECEIVER_VERIFIED` on local synthetic results alone.
- New RFC8460 intake hardening: reject email without the mandatory `TLS-Report-Domain` and `TLS-Report-Submitter` headers, require agreement with signed JSON policy and contact-info domain, validate every MIME attachment before writing any aggregate, and retain DKIM + read-only IMAP gates.
- External report arrival requires publishing `_smtp._tls.<domain>` TXT; it is impossible to prove **real reporter-initiated** report receipt on the canary prior to advertising this TXT. Secure local signed-message tests and a separate staged operator approval are prerequisite; then publish canary only and monitor reporting over the provider's reporting interval.
- Retain pending full restore rehearsal in the future DR checklist as previously agreed; backup integrity checks are not a fresh full-restore proof.
