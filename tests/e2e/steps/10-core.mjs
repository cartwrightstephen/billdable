// The core walk through the demo company (moved from smoke.mjs as it was): sign-in, Today, a job,
// the money path from blocked to closed, the phone layout. The steps share one page and run in order.
import assert from "node:assert/strict";

export default async function ({ browser, page, BASE, step, signedInPage, confirmDialog, confirmIfAsked, errors }) {
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

  await step("the Dashboard lists what needs you and opens a job by its address", async () => {
    await page.goto(BASE + "/dashboard");
    await page.getByRole("heading", { name: "Overview" }).waitFor();
    await page.getByRole("heading", { name: /To Do/ }).waitFor();
    await page.locator(".action-item", { hasText: "Oak Street kitchen" }).getByRole("button", { name: "Open" }).click();
    await page.getByRole("heading", { name: "Oak Street kitchen" }).waitFor();
    assert.equal(new URL(page.url()).pathname, "/jobs/job-oak");
    assert.match(await page.title(), /Oak Street kitchen/);
  });

  await step("the job page has one main action, and Back returns to the Dashboard", async () => {
    const primary = page.locator(".next-card .primary-action");
    assert.equal(await primary.count(), 1);
    // Tabs and job-page buttons carry no tooltips; each button says why on its second line (R3-04).
    assert.equal(await page.locator(".tabs .tab[title], .next-card .btn[title], .more-actions-rest .btn[title]").count(), 0);
    assert.match(await primary.innerText(), /Fix 3 things before sending/);  // paperwork and a CO: R6-01 wording
    await page.goBack();
    await page.getByRole("heading", { name: "Overview" }).waitFor();
    assert.equal(new URL(page.url()).pathname, "/dashboard");
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
    assert.equal(new URL(p.url()).pathname, "/settings/company");  // Settings split into pages (R6-03)
    await ctx.close();
  });

  await step("the landing opens a sample company without an account", async () => {  // R2-06
    const ctx = await browser.newContext();
    const p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    await p.locator("#land-demo", { hasText: "Look around a sample company" }).click();
    await p.getByRole("heading", { name: "Overview" }).waitFor();
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
    await p.goto(BASE + "/dashboard");
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
    await p.goto(BASE + "/jobs/daily");
    await p.locator("#daily-job").waitFor();
    assert.equal(await p.inputValue("#daily-job"), jobId);
    // No company address, phone or licence yet: Before you send says so; once the address is filled
    // in, the notice names only the phone and licence number (a warning: Approve & send stays on).
    const invId = await p.evaluate(async (id) => {
      const st = await (await fetch(`/api/jobs/${id}/done`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ notes: "" }) })).json();
      return st.jobs.find((j) => j.id === id).invoices[0].id;
    }, jobId);
    await p.goto(`${BASE}/money/pay-app/${jobId}/${invId}`);
    const warn = p.locator(".send-check .no-address");
    await warn.waitFor();
    assert.match(await warn.innerText(), /Your company address, phone and licence number are blank on this PDF/);
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
    assert.match(await p.locator(".send-check .company-gaps").innerText(), /Your company phone and licence number are blank on this PDF/);
    assert.equal(await p.locator('.send-check .company-gaps [data-focus="#co-phone"]').count(), 1);
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
    await p.getByRole("heading", { name: /^Paperwork for/ }).waitFor();
    const missing = p.locator(".packet-item.absent").first();
    await missing.getByRole("button", { name: "Take a photo" }).waitFor({ state: "visible" });
    assert.equal(await missing.locator('[data-action="pick-files"][data-capture="1"][data-attach]').count(), 1);
    assert.equal(await missing.locator("select option").first().innerText(), "Choose a record…");
    await ctx.close();
  });

  await step("Inbox: a suggested item is added with one tap, and a photo goes with the job picked first", async () => {  // R5-05, R5-07
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    const ids = await p.evaluate(async (email) => {
      const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      await post("/api/auth/signup", { accept_terms: true, email, password: "smoke test password", name: "Ina Box", company_name: "Inbox Builders" });
      await post("/api/jobs", { name: "Maple porch", address: "8 Maple Ave", customer: "Ray Homeowner", role: "Owner", ap_email: "ray@example.com", contract_cents: 900000 });
      const st = await (await post("/api/jobs", { name: "Cedar bath", address: "3 Cedar Ln", customer: "Sue Homeowner", role: "Owner",
        ap_email: "sue@example.com", contract_cents: 1500000 })).json();
      await post("/api/import/text", { text: "Railing posts set on the Maple porch today.", source: "paste" });
      return Object.fromEntries(st.jobs.map((j) => [j.name, j.id]));
    }, `inbox-${Date.now()}@example.com`);
    await p.goto(BASE + "/communications");
    // One button for the suggested job; the job and kind selects wait behind "Other job".
    const card = p.locator(".inbox-item", { hasText: "Railing posts set" });
    await card.getByRole("button", { name: "Add to Maple porch" }).waitFor();
    assert.equal(await card.locator("select").count(), 0, "no selects until Other job");
    await card.getByRole("button", { name: "Add to Maple porch" }).click();
    await card.waitFor({ state: "detached" });
    const filed = await p.evaluate(async () => (await (await fetch("/api/state")).json()).inbox.find((i) => i.text.startsWith("Railing posts")));
    assert.equal(filed.status, "filed");
    assert.equal(filed.filed_job_id, ids["Maple porch"]);
    // Which job? first: the upload is suggested to that job, not filed.
    await p.selectOption("#upload-job", ids["Cedar bath"]);
    const chooser = p.waitForEvent("filechooser");
    await p.locator('.dropzone .pointer-only [data-action="pick-files"]').click();  // desktop: the "choose files" link (R6-04)
    await (await chooser).setFiles({ name: "IMG_0412.jpg", mimeType: "image/jpeg", buffer: Buffer.from("not really a jpeg") });
    const photo = p.locator(".inbox-item", { hasText: "IMG_0412.jpg" });
    await photo.getByRole("button", { name: "Add to Cedar bath" }).waitFor();
    assert.match(await photo.innerText(), /Suggested because it was uploaded for this job/);
    await ctx.close();
  });

  await step("a member sees no provider addresses; the owner does, with Make a new address; the member sees the trial on Billing", async () => {  // R5-09, R6-07, R6-10
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    const token = await p.evaluate(async (email) => {
      const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      await post("/api/auth/signup", { accept_terms: true, email, password: "smoke test password", name: "Owen Owner", company_name: "Address Builders" });
      const out = await (await post("/api/team/invite", { email: "m-" + email, role: "member" })).json();
      return out.invite_path.split("#invite=")[1];
    }, `owner-${Date.now()}@example.com`);
    await p.goto(BASE + "/communications");
    const automatic = p.locator("details.card", { hasText: "Bring emails and texts in automatically" });
    await automatic.locator("summary").click();
    await automatic.getByRole("button", { name: "Make a new address" }).first().waitFor();
    // Accepting the invite signs this browser in as the member.
    const status = await p.evaluate(async (t) => (await fetch("/api/auth/accept-invite", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accept_terms: true, token: t, name: "Mia Member", password: "member password 1" }) })).status, token);
    assert.equal(status, 200, "member joined");
    await p.goto(BASE + "/communications");
    await automatic.locator("summary").click();
    await automatic.getByText("An owner connects the email service and the texting number.").waitFor();
    assert.equal(await automatic.getByRole("button", { name: "Make a new address" }).count(), 0, "no Make a new address for a member");
    assert.equal(await automatic.locator("#email-webhook, #sms-webhook").count(), 0, "the web addresses providers post to are an owner's");
    // A member sees the trial in Settings too, on Billing, without the owner's buttons (R6-10).
    await p.goto(BASE + "/settings/billing");
    await p.locator("#view .trial-banner", { hasText: "Free trial" }).waitFor();
    await p.getByText("An owner of this company manages the subscription.").waitFor();
    assert.equal(await p.locator('#view [data-action="billing-checkout"], #view [data-action="billing-portal"]').count(), 0, "no Subscribe for a member");
    await ctx.close();
  });

  await step("refresh keeps the page", async () => {
    await page.goto(BASE + "/money/collections");
    await page.reload();
    await page.getByRole("heading", { name: "Unpaid" }).waitFor();
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

  await step("a sent pay app with nothing paid can be pulled back, with a reason first", async () => {
    // PA-TRN-01 went out and nothing is recorded on it (R4-03). Nothing is sent here: Cancel.
    await page.goto(BASE + "/money/pay-app/job-turner/inv-trn-1");
    await page.getByRole("heading", { name: "PA-TRN-01" }).waitFor();
    await page.getByRole("button", { name: "Pull back this pay app…" }).click();
    await page.locator("#voidsent-reason-inv-trn-1").waitFor();
    await page.click('[data-action="void-sent"][data-inv="inv-trn-1"]');
    await page.locator('[data-form-key="voidsent-inv-trn-1"] .field-error', { hasText: "Enter why you are pulling it back" }).waitFor();
    await page.locator('[data-form-key="voidsent-inv-trn-1"]').getByRole("button", { name: "Cancel" }).click();
    await page.getByRole("button", { name: "Pull back this pay app…" }).waitFor();
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
      // Paperwork alone reads "Add N missing documents" (R1-08); with an unsigned CO too, "Fix N things before sending" (R6-01).
      if (/^(Fix \d+ things? before sending|Add \d+ missing document)/.test(label)) {
        await main.click();
        await page.getByRole("heading", { name: /^Paperwork( for |$)/ }).waitFor();  // "Paperwork" alone when no pay app is drafted yet
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
        await page.goto(BASE + "/dashboard");
        await page.locator(".action-item", { hasText: "Inman Park bath" }).getByRole("button", { name: "Review & send" }).click();
        await page.locator(".send-check").waitFor({ state: "visible" });
        assert.match(new URL(page.url()).pathname, /^\/money\/pay-app\//);
        await page.click('.send-check [data-action="send"]');
        await page.locator('.send-check [data-action="send"]').waitFor({ state: "detached" });
        // The demo never emails, so Today lists the pay app's email as one that didn't go out (R1-11).
        await page.goto(BASE + "/dashboard");
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
        assert.match(await card.innerText(), /CO \d+ is not signed|change order/i);  // R3-08 lists blockers as "CO N is not signed yet."
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
    await page.getByRole("heading", { name: "Activity log" }).waitFor();
    await page.locator("#view table").waitFor();
    // Only a broken chain is said (S14); an intact one shows nothing.
    assert.equal(await page.locator("#view .blocker", { hasText: "chain of records is broken" }).count(), 0);
    assert.equal(await page.getByText("checks out").count(), 0);
  });

  await step("Today's queue: one main button per row, and a follow-up names who it goes to first", async () => {
    await page.goto(BASE + "/dashboard");
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
    assert.match(await page.locator(".job-history .timeline li").first().innerText(), /^Job finished/);  // a sentence, not the action code (R6-01)
  });

  await step("a confirmation is an in-page dialog, and Escape cancels it", async () => {
    await page.goto(BASE + "/settings/company");
    await page.locator("details.danger-zone > summary").click();  // folded at the end of Company (R6-03)
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
    for (const path of ["/dashboard", "/dashboard/results", "/dashboard/calendar", "/jobs", "/jobs/daily", "/jobs/history", "/jobs/clients", "/jobs/job-oak",
                        "/communications", "/communications/outbox", "/data/import", "/data/export", "/money", "/money/packets",
                        "/money/change-orders", "/money/collections", "/money/payments", "/money/waivers", "/money/retainage",
                        "/automations", "/settings", "/settings/company", "/settings/team", "/settings/account", "/settings/billing",
                        "/settings/audit"]) {
      await phone.goto(BASE + path);
      await phone.locator("#view h1").first().waitFor();
      const width = await phone.evaluate(() => document.documentElement.scrollWidth);
      if (width > 375) wide.push(`${path} (${width}px)`);
    }
    assert.deepEqual(wide, [], "pages wider than the screen");
  });

  await step("Money has five tabs, Pay apps shows the paperwork, and Settings opens on Company", async () => {  // R6-03, Oct 29 review
    await page.goto(BASE + "/money");
    await page.locator(".tabs .tab", { hasText: "Closeout" }).waitFor();  // allInnerTexts doesn't wait for the page to draw
    assert.deepEqual(await page.locator(".tabs .tab").allInnerTexts(), ["Pay apps", "Change orders", "Unpaid", "Payments", "Closeout"]);
    await page.locator("table th", { hasText: "Paperwork" }).waitFor();
    await page.goto(BASE + "/money/packets");  // the old Paperwork tab's address opens Pay apps
    await page.getByRole("heading", { name: "Pay apps" }).waitFor();
    await page.goto(BASE + "/settings/company");
    assert.equal(await page.locator("#view .card").first().getAttribute("id"), "company-card");
    assert.deepEqual((await page.locator(".tabs .tab").allInnerTexts()).slice(0, 4), ["Company", "Team", "My account", "Billing"]);
    await page.goto(BASE + "/settings");
    await page.getByRole("heading", { name: "Company", exact: true }).waitFor();
    assert.equal(new URL(page.url()).pathname, "/settings/company");
  });

  await step("phone: a schedule of values is one card per line, and the Inbox offers Take a photo first", async () => {  // R6-02, R6-04
    const ctx = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    const p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    const jobId = await p.evaluate(async (email) => {
      const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      await post("/api/auth/signup", { accept_terms: true, email, password: "smoke test password", name: "Pia Phone", company_name: "Phone Builders" });
      const st = await (await post("/api/jobs", { name: "Long schedule job", address: "4 Line St", customer: "Gail General", role: "GC",
        ap_email: "ap@gail.example", contract_cents: 3150000 })).json();
      const id = st.jobs.find((j) => j.name === "Long schedule job").id;
      await post(`/api/jobs/${id}/sov`, { op_pct: "5", update_contract: true, lines: [
        { item: "1", description: "Rough-in wiring for the whole second floor east wing", quantity: "1", unit: "LS", rate: "12000" },
        { item: "2", description: "Ceramic wall tile in the restrooms", quantity: "1200", unit: "SF", rate: "15" },
      ] });
      return id;
    }, `phone-${Date.now()}@example.com`);
    const noSideways = async (where) => {
      const width = await p.evaluate(() => document.documentElement.scrollWidth);
      assert.ok(width <= 375, `${where} is ${width}px wide`);
    };
    await p.goto(BASE + `/jobs/${jobId}`);
    await p.locator("#sov-card .sov-table").waitFor();
    await noSideways("the job page with a schedule of values");
    await p.locator('#sov-card [data-action="sov-start"]').click();
    await p.locator("#sov-card .sov-edit").waitFor();
    await noSideways("the schedule editor");
    await p.goto(BASE + "/communications");
    // Waiting first, then the camera; the drop-and-paste sentence is for a mouse.
    assert.equal(await p.locator("#view .card").first().locator("h2").innerText(), "Waiting (0)");
    const first = p.locator('.dropzone [data-action="pick-files"]:visible').first();
    assert.equal(await first.innerText(), "Take a photo");
    assert.equal(await first.getAttribute("data-capture"), "1");
    await p.locator(".dropzone").getByRole("button", { name: "Choose files" }).waitFor({ state: "visible" });
    assert.equal(await p.locator(".dropzone .pointer-only").isVisible(), false);
    await ctx.close();
  });

  await step("phone: the menu drawer opens and moves between sections", async () => {
    const phone = await signedInPage({ width: 375, height: 812 });
    await phone.goto(BASE + "/dashboard");
    await phone.click("#menu-btn");
    await phone.locator(".shell.drawer-open").waitFor();
    await phone.locator('.nav button[data-section="money"]').click();
    await phone.getByRole("heading", { name: "Pay apps" }).waitFor();
    assert.equal(new URL(phone.url()).pathname, "/money");
  });

  await step("a company that bills in draws reads Draw, not Pay app", async () => {  // R6-01
    const ctx = await browser.newContext();
    const p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    await p.evaluate(async (email) => {
      const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      await post("/api/auth/signup", { accept_terms: true, email, password: "smoke test password", name: "Dee Draws", company_name: "Draw Builders" });
      await post("/api/company", { billing_style: "draws" });
      await post("/api/jobs", { name: "Hill house", address: "9 Hill Rd", customer: "Pat Homeowner", role: "Owner", ap_email: "pat@example.com", contract_cents: 9000000 });
    }, `draws-${Date.now()}@example.com`);
    await p.goto(BASE + "/money");
    await p.locator(".tabs .tab", { hasText: "Draws" }).waitFor();
    assert.equal(await p.locator(".tabs .tab", { hasText: "Pay apps" }).count(), 0);
    await p.getByRole("heading", { name: "Draws" }).waitFor();
    await p.goto(BASE + "/jobs");
    await p.locator("tr", { hasText: "Hill house" }).locator(".next-action").waitFor();
    assert.doesNotMatch(await p.locator("tr", { hasText: "Hill house" }).innerText(), /pay app/i);
    await ctx.close();
  });

  await step("unknown paths get the HTML 404 page", async () => {
    const res = await page.goto(BASE + "/no-such-page");
    assert.equal(res.status(), 404);
    await page.getByText("Go to Duracash").waitFor();
  });

}
