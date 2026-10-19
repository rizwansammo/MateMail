# Phase 7C-18 — Native client-policy acknowledgement before PostBox MFA enrollment

**Date:** 2026-10-19
**Feature branch:** `feature/phase7a-auth-transport-foundation`
**Status:** Code and regression cases added. Await normal GitHub CI acceptance; no production changes.

## Finding

The PostBox second-factor enrollment endpoint called the Native adapter to
require a dedicated SMTP/IMAP password *before* recording MFA enrollment.
It treated any nonexceptional adapter response as proof of enforcement.

A Native endpoint, proxy or future version could return HTTP success with a
missing/stale/inconsistent policy payload. A PostBox mailbox would then appear
MFA-enrolled without verified proof that the actual mail-client policy was
restricted. The current Native API normally returns the expected two booleans,
but this was an avoidable fail-closed response-contract gap.

## Corrective contract

- Validate that the Native `list_client_credentials` response includes an
  **actual boolean** `enabled`, never a truthy string such as `"false"`.
- After the Native `set_client_policy` call, explicitly require a dictionary
  containing `dedicated_required is True` and an `enabled` boolean identical
  to the requested active/disabled state.
- If the response is incomplete, contradictory or malformed, return generic
  HTTP **503**; leave the PostBox MFA setup pending and issue no MFA-proven
  browser session, backup codes, or one-time client password in the response.
- A deliberately disabled external mail-client policy (`enabled=False`)
  remains valid, provided the explicit confirmation still includes
  `dedicated_required=True`. It generates no new client password.
- Existing DB transaction order, Native commit semantics, session revocation
  and Require organization release lock are unchanged.

The check ensures **acknowledgement**, not global distributed atomicity or
guaranteed physical Dovecot connectivity. Failed or inconsistent acknowledgements
may leave an already-issued Native device credential; operators should inspect
the credential list and revoke/clean up only through approved APIs as needed.
Do not log or persist raw credentials.

## Added Django tests

`backend/tests/test_postbox_phase7a_security.py` now includes:

1. A matrix of incomplete and contradictory Native policy acknowledgements,
   confirming HTTP 503, no MFA enrollment and no backup/session leakage.
2. Malformed Native list-policy `enabled` values, verifying the security
   change is rejected **before** minting a client password.
3. A valid disabled-mail-client policy acknowledgement, confirming that
   voluntary web MFA can enroll without creating an SMTP/IMAP password while
   maintaining `dedicated_required=True` for future client re-enablement.

Existing successful-enrollment tests supply the correct Native acknowledgement.
The new tests are synthetic and do not access production identities.

## Acceptance and restrictions

- Source syntax and whitespace review were run on the feature branch.
- Full Django and Native CI are **PENDING** until checked on the updated PR.
- `backend/apps/postbox/two_factor_core.enrollment_ready()` still has a
  historical fail-closed return, while active endpoints use
  `two_factor_views._ready()` with both feature flags and Native adapter.
  This distinction remains an **independent release architecture review
  item** and does not authorize overriding any activation gate.
- `can_activate_require()` stays `False` by design; all production MFA
  flags remain OFF; Draft PR is not merge/deployment approval.
- Preserve all TestLab files and results until the **end of Phase 8**.
