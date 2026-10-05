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
 * markup can know the nonce it used. `deploy/nginx/portal.matemail.online.conf`
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

function buildCsp(nonce: string, allowRemoteImages = false): string {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    // `https:` on PostBox only. A mail client has to be able to show a
    // sender's logo once the reader has asked for it, and a signature's own
    // logo always. The other consoles render nothing third-party.
    `img-src 'self' data: blob:${allowRemoteImages ? " https:" : ""}`,
    "font-src 'self' data:",
    // The API is same-origin (each hostname serves both). In development
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
 * Host-based routing for MateMail's three surfaces.
 *
 * One Next.js application serves all of them, and which one a request gets is
 * decided here by the Host header rather than by running three deployments:
 *
 *   portal.matemail.online    MateMail Workspace   — customers
 *   platform.matemail.online  Platform Console     — NetaMate staff
 *   postbox.matemail.online   MateMail PostBox     — mailbox users
 *
 * `app.matemail.online` is the Workspace's former hostname. It is a legacy
 * redirect and nothing else: every path on it is sent, once, to the same
 * path on the Workspace host. It is not a surface the product answers on,
 * and no link, document or build argument should point at it.
 *
 * Each of the latter two mounts its subtree at the root of its own hostname,
 * so nobody types `/platform` or `/postbox`.
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
 *     - Workspace host: `/platform/...` and `/admin/...` REDIRECT to the
 *       platform host. Those land on the platform host, where the first rule
 *       rewrites rather than redirects. The chain terminates after one hop.
 *     - legacy host: everything REDIRECTS to the Workspace host, which is
 *       not itself the legacy host, so that chain terminates too — at worst
 *       legacy → Workspace → PostBox, which is two hops and no loop.
 *   Anything already under `/_next`, `/api` or the icons is left alone on both.
 */

/** The Platform Console's hostname, if one is configured for this deployment. */
const PLATFORM_HOST = process.env.NEXT_PUBLIC_PLATFORM_HOST ?? "";

/** MateMail PostBox — where a mailbox user reads their own mail. */
const POSTBOX_HOST = process.env.NEXT_PUBLIC_POSTBOX_HOST ?? "";

/**
 * MateMail Workspace — the customer surface. Unlike the two above this is
 * also the fallback, so an unconfigured deployment still serves it; the
 * constant exists so the legacy host has somewhere to redirect *to*.
 */
const WORKSPACE_HOST = process.env.NEXT_PUBLIC_WORKSPACE_HOST ?? "";

/** The canonical public product website. */
const PUBLIC_HOST = process.env.NEXT_PUBLIC_PUBLIC_HOST ?? "matemail.online";

function isPublicHost(host: string): boolean {
  return host === PUBLIC_HOST.toLowerCase();
}

/**
 * The Workspace's former hostname. Comma-separated, so a second retired
 * name can be added without another constant.
 */
const LEGACY_WORKSPACE_HOSTS = (process.env.NEXT_PUBLIC_LEGACY_WORKSPACE_HOSTS ?? "")
  .split(",")
  .map((value) => value.trim().toLowerCase())
  .filter(Boolean);

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

function isPostBoxHost(host: string): boolean {
  if (POSTBOX_HOST) return host === POSTBOX_HOST.toLowerCase();
  return host.startsWith("postbox.");
}


type CustomSurface = "hub" | "postbox";

/**
 * Surface marker supplied by the exact-host nginx vhost generated by MateMail.
 *
 * A browser can invent request headers, so the marker is deliberately ignored
 * on every configured/canonical hostname. For an arbitrary customer hostname,
 * the only public TLS vhost that can successfully serve it is the generated
 * exact-host vhost, and that vhost overwrites both marker headers before
 * proxying to Next.js.
 */
function customSurfaceOf(request: NextRequest): CustomSurface | null {
  const host = hostOf(request);
  const knownHost =
    (!!WORKSPACE_HOST && host === WORKSPACE_HOST.toLowerCase()) ||
    (!!PLATFORM_HOST && host === PLATFORM_HOST.toLowerCase()) ||
    (!!POSTBOX_HOST && host === POSTBOX_HOST.toLowerCase()) ||
    host === PUBLIC_HOST.toLowerCase() ||
    LEGACY_WORKSPACE_HOSTS.includes(host);

  if (knownHost) return null;
  if (request.headers.get("x-matemail-custom-host") !== "1") return null;

  const surface = request.headers.get("x-matemail-surface")?.toLowerCase();
  return surface === "hub" || surface === "postbox" ? surface : null;
}

function isPostBoxRequest(request: NextRequest): boolean {
  return (
    isPostBoxHost(hostOf(request)) ||
    customSurfaceOf(request) === "postbox"
  );
}

/**
 * A retired hostname, which is only ever redirected.
 *
 * This is an allow-list of exact names, not a prefix test like the two
 * above: a prefix test would be guessing which names are retired, and
 * guessing wrong means redirecting a hostname that was meant to work.
 */
function isLegacyWorkspaceHost(host: string): boolean {
  return LEGACY_WORKSPACE_HOSTS.includes(host);
}

/** Paths that are never console routes and must pass through untouched. */
function isInfrastructurePath(pathname: string): boolean {
  return (
    pathname.startsWith("/_next") ||
    pathname.startsWith("/api") ||
    pathname.startsWith("/assets/") ||
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
  const customSurface = customSurfaceOf(request);
  const { pathname, search } = request.nextUrl;

  if (isInfrastructurePath(pathname)) return null;

  // First, because a retired hostname serves nothing. Every path moves to
  // the same path on the Workspace host, so an old bookmark lands where it
  // used to rather than on a home page.
  //
  // Without a configured Workspace host there is nowhere to send it, and
  // redirecting to a guess would be worse than continuing to serve — so it
  // falls through and behaves as it always did.
  if (WORKSPACE_HOST && isLegacyWorkspaceHost(host)) {
    const target = new URL(request.url);
    target.host = WORKSPACE_HOST;
    target.protocol = "https:";
    target.port = "";
    target.search = search;
    return { kind: "redirect", url: target, status: 308 };
  }

  // matemail.online is now the public product homepage. Only the document root
  // belongs to that surface; customer account routes remain canonical on
  // portal.matemail.online. Static/_next/API paths already passed through
  // above, so the homepage can hydrate without duplicating the Workspace UI on
  // the public hostname.
  if (isPublicHost(host)) {
    if (pathname === "/") {
      const url = request.nextUrl.clone();
      url.pathname = "/public-home";
      url.search = search;
      return { kind: "rewrite", url };
    }

    // The internal target of the root rewrite is allowed through unchanged.
    // It is not linked publicly; the browser remains at matemail.online/.
    if (pathname === "/public-home") return null;

    if (WORKSPACE_HOST) {
      const target = new URL(request.url);
      target.host = WORKSPACE_HOST;
      target.protocol = "https:";
      target.port = "";
      target.search = search;
      return { kind: "redirect", url: target, status: 308 };
    }
  }

  if (isPostBoxHost(host) || customSurface === "postbox") {
    // PostBox mounts at the root of its own hostname, so a mailbox user never
    // types `/postbox`. Same mechanism as the Platform Console: a REWRITE, so
    // the URL stays clean and nothing re-enters this function.
    if (pathname === "/postbox" || pathname.startsWith("/postbox/")) return null;

    const url = request.nextUrl.clone();
    url.pathname = pathname === "/" ? "/postbox" : `/postbox${pathname}`;
    url.search = search;
    return { kind: "rewrite", url };
  }

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

  // The Workspace host, and the fallback for anything unrecognised. The
  // other two consoles have canonical homes, so send people there instead of
  // serving a second copy of either on the customer hostname.
  if (POSTBOX_HOST && (pathname === "/postbox" || pathname.startsWith("/postbox/"))) {
    const target = new URL(request.url);
    target.host = POSTBOX_HOST;
    target.protocol = "https:";
    target.port = "";
    target.pathname = pathname.replace(/^\/postbox/, "") || "/";
    return { kind: "redirect", url: target, status: 308 };
  }

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
  // Computed before routing, because the policy depends on which surface
  // this hostname serves.
  const csp = buildCsp(nonce, isPostBoxRequest(request));

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
