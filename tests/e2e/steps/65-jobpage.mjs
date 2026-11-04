// Browser steps for the job page's actions: all of them showing, and Log a Conversation. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ BASE, step, signedInPage, confirmDialog }) {
  const waitingJob = (p) => p.evaluate(async () => {
    const st = await (await fetch("/api/state")).json();
    const j = st.jobs.find((x) => !x.closed && x.invoices.some((i) => ["sent", "approved", "partial"].includes(i.status)));
    return j && j.id;
  });

  await step("Log a Conversation: a record on the job that holds the follow-up until its date", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const id = await waitingJob(p);
    assert.ok(id, "the demo has a job waiting for payment");
    await p.goto(`${BASE}/jobs/${id}`);
    await p.locator(".job-actions").waitFor();
    assert.equal(await p.getByRole("button", { name: /Spoke to them/ }).count(), 0);
    await p.getByRole("button", { name: "Log a Conversation" }).click();
    await p.click('[data-action="save-conversation"]');
    await p.locator('[data-form-key="conversation"] .field-error', { hasText: "Enter what they said" }).waitFor();
    await p.fill("#conversation-text", "Check run is the 5th");
    const next = new Date(Date.now() + 5 * 86400000).toISOString().slice(0, 10);
    await p.fill("#conversation-next", next);
    await p.click('[data-action="save-conversation"]');
    await p.locator("#conversation-text").waitFor({ state: "detached" });
    await p.locator('.record-group[data-group="conversation"]', { hasText: "Check run is the 5th" }).waitFor();
    const job = await p.evaluate(async (jid) => (await (await fetch("/api/state")).json()).jobs.find((j) => j.id === jid), id);
    assert.equal(job.expected_payment_on, next);
    await p.context().close();
  });

  await step("phone: the job page's actions all show, and nothing runs off the side", async () => {
    const p = await signedInPage({ width: 375, height: 812 });
    const id = await waitingJob(p);
    await p.goto(`${BASE}/jobs/${id}`);
    await p.locator(".job-actions").waitFor();
    const buttons = p.locator(".job-actions .btn");
    assert.ok(await buttons.count() > 1);
    for (const b of await buttons.all()) assert.ok(await b.isVisible());
    const wide = await p.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    assert.ok(wide <= 0, `page scrolls sideways by ${wide}px`);
    assert.equal(await p.locator("details.money-file").getAttribute("open"), "", "Money is open on a phone");
    await p.context().close();
  });

  await step("Final Pay App confirms first: the amount and what it does, and Cancel changes nothing", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const id = await p.evaluate(async () => {
      const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const st = await (await post("/api/jobs", { name: "Final check", address: "9 Elm St", customer: "Rae Final", role: "Owner", ap_email: "rae@example.com", contract_cents: 1000000 })).json();
      return st.jobs.find((j) => j.name === "Final check").id;
    });
    await p.goto(`${BASE}/jobs/${id}`);
    assert.match(await p.locator(".next-card strong").first().innerText(), /Progress Pay App/);
    await p.locator('.job-actions [data-form="done"]').click();
    await p.locator('[data-form-key="done"] h2', { hasText: "Final Pay App" }).waitFor();
    await p.click('[data-action="done"]');
    await confirmDialog(p).waitFor();
    assert.match(await confirmDialog(p).innerText(), /Final Pay App for Final check: \$[\d,.]+.*marks the work finished/s);
    await confirmDialog(p).getByRole("button", { name: "Cancel" }).click();
    const job = await p.evaluate(async (jid) => (await (await fetch("/api/state")).json()).jobs.find((j) => j.id === jid), id);
    assert.notEqual(job.work_status, "complete", "Cancel left the job open");
    await p.context().close();
  });
}
