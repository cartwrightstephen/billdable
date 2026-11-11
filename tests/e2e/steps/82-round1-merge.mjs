// Browser steps for what Round 1 finished after its three parts merged: New job's Retainage %
// follows the client and keeps a typed 0, the client card's Usual retainage %, and a lump sum's
// % done to date on New job record. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

const json = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export default async function ({ BASE, step, signedInPage }) {
  await step("New job: Retainage % starts at the client's usual, follows the type, and a typed 0 is saved as 0", async () => {
    const p = await signedInPage({ width: 375, height: 812 });
    const client = `Usual GC ${Date.now()}`;
    await p.evaluate(async ([c, json]) => {
      await fetch("/api/jobs", { ...json, body: JSON.stringify({ name: `${c} first`, address: "4 Elm St", customer: c, client_type: "gc", approver: "Gus",
        phone: "(404) 555-0100", ap_email: "gus@example.com", contract_cents: 500000 }) });
    }, [client, json({})]);
    await p.goto(`${BASE}/jobs`);
    await p.click('[data-action="toggle-form"][data-form="newjob"]');
    await p.click('[data-action="nj-client-mode"][data-mode="new"]');
    await p.fill("#nj-customer", "Lee Home");
    await p.selectOption("#nj-client-type", "homeowner");
    assert.equal(await p.inputValue("#nj-retainage"), "0");
    await p.selectOption("#nj-client-type", "public");
    assert.equal(await p.inputValue("#nj-retainage"), "10");
    await p.click('[data-action="nj-client-mode"][data-mode="existing"]');
    await p.selectOption("#nj-client", client);
    assert.equal(await p.inputValue("#nj-retainage"), "10", "a general contractor's usual");
    await p.fill("#nj-retainage", "0");
    const name = `Zero hold ${Date.now()}`;
    await p.fill("#nj-name", name);
    await p.fill("#nj-street", "5 Elm St"); await p.fill("#nj-city", "Decatur"); await p.selectOption("#nj-state", "GA"); await p.fill("#nj-zip", "30030");
    await p.fill("#nj-amount", "5,000");
    await p.click('[data-action="create-job"]');
    await p.locator(".job-head h1", { hasText: name }).waitFor();
    const st = await p.evaluate(async () => (await (await fetch("/api/state")).json()));
    assert.equal(st.jobs.find((j) => j.name === name).retainage_pct, 0);
    await p.context().close();
  });

  await step("Client playbook: Usual retainage % in Contact and billing, saved and used by the next job", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const client = `Usual Owner ${Date.now()}`;
    const payerId = await p.evaluate(async ([c, init]) => {
      const st = await (await fetch("/api/jobs", { ...init, body: JSON.stringify({ name: `${c} job`, address: "6 Elm St", customer: c, client_type: "owner", approver: "Oda",
        phone: "(404) 555-0100", ap_email: "oda@example.com", contract_cents: 500000 }) })).json();
      return st.payers.find((x) => x.name === c).id;
    }, [client, json({})]);
    await p.goto(`${BASE}/jobs/clients`);
    const card = p.locator(`[data-payer-card="${payerId}"]`);
    await card.locator(":scope > summary").click();
    const box = card.locator('input[data-field="retainage_pct"]');
    assert.equal(await box.inputValue(), "", "blank: an owner holds none unless the playbook says");
    assert.equal(await box.getAttribute("placeholder"), null, "no gray example number (Stephen, 10 Nov)");
    await box.fill("5");
    await card.getByRole("button", { name: "Save client" }).click();
    await p.waitForFunction(async (id) => (await (await fetch("/api/state")).json()).payers.find((x) => x.id === id).retainage_default === 5, payerId);
    const pct = await p.evaluate(async ([c, init]) => {
      const st = await (await fetch("/api/jobs", { ...init, body: JSON.stringify({ name: `${c} second`, address: "7 Elm St", customer: c, contract_cents: 500000, retainage_pct: "" }) })).json();
      return st.jobs.find((j) => j.name === `${c} second`).retainage_pct;
    }, [client, json({})]);
    assert.equal(pct, 5);
    await p.context().close();
  });

  await step("New job record: a lump sum asks % done to date, adds the step up, and refuses a lower figure", async () => {
    const p = await signedInPage({ width: 375, height: 812 });
    const name = `Lump job ${Date.now()}`;
    const id = await p.evaluate(async ([n, init]) => {
      const st = await (await fetch("/api/jobs", { ...init, body: JSON.stringify({ name: n, address: "8 Elm St", customer: "Lump Owner", client_type: "owner", approver: "Lou",
        phone: "(404) 555-0100", ap_email: "lou@example.com", contract_cents: 4850000 }) })).json();
      return st.jobs.find((j) => j.name === n).id;
    }, [name, json({})]);
    const record = async (pct) => {
      await p.goto(`${BASE}/jobs/daily`);
      await p.selectOption("#daily-job", id);
      const box = p.locator('input[aria-label="Contract amount: % done to date"]');
      await box.fill(pct);
      await p.fill("#daily-notes", `Walked the job at ${pct}%`);
      await p.getByRole("button", { name: "Save job record" }).click();
    };
    await record("40");
    await p.waitForFunction(async (jid) => (await (await fetch("/api/state")).json()).jobs.find((x) => x.id === jid).sov_view.lines[0].field_milli === 400, id);
    await record("65");
    await p.waitForFunction(async (jid) => (await (await fetch("/api/state")).json()).jobs.find((x) => x.id === jid).sov_view.lines[0].field_milli === 650, id);
    await record("50");
    await p.getByText("Contract amount is at 65% done already").waitFor();
    const st = await p.evaluate(async () => (await (await fetch("/api/state")).json()));
    const line = st.jobs.find((x) => x.id === id).sov_view.lines[0];
    assert.equal(line.field_milli, 650);
    assert.equal(line.to_date_cents, 3152500, "65% of $48,500.00");
    await p.context().close();
  });
}
