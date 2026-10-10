/** Phase 5 isolated browser tests. Every API request is intercepted. */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";

const base = process.env.VISUAL_QA_URL || "http://127.0.0.1:3100";
const out = process.env.VISUAL_QA_OUTPUT || "visual-qa-artifacts";
fs.mkdirSync(out, { recursive: true });
const tid = "d9bdde9d-1234-4123-8123-777777777777";
const uid = "bd1a5653-1234-4234-8234-888888888888";
const token = ["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
  Buffer.from(JSON.stringify({ tenant_id: tid, exp: Math.floor(Date.now()/1000)+3600 })).toString("base64url"),
  "synthetic"].join(".");
const report = { phase: "Astra Phase 5 optional mailbox invitation", checks: [],
  note: "All API routes synthetic. No actual mailboxes, invitations or credentials." };
const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
function check(name, okay, details = {}) {
  report.checks.push({ name, passed: Boolean(okay), details });
  assert.ok(okay, name + ": " + JSON.stringify(details));
}
async function fixture(role = "owner") {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 },
    reducedMotion: "reduce" });
  const calls = [];
  const invites = [];
  const members = [{ id: uid, email: "owner@example.test",
    full_name: "Owner", role: "owner", status: "active",
    created_at: "2026-01-01T00:00:00Z" }];
  await ctx.route("**/api/**", async route => {
    const req = route.request();
    const endpoint = new URL(req.url()).pathname;
    const method = req.method();
    const payload = req.postData() ? JSON.parse(req.postData()) : null;
    if (method !== "GET" && !endpoint.startsWith("/api/auth/"))
      calls.push({ endpoint, method, payload });
    let response = [], status = 200;
    if (endpoint === "/api/auth/refresh/") response = { access: token };
    else if (endpoint === "/api/auth/me/")
      response = { id: uid, email: "owner@example.test", full_name: "Owner",
        email_verified: true, two_factor_enabled: false, is_platform_admin: false };
    else if (endpoint === "/api/workspaces/" + tid + "/")
      response = { id: tid, name: "QA Workspace", slug: "qa", status: "active", role };
    else if (endpoint === "/api/workspaces/" + tid + "/stats/")
      response = { my_role: role, tenant_status: "active" };
    else if (endpoint === "/api/workspaces/" + tid + "/onboarding/")
      response = { workspace_created: true, domain_added: true,
        dns_verified: true, first_mailbox_created: true, completed: true };
    else if (endpoint === "/api/workspaces/" + tid + "/members/" && method === "GET")
      response = members;
    else if (endpoint === "/api/teams/invites/" && method === "GET") {
      if (role === "read_only") { status = 403; response = { detail: "Forbidden" }; }
      else response = invites;
    } else if (endpoint === "/api/teams/invites/" && method === "POST") {
      if (role === "read_only") { status = 403; response = { detail: "Forbidden" }; }
      else if (!payload.email.endsWith("@example.test")) {
        status = 400; response = { detail: "Verified domain required." };
      } else {
        response = { id: "a0000000-0000-4000-8000-000000000001",
          email: payload.email, role: payload.role, is_pending: true,
          create_mailbox: Boolean(payload.create_mailbox),
          invited_by_email: "owner@example.test",
          created_at: "2026-01-01T00:00:00Z", expires_at: "2027-01-01T00:00:00Z",
          invite_url: "https://example.test/accept-invite?token=synthetic",
          email_delivered: false };
        invites.push(response);
        status = 201;
      }
    }
    await route.fulfill({ status, contentType: "application/json",
      body: JSON.stringify(response) });
  });
  const page = await ctx.newPage(), errors = [];
  page.on("pageerror", err => errors.push(err.message));
  await page.goto(base + "/app/team", { waitUntil: "domcontentloaded" });
  await page.locator(".astra-users-page").waitFor();
  await page.getByRole("heading", { name: "Users & access" }).waitFor();
  await page.waitForTimeout(500);
  return { ctx, page, calls, errors };
}
try {
  const owner = await fixture();
  const invite = owner.page.getByRole("button", { name: "Invite member" }).first();
  await invite.click();
  const dlg = owner.page.getByRole("dialog", { name: "Invite member" });
  await dlg.waitFor({ state: "visible" });
  check("Phase 5 option is available and OFF by default",
    (await dlg.getByLabel("Create a mailbox for this user").count()) === 1 &&
    !(await dlg.getByLabel("Create a mailbox for this user").isChecked()));
  check("Off-state preserves explicit Hub-only behavior",
    (await dlg.getByText(/does not create or assign a mailbox/i).count()) === 1);
  await dlg.getByLabel("Create a mailbox for this user").check();
  check("Opt-in describes acceptance-time password, not an admin-owned credential",
    (await dlg.getByText(/when the person accepts/i).count()) === 1);
  await dlg.getByLabel("Email address").fill("newhire@example.test");
  await dlg.getByLabel("Workspace role").selectOption("support");
  await dlg.getByRole("button", { name: "Send invitation" }).click();
  await owner.page.getByText(/Invitation link \(shown once\)/).waitFor();
  const request = owner.calls.find(c => c.endpoint === "/api/teams/invites/" &&
    c.method === "POST");
  check("Opt-in API sends only the member email, role and explicit true flag",
    request?.payload?.email === "newhire@example.test" &&
    request?.payload?.role === "support" && request?.payload?.create_mailbox === true &&
    Object.keys(request?.payload || {}).length === 3, { request });
  check("One-time invite link is available if email cannot be delivered",
    (await owner.page.getByRole("button", { name: "Copy invite link" }).count()) === 1);
  check("Pending list identifies mailbox creation intent",
    (await owner.page.getByText("Mailbox requested").count()) === 1);
  check("No UI JavaScript faults", owner.errors.length === 0, { errors: owner.errors });
  await owner.ctx.close();

  const viewer = await fixture("read_only");
  check("Read-only members cannot see invitation or mailbox-creation controls",
    (await viewer.page.getByRole("button", { name: "Invite member" }).count()) === 0);
  check("Read-only performs no mutations", viewer.calls.length === 0);
  await viewer.ctx.close();
} catch (error) {
  report.error = error.stack || String(error);
  process.exitCode = 1;
} finally {
  fs.writeFileSync(path.join(out, "phase5-report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  await browser.close();
}
