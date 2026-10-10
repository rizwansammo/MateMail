# MateMail Hub — Approved Astra Integration
Date: 2026-10-10
Status: **Design approved; integration branch only; no production merge/deploy authorization.**

## Approved visual baseline
Repository: `rizwansammo/MateMail-Hub-NextJS`
Reference branch: `design/astra-95-refinement-20261010`
Approved offline package: `MateMail-Hub-Astra-UI-Fixed.zip`

**Lock Astra's original design system:** exact visual shell, sharp corners, sidebar animation, modal/search geometry, input focus, typography, colors, table density and drawers. Do not reintroduce discarded `refinements.css` or extra global focus rings. The sidebar's duplicate organization block stays removed, while the organization name remains in the topbar.

**Functional authority:** MateMail's current tenant-isolated APIs, auth, role policy and mail engine. No code in PostBox or Platform Admin should be styled by Hub CSS. Do not import offline demo's localStorage state, fake records or demo authentication.

## Getting Started lifecycle — user approved

- A brand-new organization must see `Getting started` in its Hub navigation **immediately after first organization creation**.
- Data source: `GET /api/workspaces/{tenant_id}/onboarding/`.
- Existing response fields:
  - `workspace_created`
  - `domain_added`
  - `dns_verified`
  - `first_mailbox_created`
- Display while initial setup incomplete and navigate to existing `/app/onboarding`.
- Once all initial steps are complete, automatically hide the navigation entry.
- Completion must be **one-way per organization**: do not re-show after later domain/DNS changes; those belong in Domains and health monitoring.
- Do not rely on fictional prototype seed or a hard-coded default `onboardingComplete=true`.
- Onboarding completion should be persisted server-side to provide consistent state across browsers/devices; the current dynamic API alone does not persist an ever-completed latch. This is an explicitly flagged implementation detail requiring review before production, **not silently added in this setup branch**.
- During loading/error, avoid prematurely declaring the organization complete; do not create inaccessible dead links.
- Manual test cases: (1) fresh organization with no domains/mailboxes => visible, (2) partially configured => visible, (3) all four fields true => hidden, (4) later DNS fault => stays hidden, (5) different browser => same result, (6) separate tenant => isolated.

## Implementation phases

0. **User design approval** — completed on 2026-10-10.
1. **Astra Hub shell:** isolated frontend styling, exact original sidebar/topbar/search/dialog/drawer behavior; retain real auth and Next.js navigation. Integration must not alter PostBox or Platform Admin.
2. **Core API mapping:** domains, mailboxes, TeamBoxes, Forward Groups, delegation, aliases, forwarding and team members. No backend feature inventions.
3. **Advanced admin screens:** DNS, MTA-STS, DMARC/TLS, account security, hostnames, logs, quarantine, queue, billing, connected apps, backups (read-only).
4. **Global Search:** Astra's original visual component populated with permitted existing Hub page/resource metadata, no mailbox-content search and no cross-tenant leakage.
5. **Optional mailbox on member invitation:** checkbox OFF by default. This is a new **separately reviewed backend feature**; secure invitation delivery, duplicate prevention, authorization and atomic job status required. Do not fake successful provisioning.
6. **QA:** original Astra visual comparison on real browser viewports, accessibility, role/tenant separation, end-to-end API regressions, security acceptance.
7. **Deployment:** approved PR, rollback preparation, main merge, image deploy and production smoke tests only after explicit final user approval.

## Non-negotiable safety
- Entire integration on `feat/astra-hub-integration-20261010`, not main.
- No backend or DB modification without separate review of the exact change.
- No production deploy, no actions on user mail data, and no UI demo content shipped as production.
- Preserve user-requested original focus style; don't substitute generic shadcn defaults.
