// Browser steps for the hunt of Nov 5: a wrong file at the waiver step, a file that says unconditional
// on Review & send, the way back from a client's waiver skip, a new code for a phone number, and
// controls at phone width. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ browser, BASE, step, signedInPage, confirmDialog, confirmIfAsked }) {
  const made = (p, name, thenPaid = false) => p.evaluate(async ([jobName, paid]) => {
    const call = async (path, body) => (await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })).json();
    let st = await call("/api/jobs", { name: jobName, customer: `${jobName} client`, contract_cents: 1000000, address: "7 Test St, Atlanta, GA 30307",
      approver: "Dee", phone: "(404) 555-0100", ap_email: "dee@hunt.example", role: "Owner", new_customer: true });
    const job = st.jobs.find((j) => j.name === jobName);
    const line = (await (await fetch(`/api/jobs/${job.id}`)).json()).job.sov.lines[0].id;
    await call(`/api/jobs/${job.id}/sov-progress`, { done: { [line]: "0.5" } });
    const read = async () => (await (await fetch(`/api/jobs/${job.id}`)).json()).job;
    let full = await read();
    const draft = () => full.invoices.find((i) => ["draft", "awaiting_approval"].includes(i.status));
    for (const item of draft().packet) if (item.required && !item.present) await call(`/api/jobs/${job.id}/resolve/${item.id}`, { reason: "In hand" });
    full = await read();
    if (paid) {
      await call(`/api/jobs/${job.id}/send`, { reviewed: draft().email_preview.stamp });
      full = await read();
      const inv = full.invoices.at(-1);
      await call(`/api/jobs/${job.id}/match-payment`, { amount_cents: inv.amount_cents, reference: "chk", invoice_id: inv.id });
    }
    return { id: job.id, inv: full.invoices.at(-1).id, payer: job.payer_id };
  }, [name, thenPaid]);

  await step("A wrong file at the waiver step names what didn't match, and goes only on a person's word", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const { id } = await made(p, "Smoke wrong waiver", true);
    await p.goto(`${BASE}/money/waiver/${id}`);
    const send = p.locator('[data-action="waiver"]').first();
    await send.waitFor();
    const [chooser] = await Promise.all([p.waitForEvent("filechooser"), p.locator('.waiver-row [data-action="pick-files"]:not([data-capture])').first().click()]);
    await chooser.setFiles({ name: "signed.txt", mimeType: "text/plain", buffer: Buffer.from("CONDITIONAL WAIVER AND RELEASE") });
    for (let t = 0; t < 100 && (await send.isDisabled()); t++) await new Promise((r) => setTimeout(r, 100));
    await send.click();
    await confirmIfAsked(p);
    await confirmDialog(p).filter({ hasText: "It says conditional, not unconditional." }).waitFor();
    assert.ok(await confirmDialog(p).getByRole("button", { name: "I opened it and it's right" }).count());
    await confirmIfAsked(p);
    await p.waitForFunction(async (jobId) => (await (await fetch(`/api/jobs/${jobId}`)).json()).job.invoices.some((i) => i.waiver_sent), id);
    await p.context().close();
  });

  await step("A note that reads as an unconditional waiver is named on Review & send, with the override", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const { id, inv } = await made(p, "Smoke says unconditional");
    await p.evaluate(async ([jobId, invId]) => {
      const res = await fetch(`/api/import/file?job=${jobId}&file=1&kind=note`, { method: "POST", headers: { "Content-Type": "text/plain", "X-Filename": "paper.txt" }, body: "UNCONDITIONAL WAIVER AND RELEASE" });
      const record = (await res.json()).uploaded_record_ids[0];
      await fetch(`/api/jobs/${jobId}/payapp/${invId}/attachments`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "add_record", record_id: record }) });
    }, [id, inv]);
    await p.goto(`${BASE}/money/pay-app/${id}/${inv}`);
    const warning = p.locator(".unconditional-warning");
    await warning.filter({ hasText: "paper.txt" }).waitFor();
    await warning.locator(`#override-reason-${inv}`).fill("Paid by wire");
    await warning.locator('[data-action="send-override"]').click();
    await confirmIfAsked(p);
    await p.waitForFunction(async (jobId) => (await (await fetch(`/api/jobs/${jobId}`)).json()).job.invoices.at(-1).unconditional_override?.reason === "Paid by wire", id);
    await p.context().close();
  });

  await step("Not needed for this client is turned back on from the client's card", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const { payer, id, inv } = await made(p, "Smoke client skip");
    await p.evaluate(async ([jobId, invId]) => {
      const job = (await (await fetch(`/api/jobs/${jobId}`)).json()).job;
      const item = job.invoices.find((i) => i.id === invId).packet.find((x) => x.waiver_kind);
      await fetch(`/api/jobs/${jobId}/packet-skip/${item.id}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: "They never ask", whole_client: true }) });
    }, [id, inv]);
    await p.goto(`${BASE}/jobs/clients`);
    const card = p.locator(`[data-payer-card="${payer}"]`);
    await card.locator("> summary").click();
    await card.locator(".override-note", { hasText: "They never ask" }).waitFor();
    await card.locator('[data-action="client-waiver-needed"]').click();
    await confirmIfAsked(p);
    await card.locator(".override-note", { hasText: "They never ask" }).waitFor({ state: "detached" });
    await p.context().close();
  });

  await step("A phone code that went stale has a Send a new code button", async () => {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const p = await ctx.newPage();
    await p.goto(BASE + "/");
    await p.evaluate(async (email) => {
      await fetch("/api/auth/signup", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "Code Tester", company_name: "Code Co", email, password: "correct horse battery", accept_terms: true }) });
    }, `code-${Date.now()}@example.com`);
    let asked = 0;
    await p.route("**/api/auth/sms", async (route) => {
      asked += 1;
      const { user } = await (await p.request.get(`${BASE}/api/auth/me`)).json();
      await route.fulfill({ json: { user, code_sent: true, sent_to: "+14045550123" } });
    });
    await p.goto(`${BASE}/settings/account`);
    await p.fill("#my-phone", "(404) 555-0123");
    await p.check("#my-sms-opt-in");
    await p.locator(".save-bar").getByRole("button", { name: "Save" }).click();
    await p.locator('[data-action="sms-resend"]').click();
    await p.locator("#my-sms-code").waitFor();
    for (let t = 0; t < 50 && asked < 2; t++) await new Promise((r) => setTimeout(r, 100));
    assert.equal(asked, 2, "the button asks for another code");
    await ctx.close();
  });

  await step("At phone width the small controls are tall enough, and a long email wraps", async () => {
    const p = await signedInPage({ width: 375, height: 800 });
    const { id } = await made(p, "Smoke phone controls", true);
    await p.evaluate(async (jobId) => {
      await fetch(`/api/jobs/${jobId}/add-record`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label: "A note", kind: "note" }) });
    }, id);
    await p.goto(`${BASE}/jobs/${id}`);
    await p.locator("#sov-card").waitFor();
    for (const selector of ['[data-action="sov-correct-open"]', '[data-action="record-edit"]']) {
      const box = await p.locator(selector).first().boundingBox();
      assert.ok(box && box.height >= 40, `${selector}: ${box && box.height}`);
    }
    await p.goto(`${BASE}/money/waiver/${id}`);
    const legal = p.locator('[data-focus="#co-legal-name"]').first();
    if (await legal.count()) assert.ok((await legal.boundingBox()).height >= 40, "Add your legal name");
    await p.setViewportSize({ width: 320, height: 700 });
    await p.goto(`${BASE}/settings/account`);
    await p.locator(".long-word").waitFor();
    const wide = await p.evaluate(() => {
      document.querySelector(".long-word").firstChild.textContent = `${"a".repeat(33)}@example.com `;
      return document.documentElement.scrollWidth - document.documentElement.clientWidth;
    });
    assert.ok(wide <= 0, `the page scrolls sideways by ${wide}px`);
    await p.context().close();
  });
}
