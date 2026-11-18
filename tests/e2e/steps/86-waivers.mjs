// Browser steps for Round 2's Waivers stream (Stephen, 10 Nov, decisions 1, 5 and 6): a part
// payment owes a waiver for what came in, the final waiver types wait for all the money, Final pay
// app with an earlier pay app unpaid takes a reason shown on the final pay app, the Client playbook
// setting, and the Georgia form hidden while the server has it off. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

// A request from inside the signed-in page: { status, data }.
async function api(p, path, body) {
  return p.evaluate(async ([path, body]) => {
    const res = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = null;
    try { data = await res.json(); } catch { data = null; }
    return { status: res.status, data };
  }, [path, body]);
}

// A Georgia job of its own with PA-01 sent for $4,000 and $1,500 of it paid.
async function partPaidJob(p) {
  const made = await api(p, "/api/jobs", { name: "Peach clinic R2", address: "12 Peach St, Atlanta, GA 30303", customer: "Peachline Builders R2",
    approver: "Mara Lee", ap_email: "ap@peachline.example", phone: "(404) 555-0142", contract_cents: 1_000_000, retainage_pct: 0, role: "GC", client_type: "gc", new_customer: true, approver_confirmed: true });
  assert.equal(made.status, 200, JSON.stringify(made.data));
  const job = made.data.jobs.find((j) => j.name === "Peach clinic R2");
  const line = job.sov_view && job.sov_view.lines && job.sov_view.lines[0];
  const progress = line
    ? await api(p, `/api/jobs/${job.id}/sov-progress`, { done: { [line.id]: "0.4" }, notes: "", period_to: "" })
    : await api(p, `/api/jobs/${job.id}/progress`, { completed_cents: 400_000, notes: "", period_to: "" });
  assert.equal(progress.status, 200, JSON.stringify(progress.data));
  let view = (await api(p, `/api/jobs/${job.id}`)).data.job;
  const draft = view.invoices.find((i) => i.status === "draft" || i.status === "awaiting_approval");
  for (const item of draft.packet || []) {
    if (item.required && !item.present) assert.equal((await api(p, `/api/jobs/${job.id}/resolve/${item.id}`, { reason: "Browser test" })).status, 200);
  }
  view = (await api(p, `/api/jobs/${job.id}`)).data.job;
  const ready = view.invoices.find((i) => i.id === draft.id);
  // The owner signs it first: nothing goes unapproved (Stephen, 18 Nov).
  const signed = await api(p, `/api/jobs/${job.id}/payapp-approve`, { invoice_id: ready.id, stamp: ready.approval.stamp, signature: "Smoke Signer" });
  assert.equal(signed.status, 200, JSON.stringify(signed.data));
  const send = await api(p, `/api/jobs/${job.id}/send`, { reviewed: (ready.email_preview || {}).stamp || "" });
  assert.equal(send.status, 200, JSON.stringify(send.data));
  const paid = await api(p, `/api/jobs/${job.id}/match-payment`, { amount_cents: 150_000, reference: "R2-PART-1", invoice_id: draft.id });
  assert.equal(paid.status, 200, JSON.stringify(paid.data));
  return { jobId: job.id, payerId: job.payer_id, invId: draft.id, number: draft.number };
}

export default async function ({ BASE, step, signedInPage, confirmDialog }) {
  let made = null;

  await step("A part payment owes a waiver for what came in: the Dashboard row and the waiver page say so, and no final waiver can be picked", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    made = await partPaidJob(p);
    // The To do row says it is a part payment (the demo's unsent email ranks first on the page).
    const rows = (await api(p, "/api/state")).data.today_rows.filter((r) => r.job_id === made.jobId && r.kind === "waiver");
    assert.equal(rows.length, 1);
    assert.match(rows[0].text, /^Part payment received: the client is waiting for the lien waiver\./);
    await p.goto(`${BASE}/dashboard`);
    await p.locator('[data-action="filter"][data-filter="waiver"]').click();
    await p.locator("table.dash-jobs", { hasText: "Peach clinic R2" }).waitFor();
    await p.goto(`${BASE}/money/waiver/${made.jobId}`);
    const row = p.locator(".waiver-row", { hasText: `${made.number}, part payment` });
    await row.waitFor();
    assert.match(await row.innerText(), /\$1,500\.00/);
    const select = p.locator(`#waiver-kind-${made.invId}`);
    assert.equal(await select.inputValue(), "unconditional_progress");
    assert.equal(await select.locator('option[value="unconditional_final"]').isDisabled(), true);
    assert.equal(await select.locator('option[value="conditional_final"]').isDisabled(), true);
    // The Georgia form stays hidden while the server has it off (decision 1).
    assert.equal(await p.locator("[data-filled-form]").count(), 0);
    assert.equal((await api(p, `/api/jobs/${made.jobId}/waiver-form/interim.pdf?for=${made.invId}`)).status, 404);
    assert.equal((await api(p, "/api/waiver-forms")).data.states.GA.fillable, false);
    await p.context().close();
  });

  await step("Final pay app with an earlier pay app unpaid asks why, and the final pay app shows who overrode it, when and why", async () => {
    assert.ok(made, "the part-paid job from the step before");
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(`${BASE}/jobs/${made.jobId}`);
    await p.locator('.main-actions [data-form="done"]').click();
    const form = p.locator('[data-form-key="done"]');
    await form.locator(".blocker", { hasText: `${made.number} is not paid yet.` }).waitFor();
    await form.locator('[data-action="done"]').click();
    await p.locator("#done-unpaid-err", { hasText: `Enter why the final pay app goes while ${made.number} is unpaid.` }).waitFor();
    await p.fill("#done-unpaid", "Owner pays both at closeout");
    await form.locator('[data-action="done"]').click();
    await confirmDialog(p).waitFor();
    await confirmDialog(p).locator(".confirm-actions .btn:not(.secondary)").click();
    await form.waitFor({ state: "detached" });
    const job = (await api(p, `/api/jobs/${made.jobId}`)).data.job;
    const final = job.invoices.find((i) => i.final_override);
    assert.ok(final, "the final pay app carries the override");
    assert.equal(final.final_override.reason, "Owner pays both at closeout");
    await p.goto(`${BASE}/money/pay-app/${made.jobId}/${final.id}`);
    const note = p.locator(".final-override");
    await note.waitFor();
    assert.match(await note.innerText(), new RegExp(`^Final pay app with ${made.number} unpaid\\. Overridden by .+ on .+: Owner pays both at closeout$`));
    await p.context().close();
  });

  await step("Client playbook: Lien waiver on each part payment starts Yes and saves No", async () => {
    assert.ok(made, "the client from the first step");
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(`${BASE}/jobs/clients`);
    const card = p.locator(`details[data-payer-card="${made.payerId}"]`);
    await card.locator("summary").first().click();
    await card.locator(`details[data-more="pb-docs-${made.payerId}"] > summary`).click();
    const box = p.locator(`#payer-${made.payerId}-partial_waivers`);
    assert.equal(await box.inputValue(), "yes");
    await box.selectOption("no");
    await card.locator('[data-action="save-playbook"]').click();
    for (let t = 0; t < 50; t++) {
      const payer = (await api(p, "/api/state")).data.payers.find((x) => x.id === made.payerId);
      if (payer && payer.partial_waivers === false) break;
      assert.ok(t < 49, "the client saved No");
      await p.waitForTimeout(100);
    }
    await p.context().close();
  });
}
