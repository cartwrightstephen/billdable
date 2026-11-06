// Browser steps for Round 1's job items (Nov 5 audits): a job of the same name at New job (item 20),
// Bill by dollars instead on a one-line schedule (item 10), and the Next step on a new job (item 6).
// Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ BASE, step, signedInPage }) {
  await step("New job: a job of the same name is asked about, and Keep as a new job saves it", async () => {
    const p = await signedInPage({ width: 375, height: 812 });
    const name = `Twin job ${Date.now()}`;
    await p.evaluate(async (n) => {
      await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: n, address: "2 Elm St", customer: "Twin Client", role: "Owner", client_type: "homeowner", approver: "Tia", phone: "(404) 555-0100", ap_email: "tia@example.com", contract_cents: 500000 }) });
    }, name);
    await p.goto(`${BASE}/jobs`);
    await p.click('[data-action="toggle-form"][data-form="newjob"]');
    await p.fill("#nj-name", name.toLowerCase());
    await p.fill("#nj-street", "2 Elm St"); await p.fill("#nj-city", "Decatur"); await p.selectOption("#nj-state", "GA"); await p.fill("#nj-zip", "30030");
    await p.fill("#nj-amount", "5,000");
    await p.click('[data-action="nj-client-mode"][data-mode="existing"]');
    await p.selectOption("#nj-client", "Twin Client");
    await p.click('[data-action="create-job"]');
    const box = p.locator('[data-same-job="newjob"]');
    await box.getByRole("button", { name: `Open ${name}` }).waitFor();
    await box.getByRole("button", { name: "Keep as a new job" }).click();
    await p.locator(".job-head h1", { hasText: name.toLowerCase() }).waitFor();
    const st = await p.evaluate(async () => (await (await fetch("/api/state")).json()));
    assert.equal(st.jobs.filter((j) => j.name.toLowerCase() === name.toLowerCase()).length, 2);
    await p.context().close();
  });

  await step("a one-line schedule bills by dollars instead: the exact amount, on its % done", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const id = await p.evaluate(async () => {
      const r = await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: `Dollar job ${Date.now()}`, address: "3 Elm St", customer: "Dollar GC", client_type: "gc", approver: "Dee", phone: "(404) 555-0100", ap_email: "dee@example.com", contract_cents: 4850000, retainage_pct: 10 }) });
      const st = await r.json();
      return st.jobs.find((j) => j.name.startsWith("Dollar job")).id;
    });
    await p.goto(`${BASE}/jobs/${id}`);
    // Item 6: the line names the button under it.
    const line = await p.locator(".next-card strong").first().innerText();
    const button = await p.locator(".next-card .primary-action").innerText();
    assert.ok(line.toLowerCase().includes(button.toLowerCase()), `${line} / ${button}`);
    await p.locator(".next-card .primary-action").click();
    assert.equal(await p.locator('[data-sovp][data-lump="1"]').getAttribute("aria-label"), "Contract amount: % done to date");
    await p.getByRole("button", { name: "Bill by dollars instead" }).click();
    await p.fill("#progress-draw", "10,000");
    assert.equal(await p.locator("#progress-preview").innerText(), "This pay app: $10,000.00 less retainage $1,000.00 = $9,000.00.");
    await p.click('[data-action="progress"]');
    await p.waitForFunction(async (jid) => {
      const j = (await (await fetch("/api/state")).json()).jobs.find((x) => x.id === jid);
      return j.invoices.some((i) => i.gross_cents === 1000000);
    }, id);
    await p.context().close();
  });
}
