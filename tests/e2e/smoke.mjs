// Browser smoke test (P1-10): drives the demo account the way a person would.
// Runs in GitHub Actions (.github/workflows/e2e.yml) against a fresh server:
//   BILLDABLE_URL_UNDER_TEST=http://127.0.0.1:8765 node tests/e2e/smoke.mjs
// Needs Playwright: npm install --no-save playwright@1.47.2 && npx playwright install --with-deps chromium
import { chromium } from "playwright";
import assert from "node:assert/strict";

const BASE = process.env.BILLDABLE_URL_UNDER_TEST || "http://127.0.0.1:8765";
const results = [];
async function step(name, fn) {
  try { await fn(); results.push(["ok", name]); console.log(`ok    ${name}`); }
  catch (err) { results.push(["FAIL", name]); console.log(`FAIL  ${name}\n      ${String(err.message || err).split("\n")[0]}`); }
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

await step("landing redirects a deep link to sign-in, then opens it", async () => {
  const ctx = await browser.newContext();
  const p = await ctx.newPage();
  await p.goto(BASE + "/jobs/job-oak");
  await p.getByText("Sign in to open that page.").waitFor();
  await p.fill("#auth-form input[name=email]", "owner@hartline.example");
  await p.fill("#auth-form input[name=password]", "demo");
  await p.click("#auth-form button[type=submit]");
  await p.getByRole("heading", { name: "Oak Street kitchen" }).waitFor();
  assert.equal(new URL(p.url()).pathname, "/jobs/job-oak");
  await ctx.close();
});

await step("Today lists what needs you and opens a job by its address", async () => {
  await page.goto(BASE + "/today");
  await page.getByRole("heading", { name: "Today" }).waitFor();
  await page.getByRole("heading", { name: /Needs you/ }).waitFor();
  await page.locator(".action-item", { hasText: "Oak Street kitchen" }).getByRole("button", { name: "Open" }).click();
  await page.getByRole("heading", { name: "Oak Street kitchen" }).waitFor();
  assert.equal(new URL(page.url()).pathname, "/jobs/job-oak");
  assert.match(await page.title(), /Oak Street kitchen/);
});

await step("the job page has one main action, and Back returns to Today", async () => {
  const primary = page.locator(".next-card .btn");
  assert.equal(await primary.count(), 1);
  assert.match(await primary.innerText(), /Clear 3 blockers/);
  await page.goBack();
  await page.getByRole("heading", { name: "Today" }).waitFor();
  assert.equal(new URL(page.url()).pathname, "/today");
});

await step("refresh keeps the page", async () => {
  await page.goto(BASE + "/money/collections");
  await page.reload();
  await page.getByRole("heading", { name: "Collections" }).waitFor();
});

await step("money path: the main button walks a job from blocked to closed", async () => {
  // Follow the one main button, whatever it says, until the job is closed.
  const seen = [];
  await page.goto(BASE + "/jobs/job-inman");
  for (let n = 0; n < 12; n++) {
    await page.getByRole("heading", { name: "Inman Park bath" }).waitFor();
    if (await page.locator(".job-head .pill", { hasText: "Closed" }).count()) break;
    const main = page.locator(".next-card .btn");
    const label = (await main.innerText()).trim();
    seen.push(label);
    if (/^Clear \d+ blocker/.test(label)) {
      await main.click();
      await page.getByRole("heading", { name: /packet/i }).waitFor();
      while (await page.locator('[data-form^="resolve-"]').count()) {
        const btn = page.locator('[data-form^="resolve-"]').first();
        const item = (await btn.getAttribute("data-form")).slice("resolve-".length);
        await btn.click();
        await page.fill(`#resolve-reason-${item}`, "Emailed to the payer on 10/12");
        await page.click(`[data-action="resolve"][data-item="${item}"]`);
        await page.locator(`#resolve-reason-${item}`).waitFor({ state: "detached" });
      }
      await page.goto(BASE + "/jobs/job-inman");
    } else if (label === "Match payment") {
      await main.click();
      const option = await page.locator("#match-inv option").first().innerText();
      await page.fill("#match-amt", option.match(/\$([\d,]+\.\d\d)/)[1].replace(/,/g, ""));
      await page.fill("#match-ref", "CHK-2201");
      await page.click('[data-action="match"]');
      await page.locator("#match-inv").waitFor({ state: "detached" });
    } else {
      await main.click();
      // The page's CSP forbids evaluating strings, so poll from here rather than with waitForFunction.
      for (let t = 0; t < 100; t++) {
        if (await page.locator(".job-head .pill", { hasText: "Closed" }).count()) break;
        const now = (await page.locator(".next-card .btn").innerText().catch(() => "")).trim();
        if (now && now !== label) break;
        await new Promise((r) => setTimeout(r, 100));
      }
    }
  }
  for (const want of ["Approve & send", "Match payment", "Send waiver", "Close for record"]) assert.ok(seen.includes(want), `main button never said ${want}: ${seen.join(" → ")}`);
  await page.locator(".job-head .pill", { hasText: "Closed" }).waitFor();
});

await step("the audit chain checks out after all that", async () => {
  await page.goto(BASE + "/settings/audit");
  await page.getByText("The chain of records checks out.").waitFor();
});

await step("phone: no page scrolls sideways at 375 px", async () => {
  const phone = await signedInPage({ width: 375, height: 812 });
  const wide = [];
  for (const path of ["/today", "/today/results", "/today/calendar", "/today/daily", "/jobs", "/jobs/job-oak", "/inbox", "/money", "/money/packets",
                      "/money/change-orders", "/money/collections", "/money/payments", "/money/waivers", "/money/retainage",
                      "/money/outbox", "/settings", "/settings/playbook", "/settings/audit", "/settings/help"]) {
    await phone.goto(BASE + path);
    await phone.locator("#view h1").first().waitFor();
    const width = await phone.evaluate(() => document.documentElement.scrollWidth);
    if (width > 375) wide.push(`${path} (${width}px)`);
  }
  assert.deepEqual(wide, [], "pages wider than the screen");
});

await step("phone: the menu drawer opens and moves between sections", async () => {
  const phone = await signedInPage({ width: 375, height: 812 });
  await phone.goto(BASE + "/today");
  await phone.click("#menu-btn");
  await phone.locator(".shell.drawer-open").waitFor();
  await phone.locator('.nav button[data-section="money"]').click();
  await phone.getByRole("heading", { name: "Pay apps" }).waitFor();
  assert.equal(new URL(phone.url()).pathname, "/money");
});

await step("unknown paths get the HTML 404 page", async () => {
  const res = await page.goto(BASE + "/no-such-page");
  assert.equal(res.status(), 404);
  await page.getByText("Go to Billdable").waitFor();
});

await step("no JavaScript errors on any page", async () => {
  assert.deepEqual(errors, []);
});

await browser.close();
const failed = results.filter(([r]) => r === "FAIL").length;
console.log(`\n${results.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
