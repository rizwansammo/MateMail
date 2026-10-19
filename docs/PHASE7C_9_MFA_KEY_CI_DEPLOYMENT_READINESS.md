# Phase 7C-9 — MFA key continuity and deployment/CI readiness

**Date:** 2026-10-19  
**Branch:** `feature/phase7a-auth-transport-foundation`  
**Scope:** OFF-by-default Compose configuration and synthetic CI guardrails.

## Findings and remediation

1. MateMail's production application Compose template did **not** expose the
   required Hub/PostBox encryption keys or Phase 7 switches in the shared
   backend environment. Setting these only in `.env` would not deliver them
   to Django, migrations or Celery. Added:
   `HUB_2FA_FERNET_KEY`, `POSTBOX_2FA_FERNET_KEY`,
   `ORGANIZATION_2FA_POLICY_ENABLED`,
   `ORGANIZATION_2FA_ENFORCEMENT_READY`,
   `POSTBOX_2FA_ENABLED`, `SMTP_IMAP_SECURITY_ENABLED`.
   Keys default **empty**, flags default **False**. No runtime-generated keys.
2. Extended `.github/workflows/native-api-security.yml` with the **entire**
   Native API `unittest discover` suite. Previously only named Platform
   Native modules ran in the CI candidate job, omitting the new 7C tests.
   Candidate build remains local; no image publishing or deployment.
3. Added two **distinct synthetic per-run** Hub/PostBox Fernet keys to the
   backend CI job, and two independent Python processes that create and
   then decrypt synthetic TOTP ciphertext. Missing or swapped keys must fail
   closed. Only ciphertext is saved to a disposable file.
4. Added a Compose contract validator which resolves the production
   application template twice **without starting containers**: with no MFA
   variables (OFF, keys empty), then with independent ephemeral keys and
   flags ON. This validates forwarding into backend, migrations and Celery
   services, without relying on production secrets.

## Verified in isolated TestLab

- `deploy/phase7c_mfa_compose_check.py`: **PASS** with CI-only Compose
  placeholder values; default-OFF and explicit key/flag pass-through.
  Evidence: `results/stage7c-mfa-compose-check.log`.
- `backend/scripts/phase7c_key_continuity.py`: **PASS** in two separate
  disposable **network-disabled, read-only** backend Python containers,
  each with the same synthetic independent keys. Decryption of both saved
  ciphertexts after new process startup succeeded, while swapped/empty keys
  failed closed. Synthetic fixture retained in TestLab results.
- Complete Native Engine `unittest discover`: **66/66 PASS** in disposable
  network-disabled, read-only Native API container with no production
  secrets. Evidence: `results/stage7c-ci-full-native-repeat.log`.
- GitHub workflow YAML parse and required step presence: **PASS**.
- Python syntax and `git diff --check`: **PASS**.

## Explicitly *not* verified

- Actual GitHub Actions run for this exact commit (new steps not yet
  executed remotely). CI green **must not** be asserted.
- Persistent key mounts / operator-owned keys across a real Production
  restart or migration. This synthetic fixture is NOT a production key or
  a backup of one.
- Native-issued password revoke → **real established IMAP session** kick and
  worker reconnection/outage acceptance. Require MFA activation remains
  deliberately gated.
- Final image digest, independent supply-chain verification, operator
  backup/restore rehearsal, rollback and production canary. Do not merge
  or deploy until remaining release gates and approval.

Keep all TestLab files and evidence until Phase 8 cleanup.
