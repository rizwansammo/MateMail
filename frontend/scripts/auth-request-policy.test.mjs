import assert from "node:assert/strict";
import test from "node:test";
import { needsAccessToken } from "../lib/auth-request-policy.ts";

test("public sign-in/signup never try to refresh a session first", () => {
  for (const path of [
    "/api/auth/login/", "/api/auth/signup/", "/api/auth/refresh/",
    "/api/auth/forgot-password/", "/api/auth/reset-password/",
    "/api/auth/verify-email/", "/api/auth/2fa/verify/",
    "/api/platform/auth/login/", "/api/platform/auth/verify/",
    "/api/platform/auth/resend/", "/api/platform/auth/forgot-password/",
    "/api/platform/auth/reset-password/",
    "/api/teams/invites/preview/?token=example",
  ]) {
    assert.equal(needsAccessToken(path), false, path);
  }
});

test("protected requests still acquire a bearer token", () => {
  for (const path of [
    "/api/auth/me/", "/api/auth/logout/",
    "/api/auth/resend-verification/", "/api/auth/2fa/setup/",
    "/api/workspaces/abc/", "/api/platform/stats/",
    "/api/teams/invites/",
    "/api/auth/login/other/", // exact allowlist cannot bypass protection
  ]) {
    assert.equal(needsAccessToken(path), true, path);
  }
});
