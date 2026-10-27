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

// The in-page confirmation (askConfirm) that sends, marks done and finishes now ask first (R3-03).
const confirmDialog = (p) => p.locator("dialog.confirm-dialog");
async function confirmIfAsked(p) {
  if (await confirmDialog(p).count()) {
    await confirmDialog(p).locator(".confirm-actions .btn:not(.secondary)").click();
    await confirmDialog(p).waitFor({ state: "detached" });
  }
}

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
  // This device has signed in now: after signing out, the landing opens on Sign in (R2-02).
  await p.evaluate(() => fetch("/api/auth/logout", { method: "POST" }));
  await p.goto(BASE + "/");
  await p.locator("#signup h2", { hasText: "Sign in" }).waitFor();
  assert.equal(await p.locator("#auth-name").count(), 0, "no sign-up fields");
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
  const primary = page.locator(".next-card .primary-action");
  assert.equal(await primary.count(), 1);
  // Tabs and job-page buttons carry no tooltips; each button says why on its second line (R3-04).
  assert.equal(await page.locator(".tabs .tab[title], .next-card .btn[title], .more-actions-rest .btn[title]").count(), 0);
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
  // Five boxes up front (R2-01); the rest wait under "More details (optional)".
  assert.equal(await page.locator('[data-form-key="newjob"] > .form-grid [data-field]').count(), 5);
  assert.deepEqual(await page.locator('[data-form-key="newjob"] > .form-grid [data-field]').evaluateAll(els => els.map(e => e.id)),
    ["nj-name", "nj-address", "nj-customer", "nj-role", "nj-amount"]);
  assert.equal(await page.locator("#nj-email").isVisible(), false, "More details starts folded");
  await page.getByText("More details (optional)").click();
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
  // The setup checklist's first step goes to the company address box (R2-03).
  const checklist = p.locator(".onboarding");
  await checklist.waitFor();
  assert.match(await checklist.innerText(), /Add your company address/);
  await checklist.getByRole("button", { name: "Company details" }).click();
  await p.locator("#co-address").waitFor();
  assert.equal(await p.evaluate(() => document.activeElement && document.activeElement.id), "co-address");
  assert.equal(new URL(p.url()).pathname, "/settings");
  await ctx.close();
});

await step("the landing opens a sample company without an account", async () => {  // R2-06
  const ctx = await browser.newContext();
  const p = await ctx.newPage();
  p.on("pageerror", (e) => errors.push(e.message));
  await p.goto(BASE + "/");
  await p.locator("#land-demo", { hasText: "Look around a sample company" }).click();
  await p.getByRole("heading", { name: "Today" }).waitFor();
  assert.equal((await p.evaluate(async () => (await (await fetch("/api/auth/me")).json()).user.company_id)), "co-hartline");
  await ctx.close();
});

await step("a first job: Today, the Daily record and the company address", async () => {  // R2-04, R2-05, R2-07
  const ctx = await browser.newContext();
  const p = await ctx.newPage();
  p.on("pageerror", (e) => errors.push(e.message));
  await p.goto(BASE + "/");
  const jobId = await p.evaluate(async (email) => {
    const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    await post("/api/auth/signup", { accept_terms: true, email, password: "smoke test password", name: "Ann First", company_name: "First Builders" });
    const st = await (await post("/api/jobs", { name: "First porch", address: "2 Elm St", customer: "Lee Homeowner", role: "Owner",
      ap_email: "lee@example.com", contract_cents: 1200000 })).json();
    return st.jobs[0].id;
  }, `first-${Date.now()}@example.com`);
  // One new job: Today says it isn't billed yet, and its button opens the job.
  await p.goto(BASE + "/today");
  const row = p.locator(".action-item", { hasText: "First porch" });
  await row.locator(".pill", { hasText: "Not billed yet" }).waitFor();
  await row.getByRole("button", { name: "Bill progress" }).click();
  await p.getByRole("heading", { name: "First porch" }).waitFor();
  // The progress form says where the job stands and what the pay app comes to (R3-01).
  await p.locator(".next-card .primary-action", { hasText: "Progress pay app" }).click();
  await p.locator(".progress-context", { hasText: "Billed so far $0.00 of $12,000.00 contract (0%)" }).waitFor();
  await p.fill("#progress-draw", "3,000");
  assert.equal(await p.locator("#progress-preview").innerText(), "This pay app: $3,000.00.");
  await p.locator('[data-action="progress-mode"][data-mode="percent"]').click();
  await p.fill("#progress-pct", "50");
  assert.equal(await p.locator("#progress-preview").innerText(), "This pay app: $6,000.00.");
  // The only open job is picked on the Daily record.
  await p.goto(BASE + "/today/daily");
  await p.locator("#daily-job").waitFor();
  assert.equal(await p.inputValue("#daily-job"), jobId);
  // No company address yet: Before you send says so; once it is filled in, the notice is gone.
  const invId = await p.evaluate(async (id) => {
    const st = await (await fetch(`/api/jobs/${id}/done`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ notes: "" }) })).json();
    return st.jobs.find((j) => j.id === id).invoices[0].id;
  }, jobId);
  await p.goto(`${BASE}/money/pay-app/${jobId}/${invId}`);
  const warn = p.locator(".send-check .no-address");
  await warn.waitFor();
  assert.match(await warn.innerText(), /Your company address is blank on this PDF/);
  await warn.getByRole("button", { name: "Company details" }).click();
  await p.locator("#co-address").waitFor();
  await p.fill("#co-address", "2 Elm St, Decatur, GA 30030");
  await p.click('[data-action="save-company"]');
  for (let t = 0; t < 50; t++) {
    const addr = await p.evaluate(async () => (await (await fetch("/api/state")).json()).company.address);
    if (addr) break;
    await new Promise((r) => setTimeout(r, 100));
  }
  await p.goto(`${BASE}/money/pay-app/${jobId}/${invId}`);
  await p.locator(".send-check").waitFor();
  assert.equal(await p.locator(".send-check .no-address").count(), 0);
  await ctx.close();
});

await step("a job with a draft: three buttons, the rest under More, a change order and a photo for the paperwork", async () => {  // R3-04, R3-05, R3-06
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const p = await ctx.newPage();
  p.on("pageerror", (e) => errors.push(e.message));
  await p.goto(BASE + "/");
  const jobId = await p.evaluate(async (email) => {
    const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    await post("/api/auth/signup", { accept_terms: true, email, password: "smoke test password", name: "Dee Draft", company_name: "Draft Builders" });
    const st = await (await post("/api/jobs", { name: "Draft deck", address: "4 Oak St", customer: "Rae Homeowner", role: "Owner",
      ap_email: "rae@example.com", contract_cents: 2000000 })).json();
    const id = st.jobs[0].id;
    await post(`/api/jobs/${id}/add-record`, { label: "Deck notes", kind: "note", note: "Joists in" });
    await post(`/api/jobs/${id}/progress`, { period_cents: 500000 });
    return id;
  }, `draft-${Date.now()}@example.com`);
  await p.goto(`${BASE}/jobs/${jobId}`);
  await p.locator(".next-card .primary-action").waitFor();
  assert.equal(await p.locator(".next-card .next-actions .btn").count(), 3, "one main button and two beside it");
  assert.match(await p.locator(".more-actions-rest > summary").innerText(), /^More \(\d+\)$/);
  // A change order from the job page; once saved, Request signature is right there.
  await p.locator(".more-actions-rest > summary").click();
  await p.locator('.more-actions-rest [data-action="new-co"]').click();
  await p.fill("#co-title", "Owner buys the railing");
  await p.fill("#co-amt", "-500");
  await p.click('[data-action="add-co-here"]');
  const made = p.locator(".new-co-card");
  await made.waitFor();
  assert.match(await made.innerText(), /CO 1: Owner buys the railing/);
  assert.match(await made.innerText(), /A credit of \$500\.00/);
  await made.getByRole("button", { name: "Request signature" }).waitFor();
  // On a phone, a missing paperwork item offers Take a photo.
  await p.setViewportSize({ width: 375, height: 812 });
  await p.locator(".next-card .primary-action").click();
  await p.getByRole("heading", { name: /packet/i }).waitFor();
  const missing = p.locator(".packet-item.absent").first();
  await missing.getByRole("button", { name: "Take a photo" }).waitFor({ state: "visible" });
  assert.equal(await missing.locator('[data-action="pick-files"][data-capture="1"][data-attach]').count(), 1);
  assert.equal(await missing.locator("select option").first().innerText(), "Choose a record…");
  await ctx.close();
});

await step("refresh keeps the page", async () => {
  await page.goto(BASE + "/money/collections");
  await page.reload();
  await page.getByRole("heading", { name: "Collections" }).waitFor();
});

await step("an unsettled job lists what stands before Finish job, each with its buttons", async () => {
  // Turner office TI is done, with money still owed on a pay app and retainage held back (R3-08).
  await page.goto(BASE + "/jobs/job-turner");
  await page.getByRole("heading", { name: "Turner office TI" }).waitFor();
  await page.getByRole("heading", { name: "Before you can finish this job" }).waitFor();
  assert.equal(await page.locator(".close-blocker-list li").count(), 2);
  await page.locator('.close-blocker-list li[data-blocker="outstanding"]').getByRole("button", { name: "Record a payment" }).waitFor();
  await page.locator('.close-blocker-list li[data-blocker="retainage"]').getByRole("button", { name: "Record retainage paid" }).waitFor();
  assert.equal(await page.locator(".next-card .btn", { hasText: "Finish job" }).count(), 0, "no Finish job while unsettled");
});

await step("money path: the main button walks a job from blocked to closed", async () => {
  // Follow the one main button, whatever it says, until the job is closed.
  const seen = [];
  let triedRefusal = false;
  let triedCancel = false;
  await page.goto(BASE + "/jobs/job-inman");
  for (let n = 0; n < 12; n++) {
    await page.getByRole("heading", { name: "Inman Park bath" }).waitFor();
    if (await page.locator(".job-head .pill", { hasText: "Closed" }).count()) break;
    // While the customer has the pay app and no follow-up is due there is no main button (R3-04):
    // record the payment from the button beside it.
    await page.locator(".next-card").waitFor();
    const hasMain = (await page.locator(".next-card .primary-action").count()) > 0;
    const main = hasMain ? page.locator(".next-card .primary-action") : page.locator(".next-card .btn.secondary", { hasText: "Record a payment" });
    const label = hasMain ? (await main.innerText()).trim() : "Record a payment";
    seen.push(hasMain ? label : "(waiting) Record a payment");
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
    } else if (label === "Record a payment") {
      await main.click();
      const option = await page.locator("#match-inv option").first().innerText();
      // The amount starts at what is still owed on the pay app, and the date at today (R3-02).
      assert.equal((await page.inputValue("#match-amt")).replace(/[$,]/g, ""), option.match(/\$([\d,]+\.\d\d)/)[1].replace(/,/g, ""));
      assert.match(await page.inputValue("#match-date"), /^\d{4}-\d{2}-\d{2}$/);
      await page.fill("#match-ref", "CHK-2201");
      await page.click('[data-action="match"]');
      await page.locator("#match-inv").waitFor({ state: "detached" });
    } else if (label === "Finish job" && triedRefusal && !triedCancel) {
      // Finishing asks first; Cancel changes nothing (R3-03).
      triedCancel = true;
      await main.click();
      await confirmDialog(page).waitFor();
      assert.match(await confirmDialog(page).innerText(), /Finish Inman Park bath\? It moves to Job history/);
      await confirmDialog(page).getByRole("button", { name: "Cancel" }).click();
      await confirmDialog(page).waitFor({ state: "detached" });
      const inman = await page.evaluate(async () => (await (await fetch("/api/state")).json()).jobs.find((j) => j.id === "job-inman"));
      assert.ok(inman && !inman.closed, "Cancel left the job open");
    } else if (label === "Finish job" && !triedRefusal) {
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
      await confirmIfAsked(page);
      const card = page.locator(".next-card + .action-error");
      await card.waitFor();
      assert.match(await card.innerText(), /change order/i);
      await page.evaluate(async (co) => { await fetch(`/api/jobs/job-inman/withdraw-co/${co}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); }, coId);
      await page.goto(BASE + "/jobs/job-inman");
    } else {
      await main.click();
      await confirmIfAsked(page);
      // The page's CSP forbids evaluating strings, so poll from here rather than with waitForFunction.
      for (let t = 0; t < 100; t++) {
        if (await page.locator(".job-head .pill", { hasText: "Closed" }).count()) break;
        const now = (await page.locator(".next-card .primary-action").innerText({ timeout: 500 }).catch(() => "")).trim();
        if (now && now !== label) break;
        await new Promise((r) => setTimeout(r, 100));
      }
    }
  }
  for (const want of ["Review & send", "(waiting) Record a payment", "Choose waiver and send", "Finish job"]) assert.ok(seen.includes(want), `main button never said ${want}: ${seen.join(" → ")}`);
  await page.locator(".job-head .pill", { hasText: "Closed" }).waitFor();
});

await step("the audit chain checks out after all that", async () => {
  await page.goto(BASE + "/settings/audit");
  await page.getByText("The chain of records checks out.").waitFor();
});

await step("Today's queue: one main button per row, and a follow-up names who it goes to first", async () => {
  await page.goto(BASE + "/today");
  const rows = page.locator(".action-queue .action-item");
  await rows.first().waitFor();
  const n = await rows.count();
  for (let i = 0; i < n; i++) {
    assert.equal(await rows.nth(i).locator(".action-buttons .btn:not(.secondary)").count(), 1, `row ${i + 1} has one main button`);
  }
  // Three rows show until "and N more" is tapped (R2-04).
  if (await page.locator(".queue-more").count()) {
    assert.equal(n, 3);
    await page.locator(".queue-more").click();
    await page.locator(".queue-more").waitFor({ state: "detached" });
    assert.ok(await rows.count() > 3);
  }
  const follow = () => page.locator(".action-item", { hasText: "Turner office TI" }).getByRole("button", { name: "Send follow-up" });
  if (await follow().count()) {
    await follow().click();
    await confirmDialog(page).waitFor();
    assert.match(await confirmDialog(page).innerText(), /^Email a follow-up on PA-/);
    await confirmIfAsked(page);
    await follow().waitFor({ state: "detached" });  // followed up: not due again until the payer's window passes
  }
});

await step("a part-paid pay app is under Unpaid, with what is still owed", async () => {  // R3-02
  const paid = await page.evaluate(async () => {
    const st = await (await fetch("/api/state")).json();
    for (const j of st.jobs) {
      const inv = j.invoices.find((i) => i.status === "sent");
      if (!inv) continue;
      const res = await fetch(`/api/jobs/${j.id}/match-payment`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ amount_cents: 100, reference: "SMOKE-PART", invoice_id: inv.id, received_on: "" }) });
      if (res.ok) return { number: inv.number, owed: inv.amount_cents - 100 };
    }
    return null;
  });
  assert.ok(paid, "a sent pay app to part-pay");
  await page.goto(BASE + "/money");
  await page.locator('[data-action="payapp-filter"][data-filter="sent"]', { hasText: "Unpaid" }).click();
  const row = page.locator("tr", { hasText: paid.number });
  await row.locator(".pill", { hasText: "Partly paid" }).waitFor();
  const owed = (paid.owed / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  assert.match(await row.innerText(), new RegExp(`\\$${owed.replace(/[.]/g, "\\.")} of \\$`));
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
