# MateMail Phase 3: System Mail Configuration Backend QA

Date: 2026-10-12

## Change scope

Adds the following platform-only, authenticated API:

- `GET /api/platform/system-mail/configuration/`
- `POST /api/platform/system-mail/configuration/`

The API manages strictly validated, versioned **drafts** for the proposed
`system.matemail.pro` identity and five planned system mailbox roles:
noreply, security, operations, dmarc and tlsrpt.

Saving a draft does not provision domains, mailboxes or credentials; it does
not switch an operational sender, change report ingestion, publish DNS, touch
DKIM, or call the Native Engine. Runtime sender and report receiver values
remain sourced from the existing environment configuration.

## Security and consistency

- Platform administrators only, via existing `IsAuthenticated` and
  `IsPlatformAdmin` (which excludes API keys). Tenant users and anonymous
  users are denied.
- The staged domain must already be reserved as PLATFORM by Phase 2.
- Address roles must match the exact canonical local parts under
  `system.matemail.pro`.
- Undeclared configuration fields, attempted activation flags and secret
  inputs are rejected; JSON arrays are rejected with HTTP 400.
- A current `expected_revision` is mandatory; stale writers receive HTTP 409.
- Immutable historical revision rows are protected by a PostgreSQL trigger.
- Every successful draft save requires an atomic Platform Audit Log entry.
  If logging fails, the draft transaction rolls back.
- API responses clearly state that activation and provisioning are not
  supported by this phase. The existing environment is the operational source.

## Isolated QA evidence

Executed using the existing MateMail backend image with the feature branch
Python code mounted from a temporary copy, against an isolated PostgreSQL 16
container.

| Gate | Result |
| --- | --- |
| Python static syntax | PASS |
| Django makemigrations --check --dry-run | PASS (no drift) |
| Platform API, session/authentication, cross-tenant and immutable revision tests | 71 / 71 passed |
| Phase 2 + Phase 3 domain, platform admin and deletion regression | 133 / 133 passed |
| Phase 3 forward/reverse/reapply schema rehearsal | PASS |
| Immutable revision database trigger after reapply | PASS |
| GitHub Actions runs started by this phase | ZERO |

The 71-case suite overlaps the 133-case suite; do not sum those figures.
The isolated test configuration did not set `DKIM_ENCRYPTION_KEY`, because no
real DKIM private key was restored to that test database. This log message is
from the test environment and not a production configuration change.

## What is not done

This is the Phase 3 **configuration draft foundation**, not live system mail
activation or provisioning. There is no Native Engine platform management
credential route, no DNS validation/cutover and no system mailbox creation.
Reporting ingestion and transactional sending still use their existing
operational configuration.

Future Phase 3 work must add guarded provisioning design, readiness evidence
and live configuration resolution without overriding the Phase 5 migration
gate. The new settings cannot be considered effective until that separate
validated activation path exists.

## Production release decision

**NOT DEPLOYED**. Release 1 groups Phase 2 + Phase 3 only when the whole
package is ready, existing SMTP/IMAP/DKIM behavior has been verified, the
release CI gate passes and the operator explicitly authorizes deployment.
This phase does not alter the Phase 2 DNS Canary or production mail.
