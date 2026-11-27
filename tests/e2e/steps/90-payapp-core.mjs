// Browser steps for the pay-app core (Nov 27): a job started mid-project with an earlier pay app,
// retainage by line, stored materials moved into work done, and Bill retainage release. A company
// of its own, so the demo's data stays as the other steps expect it. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ browser, BASE, step, confirmIfAsked, errors }) {
  let p, jobId;
  async function company() {
    if (p) return;
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    await p.evaluate(async (email) => {
      await fetch("/api/auth/signup", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accept_terms: true, email, password: "smoke test password", name: "Mia Midway", company_name: "Midway Mechanical" }) });
    }, `payapp-core-${Date.now()}@example.com`);
  }
  const api = (path, body) => p.evaluate(async ([path, body]) => {
    const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return [r.status, await r.json()];
  }, [path, body]);
  const job = async () => (await api(`/api/jobs/${jobId}`))[1].job;
  const daysAgo = (n) => new Date(Date.now() - n * 86400000).toISOString().slice(0, 10);

  await step("Pay-app core: a job with pay apps before Duracash takes its earlier pay app, and the next number follows it (Nov 27)", async () => {
    await company();
    const [status, st] = await api("/api/jobs", { phone: "(404) 555-0100", approver_confirmed: true, approver: "Gail GC", name: "Midway clinic", address: "8 Mid St",
      customer: "Gail Builders", role: "GC", client_type: "gc", new_customer: true, ap_email: "ap@gail.example", contract_cents: 1500000, retainage_pct: 10,
      sov_lines: [{ description: "Labor", quantity: "1", unit: "LS", rate: "10000" }, { description: "Materials", quantity: "100", unit: "ea", rate: "50" }] });
    assert.equal(status, 200, JSON.stringify(st));
    jobId = st.jobs.find(j => j.name === "Midway clinic").id;
    await p.goto(`${BASE}/jobs/${jobId}`);
    await p.locator('[data-action="toggle-form"][data-form="editjob"]').click();
    await p.locator('[data-action="earlier-open"]').click();
    const card = p.locator("#earlier-card");
    await card.waitFor();
    await card.locator("#ep-number").fill("3");
    await card.locator("#ep-period").fill(daysAgo(40));
    const lines = (await job()).sov.lines;
    await card.locator('[data-action="earlier-lines-as"][data-mode="pct"]').click();
    await card.locator(`#ep-line-${lines[0].id}`).fill("30");
    await card.locator(`#ep-line-${lines[1].id}`).fill("20");
    await card.locator("#ep-paid").fill("3,600");
    await card.locator("#ep-paid-on").fill(daysAgo(20));
    await card.locator('[data-action="earlier-save"]').click();
    await card.locator(".earlier-row", { hasText: "PA-" }).waitFor();
    const j = await job();
    const earlier = j.invoices.find(i => i.imported);
    assert.equal(earlier.gross_cents, 400000);
    assert.equal(earlier.retainage_cents, 40000);
    assert.equal(earlier.status, "paid");
    assert.match(j.earlier_payapps.next_number, /-04$/);
    await card.locator("p.sub", { hasText: "The next pay app here is" }).waitFor();
  });

  await step("Pay-app core: retainage by line, then stored materials asked to move into work done", async () => {
    await company();
    const lines = (await job()).sov.lines;
    await p.goto(`${BASE}/jobs/${jobId}`);
    const fold = p.locator("details.line-retainage");
    await fold.locator("summary").click();
    await fold.locator(`#lr-${lines[1].id}`).fill("0");
    await fold.locator('[data-action="line-retainage"]').click();
    await fold.locator("summary .sub", { hasText: "1 line with its own percent" }).waitFor();
    // Stored materials on Materials, then more work there: the page asks to move them.
    let [status, st] = await api(`/api/jobs/${jobId}/sov-progress`, { done: { [lines[0].id]: "0.4", [lines[1].id]: "20" }, stored: { [lines[1].id]: "1000" } });
    assert.equal(status, 200, JSON.stringify(st));
    let draft = (await job()).invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
    assert.match(draft.number, /-04$/);
    assert.equal(draft.retainage_cents, 10000, "10% of the labor only; materials at 0%");
    await p.goto(`${BASE}/money/pay-app/${jobId}/${draft.id}`);
    await p.locator(".stored-backup", { hasText: "Stored materials backup" }).waitFor();
    await p.goto(`${BASE}/money/new-pay-app/${jobId}`);
    await p.locator(`#sovp-done-${lines[1].id}`).fill("40");
    await p.locator('[data-action="sov-progress"]').click();
    const dialog = p.locator("dialog.confirm-dialog");
    await dialog.waitFor();
    assert.match(await dialog.innerText(), /\$1,000\.00 of materials are stored on this line/);
    await confirmIfAsked(p);
    await p.locator("h1", { hasText: /^PA-/ }).waitFor();
    draft = (await job()).invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
    const mat = draft.sov_sheet.find(e => e.line_id === lines[1].id);
    assert.equal(mat.stored_cents, 0);
    assert.equal(mat.to_date_cents, 200000);
  });

  await step("Pay-app core: Bill retainage release drafts a pay app with no new work", async () => {
    await company();
    let [status, st] = await api(`/api/jobs/${jobId}/void-draft`, {});
    assert.equal(status, 200, JSON.stringify(st));
    await p.goto(`${BASE}/jobs/${jobId}`);
    const card = p.locator(".retainage-release");
    await card.locator('[data-form="retrelease"]').click();
    await card.locator("#rr-amt").fill("150");
    await card.locator('[data-action="retainage-release"]').click();
    await p.locator("h1", { hasText: /^PA-/ }).waitFor();
    const rel = (await job()).invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
    assert.equal(rel.amount_cents, 15000);
    assert.equal(rel.gross_cents, 0);
    assert.ok(rel.lines[0].retainage_release);
    await p.locator(".payapp-numbers summary").click();
    await p.locator(".payapp-summary", { hasText: "Retainage held" }).waitFor();
  });
}
