import { NextRequest, NextResponse } from "next/server";

/**
 * Content-Security-Policy, owned by the application.
 *
 * P2.5 had to ship `script-src 'self' 'unsafe-inline'` from nginx, because the
 * App Router emits inline `<script>` tags carrying the hydration payload
 * (`self.__next_f`) and without them React never hydrates — every page renders
 * as a dead static shell. `'unsafe-inline'` on script-src removes CSP's main
 * protection, so that was tracked as security debt, not a design.
 *
 * The replacement is a per-request nonce. Each HTML response gets a fresh
 * random value; Next.js reads it from the `Content-Security-Policy` header we
 * set on the *request* and stamps it onto every script tag it emits. An
 * injected script has no way to know the nonce, so it does not run.
 *
 * CSP now lives here rather than in nginx: only the process that renders the
 * markup can know the nonce it used. `deploy/nginx/app.matemail.online.conf`
 * no longer sets this header for frontend responses — two CSP headers are
 * enforced as an intersection, which would silently reintroduce a policy
 * nobody is reading.
 *
 * `'strict-dynamic'`: the nonced bootstrap script loads the rest of the chunks.
 * With `strict-dynamic`, scripts it loads inherit trust, which is what lets the
 * chunk graph work without host allow-listing. CSP3 browsers ignore `'self'`
 * in the presence of `strict-dynamic`; it is kept as the fallback for anything
 * that only implements CSP2.
 *
 * `'unsafe-eval'` is added in development only — the dev server's React
 * refresh needs it. It must never reach production, so it is gated on
 * NODE_ENV, which Next.js sets to "production" for `next build`.
 *
 * `style-src` keeps `'unsafe-inline'`. Next injects critical CSS inline, and
 * there is no style nonce equivalent that survives the App Router's streaming.
 * The brief permits this where genuinely required; it is a far smaller
 * exposure than script-src, since injected CSS cannot execute.
 */

const isDev = process.env.NODE_ENV !== "production";

function buildCsp(nonce: string): string {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    // The API is same-origin (app.matemail.online serves both). In development
    // it is a different port, so the dev origin is allowed there only.
    `connect-src 'self'${isDev ? " http://localhost:8000 ws://localhost:3000" : ""}`,
    "frame-ancestors 'self'",
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
    ...(isDev ? [] : ["upgrade-insecure-requests"]),
  ].join("; ");
}

export function middleware(request: NextRequest) {
  // crypto.randomUUID is available in the Edge runtime and is cryptographically
  // random. Base64 keeps the header value compact and CSP-safe.
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const csp = buildCsp(nonce);

  // Next.js looks for the nonce on the *request* CSP header to decide what to
  // stamp onto its script tags. Setting it only on the response would produce a
  // policy that blocks the very scripts the page needs.
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    /*
     * HTML documents only. Static chunks, images and the app icons are not
     * script-executing documents, and giving each one a nonce would defeat
     * caching for no gain.
     *
     * The icons are `app/icon.png` and `app/apple-icon.png`. This list named
     * `favicon.ico` until that file — the stock Next.js one — was replaced by
     * the MateMail mark.
     *
     * The route names are taken from the build output (`○ /icon.png`,
     * `○ /apple-icon.png`) rather than from the documentation, which shows an
     * extensionless `/icon` and would have silently missed both. They are
     * anchored with `$` so a future document route merely beginning with
     * "icon" still receives its CSP.
     */
    {
      source: "/((?!_next/static|_next/image|icon\\.png$|apple-icon\\.png$).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
