# Phase 8 Production Acceptance Matrix
**Date:** 2026-10-23 (Asia/Dhaka)
**Release:** `1638299f6046e8493d74e64077ef7599b45ba6d7`
**Disposition:** PARTIAL, NOT FINAL ACCEPTANCE.

| Criterion | Evidence | Status |
| --- | --- | --- |
| GitHub main and application release SHA | GitHub main plus Docker inspect | Verified |
| Native immutable digest | Docker config and RepoDigests | Verified |
| Docker service health | Active containers healthy, including ClamAV | Verified (point in time) |
| Native schema 16 | Live `schema_version` SQL query | Verified |
| Django migrations | `showmigrations --plan`, zero unchecked | Verified |
| Production security checks | `manage.py check --deploy`, zero issues | Verified |
| MFA feature flags | Actual running Backend, Worker and Beat | Verified |
| Distinct valid Hub/PostBox encryption keys | Fernet validation inside backend; no disclosure | Verified |
| Organization require readiness | `can_activate_require()` True | Verified |
| No silent organization enforcement | Live ORM: four tenants, all `off` | Verified |
| Hub/PostBox HTTP availability | Both HTTP 200 | Verified |
| Historical protocol revocation/isolation | Saved isolated TestLab logs | Verified as historical evidence only |
| Full production MFA and real client acceptance | Owner-controlled end-to-end check not performed here | Not Verified |
| Current GitHub secret scanning alert state | Connector does not expose necessary alerts | Not Verified |
| Current OS/image CVE posture | Historical high/critical findings, no fresh reviewed scan | Open |
| Independent current disaster recovery | Azure job failed 2026-10-22 | Open |
| Production post-cleanup health | Ten key production containers running and healthy after scoped TestLab Docker cleanup | Verified for sampled containers; final broader regression pending |
| Final release closing | Security gates above outstanding | Pending |

## Historical GitHub runs
Application CI, Native candidate publish and Native security runs completed successfully against the merged SHA. Isolated IMAPS/SMTP acceptance belongs to an earlier PR commit and is labeled accordingly.

## Interpretation
A healthy production service and green tests are not a blanket security certification. Protected customer policies and credentials were not altered.

## Supplemental non-invasive protocol checks
On 2026-10-23, independent unauthenticated TLS negotiations from MateServer showed:
- `mx.matemail.pro:993` IMAPS, TLS 1.3, certificate verified.
- `mx.matemail.pro:587` SMTP greeting and EHLO 250, STARTTLS advertised, STARTTLS negotiated TLS 1.3 and certificate verified after a transient first-attempt greeting timeout. The subsequent retry succeeded.
- No credentials were used and no mail was transmitted. **Authenticated real-client functionality remains Not Verified**.

A bounded `find /app -maxdepth 8 -type d -name braces` inside the running Frontend Container found no result. This is supporting evidence for a development-only dependency boundary, not a comprehensive package inventory or a patched-vulnerability claim.

Local Restic `--no-lock check` passed 15/15 snapshots and reported no errors; actual current independent restore remains unverified.

## Phase 8B exact-code reconciliation on 2026-10-23
See `PHASE8_B_SECURITY_ACCEPTANCE_RECONCILIATION_20261023.md` for the full source/CI matrix. Old issue #146's all-mailbox Native login-verifier regression was resolved in `backend/apps/postbox/auth.py` through `canary_scope.native_primary_required_for_mailbox()` with isolated legacy noncanary tests. The separate actual Browser/TOTP/Native/SMTP/IMAP single-identity walkthrough and Gmail/Outlook/Thunderbird real-client testing still lack approved evidence. CI and historical isolated real-protocol tests cannot be conflated into a passed production E2E acceptance. B evidence reconciliation is finished; full release acceptance remains **Not Verified**.
