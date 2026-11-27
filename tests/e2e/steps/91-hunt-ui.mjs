// Browser steps for the Nov 27 UI hunt: every page walked at desktop, iPad and phone widths.
// Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ browser, step, signedInPage, BASE, confirmDialog, errors }) {
  await step("hunt ui: the phone's What do these mean? stays inside the page after a job record is saved", async () => {
    const p = await signedInPage({ width: 375, height: 812 });
    await p.goto(`${BASE}/jobs/daily`);
    await p.locator("#daily-job").selectOption("job-grant");
    await p.fill("#daily-notes", "Hunt: hung drywall");
    await p.click('[data-action="daily-save"]');
    await p.locator("#toast", { hasText: "Job record saved" }).waitFor();
    const where = await p.evaluate(() => [...document.querySelectorAll(".help-list")].map(h => !!h.closest("#view")));
    assert.ok(where.length <= 1, "one list at most");
    assert.ok(where.every(Boolean), "the list is in the page, not after the menu");
    await p.goto(`${BASE}/dashboard`);
    await p.goto(`${BASE}/jobs/daily`);
    assert.ok(await p.evaluate(() => [...document.querySelectorAll(".help-list")].every(h => h.closest("#view"))));
    await p.context().close();
  });

  await step("hunt ui: Remove on a time sheet row keeps the keyboard focus in the time sheet", async () => {
    const p = await signedInPage({ width: 1024, height: 768 });
    await p.goto(`${BASE}/jobs/daily`);
    await p.locator('[data-action="daily-more"][data-kind="crew"]').focus();
    await p.keyboard.press("Enter");
    await p.locator('[data-action="daily-row-remove"][data-kind="crew"][data-n="1"]').focus();
    await p.keyboard.press("Enter");
    const at = await p.evaluate(() => document.activeElement && document.activeElement.dataset.action);
    assert.equal(at, "daily-row-remove");
    await p.context().close();
  });

  await step("hunt ui: Approve & send on an unsigned pay app shows why first, in one sentence, and asks Email PA-x? only once it can go", async () => {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    await p.evaluate(async (email) => {
      await fetch("/api/auth/signup", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accept_terms: true, email, password: "smoke test password", name: "Hunt Signer", company_name: "Hunt Build" }) });
    }, `hunt-send-${Date.now()}@example.com`);
    const api = (path, body) => p.evaluate(async ([path, body]) => {
      const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      return [r.status, await r.json()];
    }, [path, body]);
    let [status, st] = await api("/api/jobs", { phone: "(404) 555-0100", approver_confirmed: true, approver: "Hunt Owner", name: "Hunt porch", address: "4 Birch St",
      customer: "Hunt Owner", role: "Owner", new_customer: true, ap_email: "pay@hunt.example", contract_cents: 600000, retainage_pct: 0 });
    assert.equal(status, 200, JSON.stringify(st));
    const jobId = st.jobs.find(j => j.name === "Hunt porch").id;
    [status, st] = await api(`/api/jobs/${jobId}/done`, { notes: "" });
    let draft = st.jobs.find(j => j.id === jobId).invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
    for (const item of draft.packet || []) await api(`/api/jobs/${jobId}/resolve/${item.id}`, { reason: "In hand" });
    await p.goto(`${BASE}/money/pay-app/${jobId}/${draft.id}`);
    await p.click('.send-check [data-action="send"][data-blocked="1"]');
    const card = p.locator(".action-error", { hasText: "needs a signature from Hunt Signer" });
    await card.waitFor();
    assert.doesNotMatch(await card.innerText(), /hasn't signed it/);
    assert.equal(await confirmDialog(p).count(), 0, "no Email PA-x? for a pay app that can't go");
    const form = p.locator(".send-check .approve-sign");
    await form.locator('input[name="signature"]').fill("Hunt Signer");
    await form.locator('[data-action="payapp-approve"]').click();
    await p.locator('.send-check [data-action="send"]:not([data-blocked])').click();
    await confirmDialog(p).waitFor();
    await confirmDialog(p).locator(".confirm-actions .btn:not(.secondary)").click();
    await p.locator("h1 + .sub .pill", { hasText: "Sent" }).waitFor();
    await ctx.close();
  });
}
