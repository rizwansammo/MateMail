/**
 * CSP + hydration verification, in a real browser.
 *
 * Not part of CI: it needs a local Chrome and puppeteer-core, neither of which
 * belongs in the frontend's dependency tree. Run it by hand after any change
 * to middleware.ts, the nginx template, or the Next.js version — and against
 * the deployed site after a release.
 *
 *   npm --prefix /tmp/cspcheck init -y
 *   npm --prefix /tmp/cspcheck install puppeteer-core
 *   NODE_PATH=/tmp/cspcheck/node_modules BASE=https://app.matemail.online \
 *     node frontend/scripts/csp-browser-check.js
 *
 * Why it exists: P2.5 declared the frontend healthy from curl alone and shipped
 * a page that returned 200 and rendered nothing, because CSP had blocked
 * hydration. Every HTML document and JS chunk returns 200 in both the working
 * and the broken case — only a browser can tell them apart.
 */
const puppeteer = require("puppeteer-core");

const BASE = process.env.BASE || "http://127.0.0.1:3099";
const CHROME =
  process.env.CHROME_PATH ||
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: "new",
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage();

  const cspViolations = [];
  const consoleErrors = [];
  const failedRequests = [];

  page.on("console", (msg) => {
    const text = msg.text();
    if (/content security policy|refused to (execute|load|apply)/i.test(text)) {
      cspViolations.push(text);
    } else if (msg.type() === "error") {
      consoleErrors.push(text);
    }
  });
  page.on("pageerror", (err) => consoleErrors.push("pageerror: " + err.message));
  page.on("requestfailed", (req) => {
    // The API is not running in this harness; auth calls are expected to fail
    // at the network layer and the app handles that. Script/style/document
    // failures are not expected.
    const type = req.resourceType();
    if (["script", "stylesheet", "document"].includes(type)) {
      failedRequests.push(`${type} ${req.url()} ${req.failure()?.errorText}`);
    }
  });

  // ── 1. CSP header shape ────────────────────────────────────────────────
  const response = await page.goto(`${BASE}/login`, {
    waitUntil: "networkidle2",
    timeout: 30000,
  });
  const csp = response.headers()["content-security-policy"] || "";
  check("CSP header is present", csp.length > 0);
  check(
    "script-src has no 'unsafe-inline'",
    !/script-src[^;]*'unsafe-inline'/.test(csp),
    csp.match(/script-src[^;]*/)?.[0] || ""
  );
  check("script-src has no 'unsafe-eval'", !/script-src[^;]*'unsafe-eval'/.test(csp));
  check("script-src carries a nonce", /script-src[^;]*'nonce-/.test(csp));
  check("frame-ancestors is restricted", /frame-ancestors 'self'/.test(csp));
  check("base-uri is restricted", /base-uri 'self'/.test(csp));
  check("form-action is restricted", /form-action 'self'/.test(csp));
  check("object-src is 'none'", /object-src 'none'/.test(csp));
  check("only one CSP header is sent", !csp.includes(",default-src"), csp.slice(0, 40));

  // ── 2. Hydration ───────────────────────────────────────────────────────
  // The client runtime only exists if the nonced bootstrap script ran. (An
  // earlier version of this harness checked window.__next_f, which Next 16
  // drains once hydration completes — it reads as 0 on a healthy page.)
  const hydrated = await page.evaluate(
    () => typeof window.next === "object" && !!window.next && !!window.next.router
  );
  check("Next.js client runtime booted", hydrated);

  const reactAttached = await page.evaluate(() => {
    const el = document.querySelector("input");
    if (!el) return false;
    return Object.keys(el).some((k) => k.startsWith("__react"));
  });
  check("React attached to the DOM", reactAttached);

  // ── 3. The login form is actually interactive ──────────────────────────
  const emailInput = await page.$('input[type="email"], input[name="email"]');
  check("login form rendered", !!emailInput);

  if (emailInput) {
    await emailInput.type("someone@example.test");
    const value = await page.evaluate(
      (el) => el.value,
      emailInput
    );
    check("typing into the form updates React state", value === "someone@example.test", value);
  }

  // Client-side navigation proves the router is live, not just present.
  const before = page.url();
  await page.click('a[href="/signup"]').catch(() => {});
  await new Promise((r) => setTimeout(r, 1500));
  const after = page.url();
  check(
    "client-side navigation works",
    after !== before && /\/signup/.test(after),
    after
  );
  await page.goBack().catch(() => {});

  // ── 4. Unauthenticated root redirects to /login ────────────────────────
  const rootPage = await browser.newPage();
  await rootPage.goto(`${BASE}/`, { waitUntil: "networkidle2", timeout: 30000 });
  // Give the client-side auth redirect a moment to run.
  await new Promise((r) => setTimeout(r, 2500));
  const finalUrl = rootPage.url();
  check(
    "unauthenticated root reaches the login screen",
    /\/login/.test(finalUrl) || (await rootPage.$('input[type="email"]')) !== null,
    finalUrl
  );
  await rootPage.close();

  // ── 5. Nothing was blocked ─────────────────────────────────────────────
  check("no CSP violations in the console", cspViolations.length === 0, cspViolations.join(" | "));
  check(
    "no script/stylesheet/document request failures",
    failedRequests.length === 0,
    failedRequests.join(" | ")
  );

  if (consoleErrors.length) {
    console.log("\nOther console errors (informational — the API is not running here):");
    consoleErrors.slice(0, 10).forEach((e) => console.log("   " + e.slice(0, 160)));
  }

  await browser.close();

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  process.exit(failed.length ? 1 : 0);
})().catch((err) => {
  console.error("harness error:", err);
  process.exit(2);
});
