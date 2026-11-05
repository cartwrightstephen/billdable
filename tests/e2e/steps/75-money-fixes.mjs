// Browser steps for the money fixes of Nov 5: correcting a field total, taking back Not needed on
// this job, and a stale Review & send landing on the draft that goes now. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ BASE, step, signedInPage, confirmIfAsked }) {
  const newJob = (p, name) => p.evaluate(async (jobName) => {
    const call = async (path, body) => (await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })).json();
    const st = await call("/api/jobs", { name: jobName, customer: `${jobName} client`, contract_cents: 2000000, address: "7 Test St, Atlanta, GA 30307",
      approver: "Dee", phone: "(404) 555-0100", ap_email: "dee@money.example", role: "Owner", new_customer: true });
    const job = st.jobs.find((j) => j.name === jobName);
    await call(`/api/jobs/${job.id}/sov`, { lines: [{ item: "1", description: "Rough", quantity: "100", unit: "ea", rate: "100" }, { item: "2", description: "Panel", quantity: "1", unit: "LS", rate: "10000" }], op_pct: "0" });
    const full = (await (await fetch(`/api/jobs/${job.id}`)).json()).job;
    return { id: job.id, line: full.sov.lines[0].id };
  }, name);

  // The preview's basis goes with the apply, as on the import page.
  const importNinety = (p, id) => p.evaluate(async (jobId) => {
    const call = async (path, body) => (await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
    const { basis } = await call("/api/import/quantities/preview", { job_id: jobId, csv: "1,Rough,90" });
    await call("/api/import/quantities/apply", { job_id: jobId, csv: "1,Rough,90", basis, name: "q.csv" });
  }, id);

  await step("Correct field total takes a wrong quantity back, with a reason", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const { id, line } = await newJob(p, "Smoke correct total");
    await importNinety(p, id);
    await p.goto(`${BASE}/jobs/${id}`);
    await p.locator("#sov-card").waitFor();
    await p.locator(`[data-action="sov-correct-open"][data-line="${line}"]`).click();
    await p.locator('.sov-correct', { hasText: "90 ea" }).waitFor();
    await p.click('[data-action="sov-correct-save"]');
    await p.locator('[data-form-key="sovcorrect"] .field-error').first().waitFor();
    await p.fill("#sov-correct-qty", "9");
    await p.fill("#sov-correct-reason", "Meant 9");
    await p.click('[data-action="sov-correct-save"]');
    await confirmIfAsked(p);
    await p.locator(".sov-correct").waitFor({ state: "detached" });
    const row = await p.evaluate(async ([jobId, lineId]) => (await (await fetch(`/api/jobs/${jobId}`)).json()).job.sov_view.lines.find((l) => l.id === lineId), [id, line]);
    assert.equal(row.done_milli, 9000);
    await p.context().close();
  });

  await step("Undo import takes off what the file added", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const { id, line } = await newJob(p, "Smoke undo import");
    await importNinety(p, id);
    await p.goto(`${BASE}/jobs/${id}`);
    await p.locator('[data-action="quantities-undo"]').first().click();
    await confirmIfAsked(p);
    await p.locator('[data-action="quantities-undo"]').waitFor({ state: "detached" });
    const row = await p.evaluate(async ([jobId, lineId]) => (await (await fetch(`/api/jobs/${jobId}`)).json()).job.sov_view.lines.find((l) => l.id === lineId), [id, line]);
    assert.equal(row.done_milli, 0);
    await p.context().close();
  });

  await step("Needed after all takes back a waiver marked not needed on this job", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const { id, line } = await newJob(p, "Smoke needed after all");
    const inv = await p.evaluate(async ([jobId, lineId]) => {
      const call = async (path, body) => (await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })).json();
      const st = await call(`/api/jobs/${jobId}/sov-progress`, { done: { [lineId]: "10" } });
      const draft = st.jobs.find((j) => j.id === jobId).invoices.at(-1);
      await call(`/api/jobs/${jobId}/packet-skip/${draft.packet.find((i) => i.waiver_kind).id}`, { reason: "mis-tap" });
      return draft.id;
    }, [id, line]);
    await p.goto(`${BASE}/money/pay-app/${id}/${inv}`);
    await p.locator('[data-action="waiver-needed"]').first().click();
    await p.locator('[data-action="waiver-needed"]').waitFor({ state: "detached" });
    const skipped = await p.evaluate(async (jobId) => (await (await fetch(`/api/jobs/${jobId}`)).json()).job.paperwork_view.waiver_skipped, id);
    assert.equal(skipped, null);
    await p.context().close();
  });

  await step("a stale Review & send lands on the draft that goes now, and the message stays", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const { id, line } = await newJob(p, "Smoke stale review");
    const inv = await p.evaluate(async ([jobId, lineId]) => {
      const call = async (path, body) => (await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })).json();
      const st = await call(`/api/jobs/${jobId}/sov-progress`, { done: { [lineId]: "10" } });
      const draft = st.jobs.find((j) => j.id === jobId).invoices.at(-1);
      for (const item of draft.packet) await call(`/api/jobs/${jobId}/resolve/${item.id}`, { reason: "smoke" });
      return draft.id;
    }, [id, line]);
    await p.goto(`${BASE}/money/pay-app/${id}/${inv}`);
    const send = p.locator('.send-check [data-action="send"]');
    await send.waitFor();
    // Another session changes it while this page is open.
    const now = await p.evaluate(async ([jobId, lineId]) => {
      const st = await (await fetch(`/api/jobs/${jobId}/sov-progress`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ done: { [lineId]: "20" } }) })).json();
      return st.jobs.find((j) => j.id === jobId).invoices.at(-1).id;
    }, [id, line]);
    assert.notEqual(now, inv);
    await send.click();
    await p.locator(".review-again").waitFor();
    await p.waitForURL(`**/money/pay-app/${id}/${now}`);
    await new Promise((r) => setTimeout(r, 1500));
    assert.match(await p.locator(".review-again").innerText(), /changed since you opened it/);
    await p.locator('.send-check [data-action="send"]').waitFor();
    assert.equal(await p.getByText("Cancelled").count(), 0);
    await p.context().close();
  });
}
