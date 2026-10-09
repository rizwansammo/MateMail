# P4-C.D — Hub Advanced Transport Security UI

## Scope (source only)
The existing Hub **Domain Details > Advanced security** tab now renders a per-domain, tenant-scoped, optional MTA-STS/TLS-RPT panel. No production settings, mail engine, gateway, certificate, DNS, service, or migration is changed by this phase.

- GET `/api/domains/{id}/transport-security/` when visiting the tab.
- Read-only `self_service_available` API field mirrors `TRANSPORT_SECURITY_SELF_SERVICE_ENABLED`, which remains **false by default**.
- Owner/Admin and verified email are required for the explicit opt-in/out confirmation workflow. Verified domain ownership is required for opt-in. Server enforces the same gates independently.
- Requests use only `{"enabled":true|false}`; no policy mode, MX, tenant, certificate or lifecycle is client-settable.
- The Verify DNS button calls the existing server-side check, which independently verifies current ownership TXT, the approved policy-gateway CNAME, and *all* MX records. UI never equates DNS check success with fully active protection.
- Each backend DNS record includes its individual `publish_ready` flag. When false, its value and copy actions are withheld, even though the backend returns a preview. Do not publish unapproved DNS records.
- MTA-STS remains fixed to **testing**. TLS-RPT publication remains gated pending P4-C.E report ingestion.
- Backend remains the source of truth for provisioning lifecycle, certificate status, verified dates and error messages.
- Existing standard DNS onboarding and mail setup tabs stay unchanged. The UI does not add records to a DNS provider.
- When edge provisioning state is no longer safely reversible, the UI does not offer a disable action; managed removal is required.

## Security / release verification
1. With `TRANSPORT_SECURITY_SELF_SERVICE_ENABLED=false`: tab loads a read-only preview, displays release pending, and does **not** allow opt-in.
2. As tenant read-only member: tab can show available information but mutating actions must not be offered. Cross-tenant domain access must remain 404.
3. Unverified domain or email: opt-in is blocked. API enforces independently.
4. When the gate is explicitly enabled at a later operator-approved rollout, opt-in enters `pending_dns`; it does not activate an HTTPS policy.
5. All DNS values are hidden and non-copyable until *their individual* `publish_ready=true`.
6. DNS verification failure / 429 is surfaced as an error; neither activation nor provision is claimed.
7. After provisioning starts, opt-out requires separately managed deactivation, preserving cached policies.
8. Run frontend lint/build and backend transport-security tests in CI. Keep both feature gates disabled through P4-C.E; production rollout is P4-C.F.

## Next work
- **P4-C.E**: securely ingest TLS-RPT JSON/GZIP reports, isolate by tenant, add report APIs and monitoring; only then evaluate readiness to publish `_smtp._tls` TXT.
- **P4-C.F**: staged live DNS/HTTPS/ACME verification, release gates, backend + frontend deployment, operator-approved host timer activation, migration and rollback proof.
- **P4-D**: full security and deliverability audit plus evidence-based score.
