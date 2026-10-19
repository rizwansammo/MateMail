# Phase 7C-3 — Real Browser / Real Backend Authentication Acceptance

**Date:** 2026-10-19
**Status:** Real browser authentication and cookie flow **PASSED**; Stage 7C as a whole remains open.
**Branch:** `feature/phase7a-auth-transport-foundation`
**TestLab root:** `/opt/TestLab/MateMail-Stage7A/`

## Isolation and test topology

- Headless Chromium (Playwright), real production-built Next.js 16 frontend at a disposable loopback-bound server.
- Local-only HTTPS reverse proxy on `127.0.0.1:31480` with a disposable two-day certificate; `/api/*` forwarded to an isolated Django test server and all other paths to Next.js.
- Real Django ORM, authentication and session code, separate PostgreSQL and Redis inside `mm7c_isolated` Docker network.
- Test settings use **DEBUG=False** to activate production-equivalent `__Host-postbox_session` Secure cookie behavior, without changing any production settings.
- The PostBox primary-password check uses a **real separate Native API** backed by a disposable synthetic Native database on `mm7cnative_isolated`. Django TestLab alone has access to both isolated networks.
- Synthetic Hub owner `owner@isolated7c.example.test`, synthetic PostBox mailbox `alice@isolated7c.example.test`, and lab-only TOTP seeds. No external traffic, production access, real customer data or live production credentials.
- Containers bounded individually by CPU, memory and PID limits. No test port is published on public interfaces.

## Verified real Playwright interactions

**Hub — 3/3 PASS:**

1. Browser submits real Next.js Hub login page to Django. Password produces a 200 with an opaque MFA challenge, not a fully authenticated session.
2. An incorrect TOTP is denied with Django HTTP 401.
3. Correct TOTP returns Django HTTP 200, navigates to Hub, and creates an HttpOnly SameSite=Strict, path-scoped refresh cookie.

**PostBox — 3/3 PASS:**

1. Browser submits the real PostBox login page. Native API checks the original mailbox password (not SMTP/IMAP client password) and Django returns an MFA challenge.
2. Invalid TOTP is denied with Django HTTP 401.
3. Valid TOTP receives HTTP 200, navigates to PostBox and stores a **Secure, HttpOnly `__Host-postbox_session`** host-only cookie.

**PostBox session reuse — 3/3 PASS (overlapping login assertions):**

1. Native-backed password login creates an MFA challenge.
2. TOTP creates a Secure HttpOnly browser cookie.
3. A separate subsequent real Django `GET /api/postbox/auth/me/` using the same browser cookie returns HTTP 200 and the correct mailbox identity; without it the endpoint returned 403.

No Playwright request interception, mock API JSON, or fake authorization is involved in these scenarios. Server HTTP statuses were observed from actual backend responses.

## Diagnostic findings and safe resolution

- The original TestLab fixture wrote its one-time TOTP secret to a read-only bind mount and failed with `PermissionError`; a deterministic synthetic TOTP seed removed that unnecessary file write.
- A test-only HTTP origin and `DEBUG=True` produced a host-prefixed PostBox cookie that Chromium rejected because the cookie lacked the Secure flag. A local HTTPS proxy and **DEBUG=False** test settings corrected the test topology. Production's cookie settings were not weakened.
- Repeated intentionally wrong OTP tests correctly exhausted the **isolated** Redis rate limit (429). Only the synthetic TestLab Redis was reset for deterministic later verification; production Redis was never accessed.
- The Browser initially raced its response event listener against Next.js navigation. The acceptance script now waits explicitly for the actual Django 200 response.

## Evidence retained until Phase 8

- `results/stage7c-real-hub-browser.log`
- `results/stage7c-real-postbox-browser-https.log`
- `results/stage7c-real-postbox-session-reuse.log`
- `results/stage7c-browser-synthetic-hub-ready.log`
- `browser-e2e/test-stage7c-real-hub.mjs`
- `browser-e2e/test-stage7c-real-postbox.mjs`
- `browser-e2e/test-stage7c-real-postbox-session.mjs`
- Test-only HTTPS reverse proxy, certificate, Docker compose configuration and synthetic fixtures under TestLab.

## Remaining Stage 7C gates — do not mislabel as complete

- The combined Native API credential **policy -> actual Dovecot/Postfix login -> live IMAP kick on revoke** still needs final accepted evidence from the same isolated stack. Previous native credential creation and 993/587 TLS authentication tests passed separately, but do not prove active-connection revocation.
- This test validates browser login and session cookies; it does not validate all PostBox folder/IMAP operations. Isolated folder provisioning may report unavailable without a running synthetic Dovecot.
- Durable unattended retry/reconciliation of failed Dovecot kicks is not complete.
- Organization `Require` remains intentionally locked pending full enforcement and independently validated recovery/refresh/API boundaries.
- Production-specific canary, customer client compatibility, independent encryption-key continuity, final rollback and release approval are outstanding.

Do not merge to `main`, deploy production, unlock Require or clean up TestLab files on the basis of this browser acceptance alone.
