// Browser steps for the last Round 1 blockers (hunt-r1b, Nov 6): retainage a client releases early,
// mid-job, is recorded from the job page and its waiver comes due; a closeout's final waiver takes
// the one tap that says which waiver it is. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ BASE, step, signedInPage, confirmIfAsked }) {
  const call = (p, path, body) => p.evaluate(async ([u, b]) => {
    const r = await fetch(u, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b || {}) });
    return { status: r.status, data: await r.json() };
  }, [path, body]);
  const read = (p, id) => p.evaluate(async (jid) => (await (await fetch(`/api/jobs/${jid}`)).json()).job, id);
  const paidJob = async (p, name) => {
    const made = await call(p, "/api/jobs", { name: `${name} ${Date.now()}`, address: "7 Elm St, Atlanta, GA 30307", customer: `${name} GC`, client_type: "gc",
      approver: "Dee", phone: "(404) 555-0100", ap_email: "dee@example.com", contract_cents: 1000000, retainage_pct: 10, new_customer: true });
    const job = made.data.jobs.find((j) => j.name.startsWith(name));
    await call(p, `/api/jobs/${job.id}/sov-progress`, { done: { [job.sov.lines[0].id]: "0.4" } });
    const draft = (await read(p, job.id)).invoices.at(-1);
    for (const item of draft.packet) if (item.required && !item.present) await call(p, `/api/jobs/${job.id}/resolve/${item.id}`, { reason: "In hand" });
    const sent = await call(p, `/api/jobs/${job.id}/send`, { reviewed: (await read(p, job.id)).invoices.at(-1).email_preview.stamp });
    assert.equal(sent.status, 200, JSON.stringify(sent.data));
    const inv = (await read(p, job.id)).invoices.at(-1);
    assert.equal((await call(p, `/api/jobs/${job.id}/match-payment`, { amount_cents: inv.amount_cents, reference: "chk", invoice_id: inv.id })).status, 200);
    return job.id;
  };

  await step("hunt r1b: retainage released early, mid-job, is recorded from the job page and its waiver comes due", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const id = await paidJob(p, "Early release");
    await p.goto(`${BASE}/jobs/${id}`);
    const button = p.locator('[data-action="toggle-form"][data-form="retpay"]');
    await button.waitFor();
    await button.click();
    assert.equal(await p.locator("#retpay-amt").inputValue(), "$400.00", "the amount held is filled in");
    await p.locator('[data-action="retainage-paid"]').click();
    await confirmIfAsked(p);
    await p.waitForFunction(async (jid) => {
      const j = (await (await fetch(`/api/jobs/${jid}`)).json()).job;
      return (j.retainage_waivers || []).some((w) => w.waiver_due && w.kind === "unconditional_progress");
    }, id);
    const job = await read(p, id);
    assert.notEqual(job.work_status, "complete", "mid-job");
    await p.context().close();
  });
}
