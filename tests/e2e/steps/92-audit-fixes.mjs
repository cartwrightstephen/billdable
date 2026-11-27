// Browser steps for the Nov 27 build-list audit's fixes: a saved job record can be deleted from the
// job page (what it added comes off; a file stays), and a record that isn't a diary entry opens
// the full form on Edit. A company of its own, so the demo's data stays as the other steps expect
// it. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ browser, BASE, step, confirmIfAsked, errors }) {
  let p;
  async function company() {
    if (p) return;
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    const status = await p.evaluate(async (email) => (await fetch("/api/auth/signup", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accept_terms: true, email, password: "smoke test password", name: "Ada Auditor", company_name: "Audit Build" }) })).status,
    `audit-${Date.now()}@example.com`);
    assert.equal(status, 200);
  }
  const api = (path, body) => p.evaluate(async ([path, body]) => {
    const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return [r.status, await r.json()];
  }, [path, body]);

  await step("Audit: Delete on a saved job record takes it and its rows off the job page (item 19)", async () => {
    await company();
    let [status, st] = await api("/api/jobs", { phone: "(404) 555-0100", approver_confirmed: true, approver: "Fir Owner", name: "Fir deck",
      street: "4 Fir St", city: "Atlanta", state: "GA", zip: "30303", customer: "Fir Owner", role: "Owner", new_customer: true,
      ap_email: "pay@fir.example", contract_cents: 600000, retainage_pct: 0 });
    assert.equal(status, 200, JSON.stringify(st));
    const jobId = st.jobs.find(j => j.name === "Fir deck").id;
    [status, st] = await api(`/api/jobs/${jobId}/daily-record`, { notes: "Set posts on the east side", today: new Date().toISOString().slice(0, 10),
      entries: [{ section: "inspections", row: 0, who: "County inspector", outcome: "Footings passed" }] });
    assert.equal(status, 200, JSON.stringify(st));
    await p.goto(`${BASE}/jobs/${jobId}`);
    const records = p.locator("details.job-records");
    if (await records.getAttribute("open") === null) await records.locator(":scope > summary").click();
    await p.getByText("Footings passed").first().waitFor();
    await p.getByRole("button", { name: /^Delete Diary entry/ }).click();
    await confirmIfAsked(p);
    await p.getByText("Footings passed").waitFor({ state: "detached" });
    [, st] = await api(`/api/jobs/${jobId}`);
    assert.equal((st.job.records || []).length, 0, "the diary entry and its inspection row went");
  });

  await step("Audit: Edit on a record that isn't a diary entry opens the form with its own section (item 19)", async () => {
    await company();
    const [, all] = await api("/api/state");
    const jobId = all.jobs.find(j => j.name === "Fir deck").id;
    let [status] = await api(`/api/jobs/${jobId}/daily-record`, { notes: "", today: new Date().toISOString().slice(0, 10),
      entries: [{ section: "invoices", row: 0, supplier: "Home Depot", number: "55", amount: "40" }] });
    assert.equal(status, 200);
    await p.goto(`${BASE}/jobs/${jobId}`);
    const records = p.locator("details.job-records");
    if (await records.getAttribute("open") === null) await records.locator(":scope > summary").click();
    await p.getByRole("button", { name: "Edit Invoice: Home Depot #55" }).click();
    await p.getByRole("heading", { name: "Edit record" }).waitFor();
    assert.equal(await p.locator("#daily-invoices-supplier-0").inputValue(), "Home Depot");
    assert.equal(await p.locator("#daily-notes").count(), 0, "no diary on a single record");
    await p.locator("#daily-invoices-number-0").fill("56");
    await p.locator('[data-action="daily-save"]').click();
    await p.waitForURL(`**/jobs/${jobId}`);
    const [, st] = await api(`/api/jobs/${jobId}`);
    assert.ok(st.job.records.some(r => r.label === "Invoice: Home Depot #56" && r.amount_cents === 4000), JSON.stringify(st.job.records));
  });
}
