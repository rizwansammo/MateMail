import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import vm from "node:vm";

// The production route helper is TypeScript. The server's Node 22 build lacks
// native TS stripping, so remove only its simple type declarations to execute
// the exact same route predicates without fetching tools or dependencies.
const routeCode = readFileSync(new URL("../lib/matemail-splash-routes.ts", import.meta.url), "utf8")
  .replace(/export type MateMailSurface = [^;]+;/, "")
  .replace(/: MateMailSurface/g, "")
  .replace(/: string/g, "")
  .replace(/: boolean/g, "")
  .replace(/export function/g, "function");
const { needsSettingsSplash, isSettingsRoute } = vm.runInNewContext(
  routeCode + "\n({ needsSettingsSplash, isSettingsRoute })",
);

const root = join(import.meta.dirname, "..");
const read = (path) => readFileSync(join(root, path), "utf8");

test("initial and refresh splash mounts only in Hub and PostBox layouts", () => {
  const hub = read("app/app/layout.tsx");
  const postbox = read("app/postbox/layout.tsx");
  const consoleLayout = read("app/platform/layout.tsx");
  assert.match(hub, /<MateMailSplash surface="hub"/);
  assert.match(postbox, /<MateMailSplash surface="postbox"/);
  assert.doesNotMatch(consoleLayout, /MateMailSplash/);
});

test("PostBox folder changes, message changes and settings submenus have no splash", () => {
  assert.equal(needsSettingsSplash("/postbox", "/postbox", "postbox"), false);
  assert.equal(needsSettingsSplash("/postbox", "/postbox/contacts", "postbox"), false);
  assert.equal(needsSettingsSplash("/postbox/settings", "/postbox/settings/profile", "postbox"), false);
  assert.equal(isSettingsRoute("/postbox/settings", "postbox"), true);
  assert.equal(isSettingsRoute("/postbox/settings/more", "postbox"), true);
});

test("PostBox entering or leaving Settings triggers a short splash", () => {
  assert.equal(needsSettingsSplash("/postbox", "/postbox/settings", "postbox"), true);
  assert.equal(needsSettingsSplash("/postbox/settings", "/postbox", "postbox"), true);
  assert.equal(needsSettingsSplash("/postbox/contacts", "/postbox/settings", "postbox"), true);
});

test("Hub Settings boundaries trigger but Settings submenu routes do not", () => {
  assert.equal(needsSettingsSplash("/app", "/app/settings", "hub"), true);
  assert.equal(needsSettingsSplash("/app/domains", "/app/settings/integrations", "hub"), true);
  assert.equal(needsSettingsSplash("/app/settings", "/app/settings/integrations", "hub"), false);
  assert.equal(needsSettingsSplash("/app/settings/api-keys", "/app/settings/integrations", "hub"), false);
  assert.equal(needsSettingsSplash("/app/settings", "/app/mailboxes", "hub"), true);
  assert.equal(needsSettingsSplash("/app", "/platform", "hub"), false);
});

test("splash uses 05B brand asset and a bounded, dismissible transition", () => {
  const splash = read("components/matemail-splash.tsx");
  const css = read("components/matemail-splash.css");
  const svg = read("public/assets/matemail-splash-mark.svg");
  assert.match(svg, /#0C6AF0/);
  assert.match(svg, /M473 176 H529/);
  assert.match(splash, /setStage\("fading"\)/);
  assert.match(splash, /setStage\("hidden"\)/);
  assert.match(splash, /minimum = stage === "initial" \? 700 : 300/);
  assert.match(splash, /document\.addEventListener\("click", onClick, true\)/);
  assert.match(css, /mm-splash-spin/);
  assert.match(css, /prefers-reduced-motion/);
});
