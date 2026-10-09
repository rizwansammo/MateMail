/**
 * A credential-issuing or public auth request must never trigger an access-token
 * refresh first. Otherwise login/signup can spend the shared refresh throttle
 * before the user submits a credential, and refresh itself can be called twice.
 *
 * Keep the allowlist explicit: other endpoints still require a valid bearer
 * token (or a refresh through the HttpOnly cookie).
 */
const NO_ACCESS_TOKEN_PATHS = new Set([
  "/api/auth/login/",
  "/api/auth/signup/",
  "/api/auth/refresh/",
  "/api/auth/forgot-password/",
  "/api/auth/reset-password/",
  "/api/auth/verify-email/",
  "/api/auth/2fa/verify/",
  "/api/platform/auth/login/",
  "/api/platform/auth/verify/",
  "/api/platform/auth/resend/",
  "/api/platform/auth/forgot-password/",
  "/api/platform/auth/reset-password/",
  "/api/teams/invites/preview/",
]);

export function needsAccessToken(path: string): boolean {
  return !NO_ACCESS_TOKEN_PATHS.has(path.split("?", 1)[0]);
}
