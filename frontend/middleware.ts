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

/**
 * Host-based routing for the two consoles.
 *
 * One Next.js application serves both. `platform.matemail.online` is the
 * Platform Console and `app.matemail.online` is the Organization Console, and
 * which one a request gets is decided here by the Host header rather than by
 * running a second deployment.
 *
 * On the platform host the whole `/platform` subtree is mounted at the root, so
 * an operator sees `/organizations`, not `/platform/organizations`. That is a
 * REWRITE, not a redirect: the URL stays clean and the route files stay in one
 * place. The console's own links are written as `/platform/...`, which the
 * rewrite below also accepts, so a link never 404s whichever host it is on.
 *
 * WHY THERE IS NO REDIRECT LOOP
 *   The two hosts push in opposite directions, so the rules have to be
 *   asymmetric:
 *     - platform host: `/x` is rewritten to `/platform/x` internally. A rewrite
 *       does not change the browser's URL, so nothing re-enters the middleware.
 *     - app host: `/platform/...` and `/admin/...` REDIRECT to the platform
 *       host. Those land on the platform host, where the first rule rewrites
 *       rather than redirects. The chain therefore terminates after one hop.
 *   Anything already under `/_next`, `/api` or the icons is left alone on both.
 */

/** The Platform Console's hostname, if one is configured for this deployment. */
const PLATFORM_HOST = process.env.NEXT_PUBLIC_PLATFORM_HOST ?? "";

function hostOf(request: NextRequest): string {
  // `host` is what nginx forwards; it carries the port in development.
  return (request.headers.get("host") ?? "").toLowerCase().split(":")[0];
}

function isPlatformHost(host: string): boolean {
  if (PLATFORM_HOST) return host === PLATFORM_HOST.toLowerCase();
  // Fallback so a deployment that has not set the variable still behaves, and
  // so `platform.localhost` works for local testing.
  return host.startsWith("platform.");
}

/** Paths that are never console routes and must pass through untouched. */
function isInfrastructurePath(pathname: string): boolean {
  return (
    pathname.startsWith("/_next") ||
    pathname.startsWith("/api") ||
    pathname === "/icon.png" ||
    pathname === "/apple-icon.png" ||
    pathname === "/robots.txt" ||
    pathname === "/sitemap.xml"
  );
}

/** Either an internal rewrite target or an external redirect target. */
type Routing =
  | { kind: "rewrite"; url: URL }
  | { kind: "redirect"; url: URL; status: 307 | 308 }
  | null;

function routeForHost(request: NextRequest): Routing {
  const host = hostOf(request);
  const { pathname, search } = request.nextUrl;

  if (isInfrastructurePath(pathname)) return null;

  if (isPlatformHost(host)) {
    // Already inside the subtree — the console's own links look like this.
    if (pathname === "/platform" || pathname.startsWith("/platform/")) return null;

    // Customer surfaces do not exist on this hostname. Sending them to the
    // console root rather than 404ing keeps a stale bookmark useful, and there
    // is deliberately no signup or trial route to reach here at all.
    const url = request.nextUrl.clone();
    url.pathname = pathname === "/" ? "/platform" : `/platform${pathname}`;
    url.search = search;
    return { kind: "rewrite", url };
  }

  // Organization host. The Platform Console has a canonical home, so send
  // people there instead of serving a second copy on the customer hostname.
  if (PLATFORM_HOST && (pathname === "/platform" || pathname.startsWith("/platform/"))) {
    const target = new URL(request.url);
    target.host = PLATFORM_HOST;
    target.protocol = "https:";
    target.port = "";
    target.pathname = pathname.replace(/^\/platform/, "") || "/";
    return { kind: "redirect", url: target, status: 308 };
  }

  // The pre-existing admin screens. Their URLs are kept working, pointed at
  // the equivalent page on the canonical hostname.
  if (pathname === "/admin" || pathname.startsWith("/admin/")) {
    const rest = pathname.replace(/^\/admin/, "");
    const mapped =
      rest === "" || rest === "/"
        ? "/"
        : rest.replace(/^\/tenants/, "/organizations");

    if (PLATFORM_HOST) {
      const target = new URL(request.url);
      target.host = PLATFORM_HOST;
      target.protocol = "https:";
      target.port = "";
      target.pathname = mapped;
      return { kind: "redirect", url: target, status: 308 };
    }

    // No platform hostname configured (development): keep it on this host.
    const url = request.nextUrl.clone();
    url.pathname = `/platform${mapped === "/" ? "" : mapped}`;
    return { kind: "redirect", url, status: 307 };
  }

  return null;
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

  const routed = routeForHost(request);

  // A redirect carries no document, so it needs no nonce — but it still gets
  // the policy, because a browser applies CSP to whatever response it receives.
  if (routed?.kind === "redirect") {
    const redirect = NextResponse.redirect(routed.url, routed.status);
    redirect.headers.set("Content-Security-Policy", csp);
    return redirect;
  }

  // Both branches pass `request.headers` through, because the rewritten route
  // renders the document and Next.js reads the nonce off the request.
  const response =
    routed?.kind === "rewrite"
      ? NextResponse.rewrite(routed.url, { request: { headers: requestHeaders } })
      : NextResponse.next({ request: { headers: requestHeaders } });

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
