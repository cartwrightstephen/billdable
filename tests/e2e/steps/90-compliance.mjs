// Browser steps for Jobs, Compliance (Siteline comparison, items 7 and 8): a document added with
// its file, what a client needs, a Georgia job's lien deadlines with the not-legal-advice note,
// and the soft gate on the pay app page. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

async function api(p, path, body) {
  return p.evaluate(async ([path, body]) => {
    const res = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = null;
    try { data = await res.json(); } catch { data = null; }
    return { status: res.status, data };
  }, [path, body]);
}
const ok = (r) => assert.equal(r.status, 200, JSON.stringify(r.data));

export default async function ({ BASE, step, signedInPage }) {
  await step("Compliance: add a certificate of insurance with its file from the tab", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(`${BASE}/jobs`);
    await p.click('.tabs [data-tab="compliance"]');
    await p.waitForURL(/\/jobs\/compliance$/);
    await p.locator("#compliance-docs").waitFor();
    assert.equal(await p.locator("#view h1").first().innerText(), "Compliance", "the heading is the tab's name");
    assert.match(await p.locator(".compliance-legal").innerText(), /Not legal advice/);
    await p.locator('[data-more="cdoc-add"] > summary').click();
    await p.selectOption("#cdoc-type", "coi");
    await p.fill("#cdoc-issuer", "Peachtree Mutual 90");
    await p.fill("#cdoc-expires", "2030-06-30");
    await p.setInputFiles("#cdoc-file", { name: "coi.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 coi") });
    await p.click('[data-action="compliance-add-doc"]');
    const row = p.locator("#compliance-docs tr", { hasText: "Peachtree Mutual 90" });
    await row.waitFor();
    assert.match(await row.innerText(), /Current/);
    await row.getByText("Open file").waitFor();
    // A tax ID is refused under its box.
    await p.locator('[data-more="cdoc-add"] > summary').click().catch(() => {});
    if (!(await p.locator("#cdoc-number").isVisible())) await p.locator('[data-more="cdoc-add"] > summary').click();
    await p.selectOption("#cdoc-type", "license");
    await p.fill("#cdoc-number", "123-45-6789");
    await p.click('[data-action="compliance-add-doc"]');
    await p.locator("#cdoc-number-err", { hasText: "Social Security" }).waitFor();
    await p.context().close();
  });

  await step("Compliance: a Georgia job's lien deadlines, and the expired COI warning on its pay app", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const run = Date.now().toString(36);
    const name = `Magnolia lobby 90 ${run}`;
    const made = await api(p, "/api/jobs", { name, address: "12 Magnolia St, Atlanta, GA 30303", customer: `Magnolia Builders 90 ${run}`,
      approver: "Lee Park", ap_email: `ap-${run}@magnolia90.example`, phone: "(404) 555-0190", contract_cents: 500_000, retainage_pct: 0, role: "GC",
      client_type: "gc", new_customer: true, approver_confirmed: true });
    ok(made);
    const job = made.data.jobs.find((j) => j.name === name);
    ok(await api(p, "/api/compliance/requirements", { type: "coi", payer_id: job.payer_id, additional_insured: true }));
    ok(await api(p, "/api/compliance/docs", { type: "coi", payer_id: job.payer_id, additional_insured: true, expires_on: "2026-01-31" }));
    ok(await api(p, `/api/compliance/liens/${job.id}`, { last_furnished_on: "2026-11-02" }));
    await p.goto(`${BASE}/jobs/compliance`);
    const card = p.locator(`[data-more="lien-${job.id}"]`);
    await card.locator("summary").click();
    const claim = card.locator("tr", { hasText: "File the claim of lien" });
    await claim.waitFor();
    assert.match(await claim.innerText(), /44-14-361\.1\(a\)\(2\)/);
    assert.match(await claim.innerText(), /Jan 29, 2027/, "Nov 2 + 90 is Sun Jan 31: safer by Fri Jan 29");
    assert.match(await card.innerText(), /don't need a notary/);
    // The soft gate: a draft pay app for this client shows the warning, and Approve & send stays.
    const line = job.sov_view && job.sov_view.lines && job.sov_view.lines[0];
    ok(line ? await api(p, `/api/jobs/${job.id}/sov-progress`, { done: { [line.id]: "0.5" }, notes: "", period_to: "" })
      : await api(p, `/api/jobs/${job.id}/progress`, { completed_cents: 250_000, notes: "", period_to: "" }));
    const view = (await api(p, `/api/jobs/${job.id}`)).data.job;
    const draft = view.invoices.find((i) => i.status === "draft" || i.status === "awaiting_approval");
    await p.goto(`${BASE}/money/pay-app/${job.id}/${draft.id}`);
    const warning = p.locator(".compliance-warning");
    await warning.waitFor();
    assert.match(await warning.innerText(), /expired Jan 31, 2026/);
    assert.match(await warning.innerText(), /You can still send/);
    await p.context().close();
  });
}
