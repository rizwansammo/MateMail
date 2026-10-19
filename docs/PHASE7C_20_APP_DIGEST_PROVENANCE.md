# Phase 7C-20 — Immutable application-image provenance manifest

**Date:** 2026-10-19  
**Branch:** `feature/phase7a-auth-transport-foundation`  
**Release:** Draft PR #136; no merge, publish, deployment or production changes.

## Source review finding

The existing application CI publishes Backend and Frontend GHCR images under
a full Git commit SHA tag (and a moving `main` tag), but the job summary
previously listed **tags only**. Registry tags can be reassigned. Operators
cannot prove immutable application-image identity or a rollback target solely
from `ghcr.io/...:<commit-sha>`.

Native Engine manual image publishing already prints actual digest outputs.
This phase extends that basic provenance hygiene to the two application images.

## Changes

- `.github/workflows/ci.yml`: give both existing `build-push-action@v6`
  steps explicit IDs and capture their actual `outputs.digest`. Only AFTER
  both images are successfully pushed, create the digest manifest. Validate
  it and upload as a GitHub Actions artifact retained for 30 days.
- `deploy/release_image_manifest.py`: stdlib-only deterministic builder and
  verifier, with a synthetic built-in negative test. The resulting JSON
  contains exactly:
  `schema_version: 1`, the full 40-hex source commit ID, and the two
  exact GHCR references `ghcr.io/rizwansammo/matemail-{backend,frontend}@sha256:<digest>`.
  Invalid/uppercase/truncated digests, branch tags, foreign registries,
  extra fields or schema drift are rejected.
- The existing `compose` PR CI job now executes only synthetic manifest
  unit checks (no Docker build, registry push, package token or deployment).
- `publish` remains gated against pull requests; no application image is
  published by this Draft PR. Existing manual production deploy workflow
  is **not** changed or invoked.

## Important boundary and operator follow-up

This produces verifiable digest references **after a future approved build
push**, not a currently published Release Candidate image. The manifest is
not a cryptographic signature, SBOM, attestations, a guarantee of registry
immutability, or proof that the server actually runs those digests.
If the operator subsequently chooses to deploy by digest, the referenced
Compose/image configuration must use the exact `@sha256` values and the
existing deployment workflow must be separately reviewed; it currently
accepts a commit-SHA image tag. The native images still require their own
independent immutable digests from the manual Native publish workflow.

For production approval, independently collect and preserve current/target
digests, existing production backup state, distinct persistent Hub/PostBox
Fernet keys, approved restore/rollback points, and a narrow customer-safe
canary. Never extract production secrets or bypass previously blocked
production inspection controls.

## Verification

- Static Python syntax, synthetic manifest success/negative cases, YAML
  workflow parsing and diff whitespace checks are suitable for TestLab.
- The updated exact-head PR workflow must pass before accepting this source
  change. A PR CI pass confirms generation logic only; real digest outputs
  are available only when a future authorized `publish` job runs.
- Keep PR #136 DRAFT, Require MFA hard lock false, `main` and production
  unchanged, and all TestLab evidence preserved until the end of Phase 8.
