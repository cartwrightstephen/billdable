// Browser smoke test (P1-10): drives the demo account the way a person would.
// Runs in GitHub Actions (.github/workflows/e2e.yml) against a fresh server:
//   DURACASH_URL_UNDER_TEST=http://127.0.0.1:8765 node tests/e2e/smoke.mjs
// Needs Playwright: npm install --no-save playwright@1.47.2 && npx playwright install --with-deps chromium
import { chromium } from "playwright";
import assert from "node:assert/strict";

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

await step("New job: a wrong field is marked in place and nothing typed is lost", async () => {
  await page.goto(BASE + "/jobs");
  await page.getByRole("button", { name: "New job" }).first().click();
  await page.fill("#nj-name", "Smoke test porch");
  await page.fill("#nj-address", "1 Porch Lane");
  await page.fill("#nj-amount", "48,500");
  await page.fill("#nj-customer", "Smoke Homeowner");
  await page.fill("#nj-approver", "Sam Smoke");
  await page.fill("#nj-email", "not an email");
  await page.click('[data-action="create-job"]');
  await page.locator('#nj-email[aria-invalid="true"]').waitFor();
  assert.match(await page.locator("#nj-email-err").innerText(), /ap@builder\.com/);
  assert.equal(await page.inputValue("#nj-name"), "Smoke test porch");
  assert.equal(await page.inputValue("#nj-amount"), "$48,500.00", "dollars are tidied when the box is left");
  await page.getByRole("button", { name: "Cancel" }).first().click();  // nothing saved
});

await step("sign-up says where the confirmation link went", async () => {
  const ctx = await browser.newContext();
  const p = await ctx.newPage();
  p.on("pageerror", (e) => errors.push(e.message));
  const email = `smoke-${Date.now()}@example.com`;
  await p.goto(BASE + "/");
  await p.fill("#auth-name", "Sam Smoke");
  await p.fill("#auth-company", "Smoke Builders");
  await p.fill("#auth-email", email);
  await p.fill("#auth-password", "smoke test password");
  await p.check("#auth-terms");
  await p.click("#auth-form button[type=submit]");
  const panel = p.locator(".check-email");
  await panel.waitFor();
  // CI has no mail server, so the panel gives the server's reason instead of "We sent a confirmation link".
  assert.match(await panel.innerText(), new RegExp(`(We sent a confirmation link|No confirmation link went) to ${email.replace(/[.]/g, "\\.")}`));
  assert.match(await panel.innerText(), /set up your first job meanwhile/);
  await panel.getByRole("button", { name: "Got it" }).click();
  await panel.waitFor({ state: "detached" });
  await ctx.close();
});

await step("refresh keeps the page", async () => {
  await page.goto(BASE + "/money/collections");
  await page.reload();
  await page.getByRole("heading", { name: "Collections" }).waitFor();
});

await step("money path: the main button walks a job from blocked to closed", async () => {
  // Follow the one main button, whatever it says, until the job is closed.
  const seen = [];
  let triedRefusal = false;
  await page.goto(BASE + "/jobs/job-inman");
  for (let n = 0; n < 12; n++) {
    await page.getByRole("heading", { name: "Inman Park bath" }).waitFor();
    if (await page.locator(".job-head .pill", { hasText: "Closed" }).count()) break;
    const main = page.locator(".next-card .btn");
    const label = (await main.innerText()).trim();
    seen.push(label);
    // Paperwork alone reads "Add N missing documents" (R1-08); an unsigned CO keeps "Clear N blockers".
    if (/^(Clear \d+ blocker|Add \d+ missing document)/.test(label)) {
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
    } else if (label === "Review & send") {
      // Every send passes "Before you send" (R1-06): Today's button opens the pay app there.
      await page.goto(BASE + "/today");
      await page.locator(".action-item", { hasText: "Inman Park bath" }).getByRole("button", { name: "Review & send" }).click();
      await page.locator(".send-check").waitFor({ state: "visible" });
      assert.match(new URL(page.url()).pathname, /^\/money\/pay-app\//);
      await page.click('.send-check [data-action="send"]');
      await page.locator('.send-check [data-action="send"]').waitFor({ state: "detached" });
      // The demo never emails, so Today lists the pay app's email as one that didn't go out (R1-11).
      await page.goto(BASE + "/today");
      await page.locator(".action-item", { hasText: "Inman Park bath" }).filter({ hasText: "Didn't go out" })
        .getByRole("button", { name: "Send again" }).waitFor();
      await page.goto(BASE + "/jobs/job-inman");
    } else if (label === "Choose waiver and send") {
      await main.click();
      await page.getByRole("heading", { name: /^Waivers:/ }).waitFor();
      const sendWaiver = page.locator('[data-action="waiver"]');
      await sendWaiver.first().waitFor();
      const before = await sendWaiver.count();
      await sendWaiver.first().click();
      for (let t = 0; t < 100 && (await sendWaiver.count()) >= before; t++) await new Promise((r) => setTimeout(r, 100));
      await page.goto(BASE + "/jobs/job-inman");
    } else if (label === "Match payment") {
      await main.click();
      const option = await page.locator("#match-inv option").first().innerText();
      await page.fill("#match-amt", option.match(/\$([\d,]+\.\d\d)/)[1].replace(/,/g, ""));
      await page.fill("#match-ref", "CHK-2201");
      await page.click('[data-action="match"]');
      await page.locator("#match-inv").waitFor({ state: "detached" });
    } else if (label === "Close for record" && !triedRefusal) {
      // A refusal shows under the button that was tapped: an unsigned change order added
      // behind the page's back makes Close fail, and the card says why.
      triedRefusal = true;
      const coId = await page.evaluate(async () => {
        const res = await fetch("/api/jobs/job-inman/add-co", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: "Smoke test extra", amount_cents: 10000 }) });
        const st = await res.json();
        return st.jobs.find((j) => j.id === "job-inman").change_orders.find((c) => c.status === "pending").id;
      });
      await main.click();
      const card = page.locator(".next-card + .action-error");
      await card.waitFor();
      assert.match(await card.innerText(), /change order/i);
      await page.evaluate(async (co) => { await fetch(`/api/jobs/job-inman/withdraw-co/${co}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); }, coId);
      await page.goto(BASE + "/jobs/job-inman");
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
  for (const want of ["Review & send", "Match payment", "Choose waiver and send", "Close for record"]) assert.ok(seen.includes(want), `main button never said ${want}: ${seen.join(" → ")}`);
  await page.locator(".job-head .pill", { hasText: "Closed" }).waitFor();
});

await step("the audit chain checks out after all that", async () => {
  await page.goto(BASE + "/settings/audit");
  await page.getByText("The chain of records checks out.").waitFor();
});

await step("Today's queue: one main button per row, and a follow-up goes in one tap", async () => {
  await page.goto(BASE + "/today");
  const rows = page.locator(".action-queue .action-item");
  await rows.first().waitFor();
  const n = await rows.count();
  for (let i = 0; i < n; i++) {
    assert.equal(await rows.nth(i).locator(".action-buttons .btn:not(.secondary)").count(), 1, `row ${i + 1} has one main button`);
  }
  const follow = () => page.locator(".action-item", { hasText: "Turner office TI" }).getByRole("button", { name: "Send follow-up" });
  if (await follow().count()) {
    await follow().click();
    await follow().waitFor({ state: "detached" });  // followed up: not due again until the payer's window passes
  }
});

await step("the job page shows what happened, newest first", async () => {
  await page.goto(BASE + "/jobs/job-inman");
  await page.locator(".job-history .timeline li").first().waitFor();
  assert.ok(await page.locator(".job-history .timeline li").count() >= 4, "the money path's actions are in the history");
  assert.match(await page.locator(".job-history .timeline li").first().innerText(), /clos/i);
});

await step("a confirmation is an in-page dialog, and Escape cancels it", async () => {
  await page.goto(BASE + "/settings");
  await page.locator("#reset").click();
  await page.locator("dialog.confirm-dialog").waitFor();
  await page.keyboard.press("Escape");
  await page.locator("dialog.confirm-dialog").waitFor({ state: "detached" });
  const inman = await page.evaluate(async () => (await (await fetch("/api/state")).json()).jobs.find((j) => j.id === "job-inman"));
  assert.ok(inman && inman.closed, "nothing was reset");
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
  await page.getByText("Go to Duracash").waitFor();
});

await step("no JavaScript errors on any page", async () => {
  assert.deepEqual(errors, []);
});

await browser.close();
const failed = results.filter(([r]) => r === "FAIL").length;
console.log(`\n${results.length - failed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
