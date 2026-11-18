// Browser steps for Stephen's Round 2 job rules (10 Nov 2026): a change order saved without a price
// (decision 2), Finish job with closeout documents missing (decision 4) and named pay app approvers
// (decision 8). They sign up a company of their own, so the demo's data stays as the other steps
// expect it. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ browser, BASE, step, confirmIfAsked, errors }) {
  let ctx, p;
  async function company() {
    if (p) return;
    ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    await p.evaluate(async (email) => {
      await fetch("/api/auth/signup", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accept_terms: true, email, password: "smoke test password", name: "Rae Rules", company_name: "Rules Builders" }) });
    }, `rules-${Date.now()}@example.com`);
  }
  // A job of its own for each step, set up through the API: returns its id.
  const newJob = (name) => p.evaluate(async (name) => {
    const r = await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      phone: "(404) 555-0100", approver_confirmed: true, approver: "Kim Homeowner", name, address: "3 Maple St", customer: "Kim Homeowner", role: "Owner",
      ap_email: "kim@example.com", contract_cents: 900000, retainage_pct: 0 }) });
    const st = await r.json();
    return st.jobs.find(j => j.name === name).id;
  }, name);
  const api = (path, body = {}) => p.evaluate(async ([path, body]) => {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return [r.status, await r.json()];
  }, [path, body]);
  // Every supporting document of the open draft marked in hand, then the state.
  async function clearDraft(jobId) {
    let [, st] = await api(`/api/jobs/${jobId}/note-contact`, { text: "Checking" });
    const draft = st.jobs.find(j => j.id === jobId).invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
    for (const item of draft.packet || []) [, st] = await api(`/api/jobs/${jobId}/resolve/${item.id}`, { reason: "In hand" });
    return st.jobs.find(j => j.id === jobId).invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
  }

  await step("A change order saved with no price is a Dashboard row and a job task until it is priced (Round 2 decision 2)", async () => {
    await company();
    const jobId = await newJob("Maple deck");
    await p.goto(`${BASE}/jobs/${jobId}`);
    await p.click('[data-action="new-co"]');
    await p.fill("#co-title", "Extra gate");
    await p.selectOption("#co-reason", "owner_request");
    await p.click('[data-action="add-co-here"]');
    await p.locator(".new-co-card", { hasText: "No price yet" }).waitFor();
    assert.equal(await p.locator('.new-co-card [data-action="request-co-signature"]').count(), 0, "no signature without a price");
    await p.goto(`${BASE}/dashboard`);
    const row = p.locator(".todo-table .action-item", { hasText: "CO 1 Extra gate needs a price" });
    await row.waitFor();
    await row.getByRole("button", { name: "Add the price to CO 1" }).click();
    await p.waitForURL(/\/money\/change-order\//);
    await p.locator(".co-price input").fill("1,250");
    await p.click('[data-action="co-price"]');
    await p.locator("h1 + .sub", { hasText: "$1,250.00" }).waitFor();
    await p.locator('[data-action="request-co-signature"]').waitFor();
    await p.goto(`${BASE}/dashboard`);
    await p.locator(".todo-table").or(p.locator(".action-queue")).first().waitFor();
    assert.equal(await p.locator(".todo-table .action-item", { hasText: "needs a price" }).count(), 0);
  });

  await step("Finish job with closeout documents missing asks for the reason, then shows the override on the job file (decision 4)", async () => {
    await company();
    const jobId = await newJob("Oak porch");
    let [status, st] = await api(`/api/jobs/${jobId}/done`, { notes: "" });
    assert.equal(status, 200, JSON.stringify(st));
    const draft = await clearDraft(jobId);
    [, st] = await api(`/api/jobs/${jobId}/note-contact`, { text: "Ready" });
    const ready = st.jobs.find(j => j.id === jobId).invoices.find(i => i.id === draft.id);
    const stamp = ready.email_preview.stamp;
    // The owner signs it first: nothing goes unapproved (Stephen, 18 Nov).
    [status, st] = await api(`/api/jobs/${jobId}/payapp-approve`, { invoice_id: ready.id, stamp: ready.approval.stamp, signature: "Rae Rules" });
    assert.equal(status, 200, JSON.stringify(st));
    [status, st] = await api(`/api/jobs/${jobId}/send`, { reviewed: stamp });
    assert.equal(status, 200, JSON.stringify(st));
    [status, st] = await api(`/api/jobs/${jobId}/match-payment`, { amount_cents: draft.amount_cents, reference: "CHK-1", invoice_id: draft.id });
    assert.equal(status, 200, JSON.stringify(st));
    [status, st] = await api(`/api/jobs/${jobId}/waiver-not-needed`, { reason: "Handed over in person" });
    assert.equal(status, 200, JSON.stringify(st));
    await p.goto(`${BASE}/jobs/${jobId}`);
    await p.locator('[data-action="close"]').first().click();
    const form = p.locator(".close-override");
    await form.locator(".blocker", { hasText: "Missing:" }).waitFor();
    await form.locator('[data-action="close-override"]').click();
    await form.locator("#close-override-reason-err", { hasText: "Enter why the job can finish without them." }).waitFor();
    await p.fill("#close-override-reason", "Owner keeps the closeout binder");
    await form.locator('[data-action="close-override"]').click();
    const note = p.locator(".closeout-override");
    await note.waitFor();
    assert.match(await note.innerText(), /^Finished without .*\. Override by Rae Rules, .*: Owner keeps the closeout binder$/);
  });

  await step("Named approvers: Review & send lists who hasn't signed, Sign, then it sends (decision 8; signing, 18 Nov)", async () => {
    await company();
    const me = await p.evaluate(async () => (await (await fetch("/api/auth/me")).json()).user);
    const jobId = await newJob("Elm fence");
    await p.goto(`${BASE}/settings/company`);
    const card = p.locator("#approvers-card");
    await card.locator(`[data-approver="company"][data-user="${me.id}"]`).check();
    await card.locator('[data-action="approvers-save"]').click();
    await p.waitForFunction(() => document.querySelector("#approvers-card input:checked"));
    let [status, st] = await api(`/api/jobs/${jobId}/done`, { notes: "" });
    assert.equal(status, 200, JSON.stringify(st));
    const draft = await clearDraft(jobId);
    await p.goto(`${BASE}/money/pay-app/${jobId}/${draft.id}`);
    const approvals = p.locator(".payapp-approvals");
    await approvals.locator(".money-row", { hasText: "Rae Rules" }).locator(".pill", { hasText: "Waiting" }).waitFor();
    assert.equal(await p.locator('[data-action="send"][data-blocked="1"]').count(), 1);
    await approvals.locator('input[name="signature"]').fill("Rae Rules");
    await approvals.locator('[data-action="payapp-approve"]').click();
    await approvals.locator(".money-row", { hasText: "Signed /s/ Rae Rules" }).waitFor();
    await p.click('[data-action="send"]');
    await confirmIfAsked(p);
    await p.locator("h1 + .sub .pill", { hasText: "Sent" }).waitFor();
    [status] = await api("/api/approvers", { user_ids: [] });
    assert.equal(status, 200);
    await ctx.close();
  });
}
