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
