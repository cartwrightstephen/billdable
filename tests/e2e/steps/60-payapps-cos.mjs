// Browser steps for the payapps-cos: pay apps and change orders stream. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ browser, page, BASE, step, signedInPage, confirmDialog, confirmIfAsked, errors }) {
  await step("New change order on Money > Change orders asks for the job, then the job's form needs a reason", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(`${BASE}/money/change-orders`);
    await p.getByRole("heading", { name: "Change orders" }).waitFor();
    assert.equal(await p.locator('[data-form-key="newco"]').count(), 0, "no form on the list (G10)");
    await p.click('[data-action="co-new"]');
    await p.selectOption("#new-co-job", "job-turner");
    await p.click('[data-action="co-new-open"]');
    await p.locator("#co-title").waitFor();
    assert.equal(new URL(p.url()).pathname, "/jobs/job-turner");
    await p.fill("#co-title", "Extra outlets in the break room");
    await p.click('[data-action="add-co-here"]');
    await p.locator("#co-reason-err", { hasText: "Pick why the scope changed." }).waitFor();
    await p.selectOption("#co-reason", "owner_request");
    await p.click('[data-action="co-add-item"]');
    await p.fill("#co-item-label-0", "Labor");
    await p.fill("#co-item-amt-0", "600");
    await p.click('[data-action="co-add-item"]');
    await p.fill("#co-item-label-1", "Material");
    await p.fill("#co-item-amt-1", "250");
    await p.click('[data-action="add-co-here"]');
    const made = p.locator(".new-co-card");
    await made.waitFor();
    assert.match(await made.innerText(), /\$850\.00/, "the cost lines make the amount");
    await p.goto(`${BASE}/money/change-orders`);
    await p.getByRole("link", { name: /Extra outlets in the break room/ }).click();
    await p.getByRole("heading", { name: "Contract sum" }).waitFor();
    const text = await p.locator("#main").innerText();
    assert.match(text, /Owner request/);
    assert.match(text, /Revised contract sum if signed/);
    assert.match(text, /History[\s\S]*Created/);
    // Withdrawn, so the demo's Turner job is as it was for the steps after this one.
    await p.locator('#main [data-action="withdraw-co"]').click();
    await confirmIfAsked(p);
    await p.locator("#main", { hasText: /History[\s\S]*Withdrawn/ }).waitFor();
    await p.context().close();
  });

  await step("New pay app on Money > Pay apps asks for the job, then opens that job's Progress pay app form", async () => {  // G10
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(`${BASE}/money`);
    await p.getByRole("heading", { name: "Pay apps" }).waitFor();
    await p.click('[data-action="payapp-new"]');
    const options = await p.locator("#new-payapp-job option").evaluateAll((os) => os.map((o) => o.value));
    assert.ok(options.includes("job-grant"), "a job in progress can be billed");
    assert.ok(!options.includes("job-turner"), "a job with nothing to bill isn't listed");
    await p.click('[data-action="payapp-new-open"]');
    await p.locator("#new-payapp-job-err", { hasText: "Pick a job." }).waitFor();
    await p.selectOption("#new-payapp-job", "job-grant");
    await p.click('[data-action="payapp-new-open"]');
    await p.locator('[data-form-key="progress"], [data-form-key="sovprogress"]').first().waitFor();
    assert.equal(new URL(p.url()).pathname, "/jobs/job-grant");
    await p.context().close();
  });

  await step("Schedule of values: job records add up to a suggested value, Progress pay app drafts it, and the amount can be changed", async () => {  // Nov 3
    const ctx = await browser.newContext({ viewport: { width: 375, height: 800 } });
    const p = await ctx.newPage();
    await p.goto(BASE + "/");
    const made = await p.evaluate(async (email) => {
      const post = async (path, body) => { const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); return { status: r.status, data: await r.json() }; };
      await post("/api/auth/signup", { accept_terms: true, email, password: "smoke test password", name: "Sal Schedule", company_name: "Schedule Builders" });
      const job = { name: "Conduit run", address: "7 Line St", customer: "Sam Client", role: "Owner", ap_email: "sam@example.com", contract_cents: 1000000 };
      const lines = [{ description: "Conduit", quantity: "100", unit: "LF", rate: "50" }, { description: "Fixtures", quantity: "10", unit: "EA", rate: "400" }];
      const refused = await post("/api/jobs", { ...job, sov_lines: lines });
      const before = (await (await fetch("/api/state")).json()).jobs.length;
      const lines2 = [lines[0], { ...lines[1], rate: "500" }];
      const st = (await post("/api/jobs", { ...job, sov_lines: lines2 })).data;
      const j = st.jobs.find((x) => x.name === "Conduit run");
      const [conduit] = j.sov.lines;
      for (const qty of ["20", "15"]) await post(`/api/jobs/${j.id}/daily-record`, { notes: "Pulled conduit", sections: { quantities: [{ line_id: conduit.id, qty }] } });
      const after = (await (await fetch("/api/state")).json()).jobs.find((x) => x.id === j.id);
      return { id: j.id, conduit: conduit.id, refused: refused.status, sov: refused.data.fields && refused.data.fields.sov, before, drafts: after.invoices.length };
    }, `sov-${Date.now()}@example.com`);
    assert.equal(made.refused, 409);
    assert.match(made.sov, /\$9,000\.00, \$1,000\.00 less/);
    assert.equal(made.before, 0, "nothing saved when the lines don't add up");
    assert.equal(made.drafts, 0, "a job record makes no draft");
    await p.goto(`${BASE}/jobs/${made.id}`);
    const card = p.locator("#sov-card");
    await card.locator(".sov-period", { hasText: "35 LF" }).waitFor();
    assert.match(await card.locator(".sov-suggested").innerText(), /\$1,750\.00/);
    await p.locator(".next-card .primary-action", { hasText: "Progress pay app" }).click();
    assert.equal(await p.inputValue(`#sovp-done-${made.conduit}`), "35");
    await p.click('[data-action="sov-progress"]');
    await p.locator(`#sova-${made.conduit}`).waitFor();
    assert.equal(await p.inputValue(`#sova-${made.conduit}`), "1750.00");
    await p.fill(`#sova-${made.conduit}`, "1500.25");
    await p.click('#sov-card [data-action="sov-amounts"]');
    await card.locator('[data-action="sov-amounts-reset"]').waitFor();
    await p.reload();
    assert.equal(await p.inputValue(`#sova-${made.conduit}`), "1500.25");
    const state = await p.evaluate(async (id) => (await (await fetch("/api/state")).json()).jobs.find((j) => j.id === id), made.id);
    const draft = state.invoices.filter((i) => i.status !== "void").at(-1);
    assert.equal(draft.gross_cents, 150025);
    const pdf = await p.evaluate(async ([id, inv]) => (await fetch(`/api/jobs/${id}/payapp/${inv}.pdf`)).status, [made.id, draft.id]);
    assert.equal(pdf, 200);
    assert.equal(await p.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, "no sideways scroll at 375 px");
    await ctx.close();
  });

  await step("New job asks for the schedule, and refuses lines that don't add up with nothing saved", async () => {  // Nov 3 Decision 1
    const p = await signedInPage({ width: 375, height: 800 });
    await p.goto(`${BASE}/jobs`);
    await p.click('[data-action="toggle-form"][data-form="newjob"]');
    await p.locator(".nj-sov h3", { hasText: "Schedule of values" }).waitFor();
    assert.match(await p.locator(".nj-sov").innerText(), /one lump-sum line for the contract amount/);
    await p.fill("#nj-sov-desc-0", "Conduit");
    await p.click('[data-action="nj-sov-add"]');
    assert.equal(await p.inputValue("#nj-sov-desc-0"), "Conduit", "what was typed stays when a line is added");
    await p.locator("#nj-sov-desc-3").waitFor();
    assert.equal(await p.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), true, "no sideways scroll at 375 px");
    await p.context().close();
  });

  await step("New pay app with one job marked done drafts it, as the job page's Draft pay app does", async () => {  // G10
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    const jobId = await p.evaluate(async (email) => {
      const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
      await post("/api/auth/signup", { accept_terms: true, email, password: "smoke test password", name: "Pam Payapp", company_name: "Payapp Builders" });
      const st = await (await post("/api/jobs", { name: "Spruce porch", address: "6 Spruce St", customer: "Lee Homeowner", role: "Owner",
        ap_email: "lee@example.com", contract_cents: 800000 })).json();
      const id = st.jobs.find((j) => j.name === "Spruce porch").id;
      await post(`/api/jobs/${id}/done`, {});
      await post(`/api/jobs/${id}/void-draft`, {});
      return id;
    }, `payapp-new-${Date.now()}@example.com`);
    await p.goto(`${BASE}/money`);
    await p.click('[data-action="payapp-new"]');
    await p.locator(`[data-action="open-payapp"][data-job="${jobId}"]`).first().waitFor();
    assert.equal(new URL(p.url()).pathname, `/jobs/${jobId}`);
    const invoices = await p.evaluate(async (id) => (await (await fetch("/api/state")).json()).jobs.find((j) => j.id === id).invoices.map((i) => i.status), jobId);
    assert.deepEqual(invoices.filter((s) => s === "draft" || s === "awaiting_approval").length, 1, "one draft");
    await ctx.close();
  });

  await step("a pay app's page shows its summary, as the PDF prints it", async () => {
    await page.goto(`${BASE}/money/pay-app/job-turner/inv-trn-1`);
    const card = page.locator(".payapp-summary");
    await card.waitFor();
    const text = await card.innerText();
    for (const label of ["Contract sum", "Completed and stored to date", "Retainage held", "Current amount due", "Balance to finish, including retainage", "Prior payments received"]) {
      assert.ok(text.includes(label), label);
    }
  });

  await step("Money > Unpaid has Recent contact under each job, closed until opened", async () => {
    await page.goto(`${BASE}/money/collections`);
    const contact = page.locator("details.recent-contact").first();
    await contact.waitFor();
    assert.match(await contact.locator("summary").innerText(), /^Recent contact \(\d+\)$/);
    await contact.locator("summary").click();
    await contact.getByRole("link", { name: "See all on the job" }).waitFor();
  });
}
