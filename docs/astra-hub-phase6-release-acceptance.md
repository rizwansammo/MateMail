# Astra Hub Phase 6 — release acceptance evidence

**Branch:** `feat/astra-hub-integration-20261010` · Draft PR #98.  
**Policy:** no main merge, production deployment, real mailboxes or production database mutations.

## Verified in isolated CI
- Phase 1–5 synthetic browser assertions; Phase 6 visual token and browser
  interaction contract captures desktop/mobile light/dark screenshots.
- Native UI isolation: Astra CSS belongs to the Hub scope and must not change
  PostBox or Platform Console.
- Approved visual baseline from `rizwansammo/MateMail-Hub-NextJS` branch
  `design/astra-95-refinement-20261010`:
  light #f7f8fa/#3d54b8, dark #141b26/#9aacff,
  4px radius, 244px sidebar, 60px topbar.
- Django regression tests exercise optional mailbox provisioning with a
  verified inviter, legacy Hub-only invite behavior, tenant policy, passwords,
  invitation lifecycle, Mail Engine error handling and rollback.

## Phase 5 verification policy
The **provisioning administrator**, not the future mailbox user, must have
a verified Hub email address, matching the existing direct mailbox creation
API. Guard at invitation issuance and acceptance before Mail Engine access.
New invitees can have `email_verified=False`: their newly provisioned
mailbox is the destination for the subsequent verification message.
Forcing verification before mailbox creation would block that onboarding
flow. Invitation URLs are security-sensitive bearer links; share privately.

## Cannot be confirmed using synthetic QA or the live production server
- Real SMTP/IMAP, Native Engine provisioning and outbound/inbound routing.
- Staging with a real, non-production tenant on isolated infrastructure.
- Exact pixel-by-pixel automated screenshot comparison against approved
  reference image assets. The browser script checks key design tokens,
  geometry and interactions; the PR artifact supplies screenshots for manual
  visual approval.
- Restore drill, real release migration and environment-specific rollback.

## Phase 7 strict hold points
1. User reviews screenshot artifact and approves visual result.
2. Authorized isolated staging, with test domain/account, completes real
   API/native engine and mail verification without using production data.
3. Backup and database migration review, including rollback plan.
4. Explicit approval before merge, image publication or live deployment.

Staging absence must **not** be substituted with a test on a production
MateMail tenant. Remain Draft if any required gate is unverified.
