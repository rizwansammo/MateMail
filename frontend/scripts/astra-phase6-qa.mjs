/**
 * Phase 6: approved Astra visual-token and interaction acceptance in browsers.
 * Synthetic API fixtures only. Produces desktop/mobile screenshots; no mail.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";

const base = process.env.VISUAL_QA_URL || "http://127.0.0.1:3100";
const output = process.env.VISUAL_QA_OUTPUT || "visual-qa-artifacts";
fs.mkdirSync(output, { recursive: true });
const tid = "d9bdde9d-1234-4123-8123-777777777777";
const uid = "bd1a5653-1234-4234-8234-888888888888";
const token = ["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
  Buffer.from(JSON.stringify({ tenant_id: tid, exp: Math.floor(Date.now()/1000)+3600 })).toString("base64url"),
  "synthetic-phase6"].join(".");
const checks = [];
function check(name, condition, details = {}) {
  checks.push({ name, passed: Boolean(condition), details });
  assert.ok(condition, name + " " + JSON.stringify(details));
}
const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });

async function prepare(viewport) {
  const context = await browser.newContext({
    viewport, deviceScaleFactor: 1, reducedMotion: "reduce"
  });
  await context.route("**/api/**", async route => {
    const endpoint = new URL(route.request().url()).pathname;
    let data = [];
    if (endpoint === "/api/auth/refresh/") data = { access: token };
    else if (endpoint === "/api/auth/me/")
      data = { id: uid, email: "owner@example.test", full_name: "QA Owner", email_verified: true,
        two_factor_enabled: false, is_platform_admin: false };
    else if (endpoint === "/api/workspaces/" + tid + "/")
      data = { id: tid, name: "QA Workspace", slug: "qa-workspace", status: "active" }; // Backend does not include member role in detail payload.
    else if (endpoint === "/api/workspaces/" + tid + "/onboarding/")
      data = { workspace_created: true, domain_added: true, dns_verified: true,
        first_mailbox_created: true, completed: true, completed_at: "2026-01-01T00:00:00Z" };
    else if (endpoint === "/api/workspaces/" + tid + "/stats/")
      data = { my_role: "owner", tenant_status: "active", tenant_plan: "business", mailbox_count: 7, active_mailbox_count: 6, domain_count: 2, active_domain_count: 1, storage_used_mb: 1536, storage_quota_mb: 10240, member_count: 2 };
    else if (endpoint === "/api/workspaces/" + tid + "/members/")
      data = [{ id: "a1", email: "owner@example.test", full_name: "QA Owner", role: "owner", status: "active" },
              { id: "a2", email: "admin@example.test", full_name: "QA Admin", role: "admin", status: "active" }];
    else if (endpoint === "/api/domains/")
      data = [
        { id:"d1", domain:"healthy.example.test", status:"active", ownership_verified:true, dns_health_score:100, mail_service_ready:true },
        { id:"d2", domain:"pending.example.test", status:"pending", ownership_verified:false, dns_health_score:10, mail_service_ready:false }
      ];
    else if (endpoint === "/api/logs/")
      data = [{ id:"l1", event_type:"domain_verified", source:"healthy.example.test", result:"success", created_at:"2026-10-10T00:00:00Z" }];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
  });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", err => errors.push(err.message));
  await page.goto(base + "/app", { waitUntil: "domcontentloaded" });
  await page.locator(".astra-sidebar").waitFor({ state: "visible" });
  await page.waitForTimeout(600);
  return { context, page, errors };
}
async function measure(page) {
  return page.evaluate(() => {
    const root = document.querySelector(".ws.astra-hub");
    const sidebar = document.querySelector(".astra-sidebar");
    const topbar = document.querySelector(".astra-topbar");
    const style = getComputedStyle(root);
    return {
      primary: style.getPropertyValue("--primary").trim(),
      background: style.getPropertyValue("--background").trim(),
      radius: style.getPropertyValue("--radius").trim(),
      sidebar: Math.round(sidebar.getBoundingClientRect().width),
      topbar: Math.round(topbar.getBoundingClientRect().height),
      scroll: document.documentElement.scrollWidth,
      width: window.innerWidth,
      scoped: root.classList.contains("astra-hub"),
    };
  });
}
const report = { phase: "Phase 6 Astra approved baseline acceptance",
  fixture: "No real credentials, domains, mailboxes or mail delivery",
  checks };
try {
  const desktop = await prepare({ width: 1440, height: 900 });
  let m = await measure(desktop.page);
  await desktop.page.getByRole("heading", { name: "Your workspace, in focus." }).waitFor();
  check("Overview uses approved Astra welcome, not legacy dashboard",
    (await desktop.page.getByRole("heading", { name: "Your workspace, in focus." }).count()) === 1
    && (await desktop.page.getByText("Workspace overview").count()) === 0
    && (await desktop.page.locator(".portal-metrics-grid").count()) === 0);
  for (const section of ["Domain readiness","Needs attention","Recent activity","Workspace essentials"]) {
    check("Approved Astra Overview section: " + section,
      (await desktop.page.getByRole("heading", { name: section }).count()) === 1);
  }
  check("Real fixture stats mapped into Astra metrics, without seed/demo numbers",
    (await desktop.page.getByText("7", { exact: true }).count()) > 0 &&
    (await desktop.page.getByText("2", { exact: true }).count()) > 0 &&
    (await desktop.page.getByText("healthy.example.test").count()) > 0 &&
    (await desktop.page.getByText("Northstar Studio").count()) === 0);
  check("Original organization identity card is visible in sidebar",
    (await desktop.page.locator(".astra-sidebar .astra-org-card").count()) === 1 &&
    (await desktop.page.locator(".astra-sidebar").getByText("QA Workspace").count()) === 1);
  await desktop.page.getByRole("link", { name: "Quarantine" }).waitFor();
  for (const label of ["Delegation","Quarantine","Mail queue","Activity log","Backup history","Connected apps"]) {
    check("Original prototype navigation preserved: " + label,
      (await desktop.page.locator(".astra-sidebar").getByRole("link", { name: label, exact: true }).count()) === 1);
  }

  check("Light design tokens match approved Astra reference",
    m.primary === "#3d54b8" && m.background === "#f7f8fa" && m.radius === "4px", m);
  check("Desktop shell matches 244px/60px approved geometry",
    m.sidebar === 244 && m.topbar === 60, m);
  check("Hub visual scope is isolated", m.scoped);
  await desktop.page.screenshot({ path: path.join(output, "phase6-light-desktop.png"), animations: "disabled" });
  await desktop.page.getByRole("button", { name: "Switch to dark theme" }).click();
  await desktop.page.waitForTimeout(130);
  m = await measure(desktop.page);
  check("Dark design tokens match approved Astra reference",
    m.primary === "#9aacff" && m.background === "#141b26" && m.radius === "4px", m);
  await desktop.page.screenshot({ path: path.join(output, "phase6-dark-desktop.png"), animations: "disabled" });
  await desktop.page.getByRole("button", { name: "Search workspace" }).click();
  await desktop.page.getByRole("dialog", { name: "Search Hub" }).waitFor();
  check("Astra global search remains keyboard-accessible",
    (await desktop.page.getByRole("textbox", { name: "Search Hub pages and resources" }).count()) === 1);
  await desktop.page.keyboard.press("Escape");
  check("Escape closes search overlay",
    (await desktop.page.getByRole("dialog", { name: "Search Hub" }).count()) === 0);
  check("Desktop browser has no unhandled errors", desktop.errors.length === 0, { errors: desktop.errors });
  await desktop.context.close();

  const mobile = await prepare({ width: 390, height: 844 });
  m = await measure(mobile.page);
  check("Mobile shell has no document-wide horizontal overflow", m.scroll <= m.width + 1, m);
  check("Mobile topbar retains approved 60px geometry", m.topbar === 60, m);
  await mobile.page.screenshot({ path: path.join(output, "phase6-light-mobile.png"), animations: "disabled" });
  check("Mobile browser has no unhandled errors", mobile.errors.length === 0, { errors: mobile.errors });
  await mobile.context.close();
} catch (error) {
  report.error = error.stack || String(error);
  process.exitCode = 1;
} finally {
  fs.writeFileSync(path.join(output, "phase6-report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  await browser.close();
}
