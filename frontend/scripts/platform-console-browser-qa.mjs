/**
 * Release 2 Platform Console browser QA.
 * Runs only against an isolated local production Next preview.
 * Every API response is synthetic. Never sends real credentials or changes mail.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";

const base = process.env.PLATFORM_QA_URL || "http://127.0.0.1:3102";
const output = process.env.PLATFORM_QA_OUTPUT || "platform-qa-artifacts";
fs.mkdirSync(output, { recursive: true });
const report = { scenario: "Release 2 Platform Console", synthetic: true, checks: [], screenshots: [], errors: [] };
const token = [
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
  Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600, platform: true })).toString("base64url"),
  "synthetic-not-a-real-signature"
].join(".");
const user = { id: "qa-platform-operator", full_name: "Morgan Test", email: "qa-admin@example.test", is_platform_admin: true };
const defaults = {
  domain: "system.matemail.pro", sender_name: "MateMail",
  noreply_address: "noreply@system.matemail.pro", security_address: "security@system.matemail.pro",
  operations_address: "operations@system.matemail.pro", dmarc_address: "dmarc@system.matemail.pro",
  tlsrpt_address: "tlsrpt@system.matemail.pro"
};
const engine = { status: "observed", legacy_domain: "active", target_domain: "absent", legacy_sender: "active",
  target_roles: { noreply_address: "absent", security_address: "absent", operations_address: "absent", dmarc_address: "absent", tlsrpt_address: "absent" },
  reason: "Synthetic inventory" };
const readiness = { checked_at: "2026-10-13T08:00:00Z", domain: defaults.domain, platform_ownership: { target: "verified", legacy: "verified" },
  engine, runtime_alignment: { roles: { sender: "legacy" }, legacy_configuration_intact: true, migration_started: false },
  outstanding_gates: { dns: "not_verified", dkim_signing: "not_verified", smtp_delivery: "not_tested", imap_access: "not_tested",
    new_credentials: "not_provisioned", rollback_release: "not_approved" }, cutover_ready: false };
const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
let failed = false;
function check(name, condition, facts = {}) {
  report.checks.push({ name, passed: Boolean(condition), facts });
  assert.ok(condition, name + ": " + JSON.stringify(facts));
}
async function shot(page, name) {
  const filename = name + ".png";
  await page.screenshot({ path: path.join(output, filename), animations: "disabled", fullPage: false });
  report.screenshots.push(filename);
}
async function setup({ width = 1440, height = 900, theme = "light" } = {}) {
  const context = await browser.newContext({ viewport: { width, height }, colorScheme: theme, reducedMotion: "reduce" });
  const state = { revision: 0, draft: null, operations: [], errors: [] };
  await context.route("**/api/**", async route => {
    const request = route.request(), url = new URL(request.url()), endpoint = url.pathname;
    const method = request.method();
    let data = {}, code = 200;
    if (endpoint === "/api/auth/refresh/") data = { access: token };
    else if (endpoint === "/api/auth/me/") data = user;
    else if (endpoint === "/api/auth/logout/" && method === "POST") { data = { detail: "ok" }; state.operations.push("logout"); }
    else if (endpoint === "/api/platform/system-mail/configuration/" && method === "GET") {
      data = { revision: state.revision, draft: state.draft, defaults,
        operational: { sender: "noreply@mail.matemail.pro", dmarc_receiver: "dmarc@mail.matemail.pro",
          tlsrpt_receiver: "tlsrpt@mail.matemail.pro", source: "environment", draft_applied: false },
        activation_supported: false, mailboxes_provisioned: false };
    } else if (endpoint === "/api/platform/system-mail/configuration/" && method === "POST") {
      const body = JSON.parse(request.postData() || "{}");
      if (body.expected_revision !== state.revision) { code = 409; data = { detail: "Revision conflict" }; }
      else {
        state.revision++;
        state.draft = { ...defaults, ...body, revision: state.revision, created_at: "2026-10-13T08:00:00Z" };
        delete state.draft.expected_revision;
        state.operations.push("save-draft");
        data = { revision: state.revision, draft: state.draft, defaults,
          operational: { sender: "noreply@mail.matemail.pro", dmarc_receiver: "dmarc@mail.matemail.pro",
            tlsrpt_receiver: "tlsrpt@mail.matemail.pro", source: "environment", draft_applied: false },
          activation_supported: false, mailboxes_provisioned: false };
      }
    } else if (endpoint === "/api/platform/system-mail/readiness/") data = readiness;
    else if (endpoint === "/api/platform/system-mail/history/") data = {
      results: state.draft ? [{ revision: state.revision, created_at: state.draft.created_at, state: "draft" }] : []
    };
    else if (endpoint === "/api/platform/search/") data = { query: "", results: [] };
    else if (endpoint.startsWith("/api/platform/") && method !== "GET") {
      state.operations.push("UNEXPECTED_WRITE:" + endpoint);
      code = 403;
      data = { detail: "Synthetic preview denies other mutations" };
    }
    await route.fulfill({ status: code, contentType: "application/json", body: JSON.stringify(data) });
  });
  const page = await context.newPage();
  page.on("pageerror", e => state.errors.push(e.message));
  return { context, page, state };
}
async function run(label, options, perform) {
  let session;
  try {
    session = await setup(options);
    await perform(session);
    check(label + " has no client errors", session.state.errors.length === 0, { errors: session.state.errors });
    check(label + " performs no unexpected writes", !session.state.operations.some(s => s.startsWith("UNEXPECTED_WRITE")), session.state);
  } catch (err) {
    failed = true;
    report.errors.push({ label, error: err.stack || String(err) });
  } finally {
    await session?.context.close();
  }
}
async function visit(page) {
  await page.goto(base + "/platform/system-mail", { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.getByRole("heading", { name: "System Mail" }).waitFor({ state: "visible", timeout: 30000 });
  await page.getByText("Readiness inventory").waitFor({ state: "visible", timeout: 15000 });
}
try {
  await run("Desktop light", { theme: "light" }, async ({ page, state }) => {
    await visit(page);
    await page.evaluate(() => document.fonts.ready);
    await shot(page, "01-platform-desktop-light");
    const geometry = await page.evaluate(() => {
      const aside = document.querySelector("aside"), word = [...aside.querySelectorAll("span")].find(el => el.textContent.trim() === "MateMail");
      const label = document.querySelector("header");
      const asideRect = aside?.getBoundingClientRect();
      return {
        asideWidth: asideRect?.width, wordmarkFamily: word && getComputedStyle(word).fontFamily,
        headerLabel: label?.textContent.includes("Platform Console"),
        sidebarLabel: aside?.textContent.includes("Platform Console"),
        sidebarFooter: aside?.textContent.includes("Sign out"),
        viewportOverflow: document.documentElement.scrollWidth > window.innerWidth
      };
    });
    check("Sidebar remains 240px and wordmark uses HemiHead", geometry.asideWidth === 240 && /HemiHead/.test(geometry.wordmarkFamily), geometry);
    check("Platform Console label moved to header only", geometry.headerLabel && !geometry.sidebarLabel, geometry);
    check("Account footer removed and no page overflow", !geometry.sidebarFooter && !geometry.viewportOverflow, geometry);
    check("Console keeps core navigation", await page.getByRole("link", { name: "All Organizations" }).count() === 1 &&
      await page.getByRole("link", { name: "Mailboxes" }).count() === 1);
    check("No live staging or activation button", await page.getByRole("button", { name: /Activate|Stage.*mail|Cutover/i }).count() === 0);
    check("Reserved addresses are read only", await page.getByLabel("Security notifications").isDisabled() &&
      await page.getByLabel("DMARC reports").isDisabled());
    check("Unmodified draft cannot be saved", await page.getByRole("button", { name: "Save draft" }).isDisabled());
    await page.getByRole("button", { name: "Open account menu" }).click();
    check("Avatar contains name and email", await page.getByRole("menu", { name: "Account menu" }).getByText("Morgan Test").isVisible() &&
      await page.getByRole("menu", { name: "Account menu" }).getByText("qa-admin@example.test").isVisible());
    await shot(page, "02-platform-account-menu");
    await page.getByRole("radio", { name: "Dark" }).click();
    check("Dark theme sets correct preference", await page.evaluate(() => document.documentElement.getAttribute("data-pf-theme")) === "dark");
    await page.getByRole("button", { name: "Open account menu" }).click();
    await shot(page, "03-platform-desktop-dark");
    await page.getByRole("button", { name: "Open account menu" }).click();
    const sender = page.getByLabel("Display name");
    await sender.fill("MateMail Operations");
    await page.getByRole("button", { name: "Save draft" }).click();
    await page.getByText("Configuration draft saved.", { exact: false }).waitFor({ state: "visible" });
    check("Synthetic draft saved, never staged", state.operations.join(",") === "save-draft", state.operations);
    check("Revision updated after save", await page.getByText("Draft revision 1.", { exact: false }).isVisible());
  });

  await run("Mobile navigation", { width: 390, height: 844 }, async ({ page }) => {
    await visit(page);
    await shot(page, "04-platform-mobile-closed");
    await page.getByRole("button", { name: "Open navigation" }).click();
    check("Mobile sidebar is visible after menu click", await page.getByRole("link", { name: "System Mail" }).isVisible());
    await shot(page, "05-platform-mobile-sidebar");
    await page.getByRole("link", { name: "System Mail" }).click();
    check("Mobile drawer closes on navigation", await page.locator("aside").evaluate(el => el.classList.contains("-translate-x-full")));
    check("Mobile page has no horizontal overflow", await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  });
  await run("Sign out", {}, async ({ page, state }) => {
    await visit(page);
    await page.getByRole("button", { name: "Open account menu" }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await page.waitForURL("**/platform/login", { timeout: 15000 });
    check("Sign out calls logout endpoint", state.operations.includes("logout"), state.operations);
  });
} finally {
  await browser.close();
  fs.writeFileSync(path.join(output, "report.json"), JSON.stringify(report, null, 2));
}
if (failed) {
  console.error("Platform Console QA failed:", report.errors);
  process.exitCode = 1;
} else {
  console.log("Platform Console QA passed:", report.checks.length, "checks; screenshots:", report.screenshots.join(", "));
}
