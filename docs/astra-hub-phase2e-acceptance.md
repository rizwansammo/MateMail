# Astra Hub Integration — Phase 2E Acceptance & Release Gates

**Status:** Implementation branch / Draft PR #98. This document is a QA plan and evidence index, not production approval.

## Completed engineering scope

- Phase 1: authenticated approved-Astra Hub shell, accessible command search, responsive/compact sidebar, light/dark, permanent tenant-level Getting Started completion and backfill.
- Phase 2A: Mailboxes and Domains, with existing create/management/verification APIs; real backend fields and permissions.
- Phase 2B: TeamBox and Forward Groups, with actual member permissions and sender policy APIs.
- Phase 2C: Aliases, Forwarding and Delegation; verified-domain/address operations, personal mailbox delegation.
- Phase 2D: Users & Access, owner/admin hierarchy, invite lifecycle, member role changes and removal.
- Phase 2E: automated **same-tenant** cross-feature journey (unverified Domain → verified-only Mailbox creation → Alias → Forwarding → Delegation → TeamBox/Forward Group pickers → Hub invitation → Global Search). Eight core feature pages checked for consistent compact geometry, 4px card radius, table headings and horizontal overflow. Read-only navigation restrictions are also tested.

### Automation (synthetic data, never production)

| Test | Scope |
|---|---|
| `astra-visual-qa.mjs` | Shell, keyboard search, themes, mobile, onboarding |
| `astra-phase2a-qa.mjs` | Mailbox/Domain APIs, details, status, restrictions |
| `astra-phase2b-qa.mjs` | TeamBox member grant, Forward Group policies |
| `astra-phase2c-qa.mjs` | Alias, Forwarding, Delegation operations |
| `astra-phase2d-qa.mjs` | Invitations, membership roles, owner/admin/read-only |
| `astra-phase2e-qa.mjs` | Joined creation, data reuse across features, resource search, nav |

The isolated GitHub Action uploads screenshots and per-phase JSON reports under `astra-hub-visual-review`. Mocked API responses test frontend wiring. They do **not** prove transactional persistence, real SMTP/IMAP routing or production DNS behavior.

## Critical release gates (not satisfied by frontend CI alone)

1. **Manual visual approval:** compare desktop and mobile screenshots against the user's original Astra reference, especially overlays, active navigation, typography, density, focus, empty/error states, and dark mode. Automated component geometry is not a pixel-diff baseline.
2. **Staging API contract run:** use a non-production workspace and authorized test accounts to test actual API results for all eight core screens. Confirm 4xx/202 behavior, provisioning failures and cross-tenant denial.
3. **Production change review:** assess migration `tenants.0005_onboarding_completed_at`, existing tenant backfill behavior, rollback plan, backup/restore readiness and one-time completion across devices.
4. **Tenant isolation & authorization:** independently verify all owner/admin/support/read-only roles and resource ownership for GET/POST/PATCH/DELETE; never trust frontend controls as security boundaries.
5. **Mail and DNS delivery:** separate real mailbox provisioning/SMTP/IMAP tests, DNS ownership MX/SPF/DKIM/DMARC and TLS report checks; do not infer these from synthetic UI tests.
6. **User acceptance:** owner reviews the complete Hub experience and explicitly approves a non-draft PR merge and deployment. No automatic production release.
7. **Other Hub pages:** Operations/Settings/Billing/advanced security screens outside Phases 2A–2D require their own design parity, API audit and QA in subsequent work. Phase 2E does not imply all original legacy pages have been replaced.
8. **Deferred feature:** `Create a mailbox for this user` on invitation is **not implemented**. It requires a separate backend provisioning/rollback workflow in Phase 5. Inviting somebody only grants Hub access.

## Evidence recording

Final workflow run IDs, head SHA, screenshot review decision, reviewer and staging acceptance results must be recorded in PR #98 after the last CI run. Keep PR in **Draft** until explicitly approved. Do not merge, run production migrations or deploy to MateServer as part of Phase 2E.
