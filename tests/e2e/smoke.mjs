// Browser smoke test (P1-10): drives the demo account the way a person would.
// Runs in GitHub Actions (.github/workflows/e2e.yml) against a fresh server:
//   DURACASH_URL_UNDER_TEST=http://127.0.0.1:8765 node tests/e2e/smoke.mjs
// Needs Playwright: npm install --no-save playwright@1.47.2 && npx playwright install --with-deps chromium
//
// This file is the runner: it signs in, then runs every tests/e2e/steps/*.mjs in name order, one
// file per work stream (10-core, 20-foundation, 30-comms-data...). Each file exports a default
// async function that gets the shared context below. A stream adds its steps to its own file and
// leaves this one alone; a step that changes the demo's data should use its own signedInPage().
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { readdirSync } from "node:fs";

const BASE = process.env.DURACASH_URL_UNDER_TEST || "http://127.0.0.1:8765";
const results = [];
async function step(name, fn) {
  try { await fn(); results.push(["ok", name]); console.log(`ok    ${name}`); }
  catch (err) { results.push(["FAIL", name]); console.log(`FAIL  ${name}\n      ${String(err.message || err).replace(/\s+/g, " ").slice(0, 400)}`); }
}

const browser = await chromium.launch();
const errors = [];

async function signedInPage(viewport) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(BASE + "/");
  const status = await page.evaluate(async () => (await fetch("/api/auth/login", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: "owner@hartline.example", password: "demo" }),
  })).status);
  assert.equal(status, 200, "demo sign-in");
  return page;
}

const page = await signedInPage({ width: 1280, height: 900 });

// The in-page confirmation (askConfirm) that sends, marks done and finishes now ask first (R3-03).
const confirmDialog = (p) => p.locator("dialog.confirm-dialog");
async function confirmIfAsked(p) {
  if (await confirmDialog(p).count()) {
    await confirmDialog(p).locator(".confirm-actions .btn:not(.secondary)").click();
    await confirmDialog(p).waitFor({ state: "detached" });
  }
}

const ctx = { browser, page, BASE, step, signedInPage, confirmDialog, confirmIfAsked, errors };
const stepsDir = new URL("./steps/", import.meta.url);
for (const f of readdirSync(stepsDir).filter(f => f.endsWith(".mjs")).sort()) {
  const mod = await import(new URL(f, stepsDir));
  await mod.default(ctx);
}
await step("no JavaScript errors on any page", async () => {
  assert.deepEqual(errors, []);
});

await browser.close();
const failed = results.filter(([r]) => r === "FAIL").length;
console.log(`\n${results.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
