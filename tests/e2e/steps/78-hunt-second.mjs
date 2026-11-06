// Browser steps for the second hunt of Nov 5: a long email at 320 px on the real page, a file that
// only mentions an unconditional waiver, a refund a later change order leaves short, a signed credit
// off the retainage, one look on a flagged file, Confirm refused for a newer file, and the "?" clear
// of the page at phone width. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ browser, BASE, step, signedInPage, confirmDialog }) {
  const call = (p, path, body) => p.evaluate(async ([to, what]) => {
    const r = await fetch(to, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(what || {}) });
    return { status: r.status, data: await r.json() };
  }, [path, body]);
  const read = (p, id) => p.evaluate(async (jobId) => (await (await fetch(`/api/jobs/${jobId}`)).json()).job, id);
  const draftOf = (job) => job.invoices.find((i) => ["draft", "awaiting_approval"].includes(i.status));
  const upload = (p, id, name, text, query) => p.evaluate(async ([jobId, file, words, q]) => {
    const qs = new URLSearchParams({ job: jobId, file: "1", ...q });
    const r = await fetch(`/api/import/file?${qs}`, { method: "POST", headers: { "X-Filename": encodeURIComponent(file), "Content-Type": "application/octet-stream" }, body: words });
    return { status: r.status, data: await r.json() };
  }, [id, name, text, query]);
  const made = async (p, name, retainage = 0) => {
    const st = (await call(p, "/api/jobs", { name, customer: `${name} client`, contract_cents: 10000000, retainage_pct: retainage, address: "7 Test St, Atlanta, GA 30307",
      approver: "Dee", phone: "(404) 555-0100", ap_email: "dee@hunt.example", role: "Owner", new_customer: true })).data;
    const id = st.jobs.find((j) => j.name === name).id;
    const line = (await read(p, id)).sov.lines[0].id;
    await call(p, `/api/jobs/${id}/sov-progress`, { done: { [line]: "1" } });
    await call(p, `/api/jobs/${id}/done`, {});
    return id;
  };
  const clear = async (p, id) => {
    for (const item of draftOf(await read(p, id)).packet) if (item.required && !item.present) await call(p, `/api/jobs/${id}/resolve/${item.id}`, { reason: "In hand" });
  };
  const sendAndPay = async (p, id) => {
    await clear(p, id);
    const r = await call(p, `/api/jobs/${id}/send`, { reviewed: draftOf(await read(p, id)).email_preview.stamp });
    assert.equal(r.status, 200, JSON.stringify(r.data));
    const inv = (await read(p, id)).invoices.at(-1);
    await call(p, `/api/jobs/${id}/match-payment`, { amount_cents: inv.amount_cents, reference: "chk", invoice_id: inv.id });
    return inv;
  };
  const signedCo = async (p, id, cents) => {
    const added = (await call(p, `/api/jobs/${id}/add-co`, { title: "Change", amount_cents: cents, reason: "owner_request" })).data;
    const job = await read(p, id);
    const co = job.change_orders.at(-1);
    const rec = await upload(p, id, "signed-co.txt", "Signed change order", { kind: "change_order" });
    const r = await call(p, `/api/jobs/${id}/approve-co/${co.id}`, { record_id: rec.data.uploaded_record_ids[0], signer_name: "Dee", signed_on: "2026-11-01" });
    assert.equal(r.status, 200, JSON.stringify(r.data || added));
  };

  await step("A 96-character email doesn't scroll /settings/account sideways at 320 px", async () => {
    const ctx = await browser.newContext({ viewport: { width: 320, height: 700 } });
    const p = await ctx.newPage();
    // Codes by email are on only with a mail server: the page's own answer says so, and the page draws the rest.
    await p.route("**/api/auth/me", async (route) => {
      const res = await route.fetch();
      const body = await res.json();
      await route.fulfill({ response: res, json: { ...body, codes_by_email: true } });
    });
    await p.goto(BASE + "/");
    const email = `${"a".repeat(84 - String(Date.now()).length)}${Date.now()}@example.com`;
    assert.equal(email.length, 96);
    const res = await call(p, "/api/auth/signup", { accept_terms: true, email, password: "smoke test password", name: "Long Mail", company_name: "Long Mail Builders" });
    assert.equal(res.status, 200, JSON.stringify(res.data));
    for (const width of [320, 375]) {
      await p.setViewportSize({ width, height: 700 });
      await p.goto(`${BASE}/settings/account`);
      await p.locator(".long-word").first().waitFor();
      const wide = await p.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      assert.ok(wide <= 0, `${width} px: the page scrolls sideways by ${wide}px`);
      const rail = await p.evaluate(() => { const m = document.querySelector(".user-meta"); return m ? m.scrollWidth - m.clientWidth : 0; });
      assert.ok(rail <= 0, `the side rail's email overflows by ${rail}px`);
    }
    await ctx.close();
  });

  await step("At phone width the ? sits in the bar, over no line of the page", async () => {
    const p = await signedInPage({ width: 375, height: 800 });
    await p.goto(`${BASE}/settings/account`);
    await p.locator(".topbar").waitFor();
    const clash = await p.evaluate(() => {
      const b = document.getElementById("ai-badge").getBoundingClientRect();
      const bar = document.querySelector(".topbar").getBoundingClientRect();
      return b.bottom <= bar.bottom + 1 && b.top >= bar.top - 1;
    });
    assert.ok(clash, "the ? is inside the top bar");
    await p.context().close();
  });

  await step("A file that only mentions an unconditional waiver: the words shown, one look, then it sends", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const id = await made(p, "Smoke mention");
    const res = await upload(p, id, "log.txt", "Daily log. Sub's unconditional lien waiver received from plumber for last month.", { kind: "note" });
    const draft = draftOf(await read(p, id));
    await call(p, `/api/jobs/${id}/payapp/${draft.id}/attachments`, { action: "add_record", record_id: res.data.uploaded_record_ids[0] });
    await clear(p, id);
    await p.goto(`${BASE}/money/pay-app/${id}/${draft.id}`);
    const look = p.locator('[data-action="unconditional-look"]');
    await look.waitFor();
    await look.click();
    await confirmDialog(p).filter({ hasText: "unconditional lien waiver received" }).getByRole("button", { name: "It's right" }).click();
    await p.waitForFunction(async (jobId) => !((await (await fetch(`/api/jobs/${jobId}`)).json()).job.invoices.at(-1).unconditional_mentions || []).length, id);
    await p.context().close();
  });

  await step("A refund a later change order leaves short shows owed, and Record a Payment takes it", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const id = await made(p, "Smoke refund");
    await sendAndPay(p, id);
    await signedCo(p, id, -1000000);
    assert.equal((await call(p, `/api/jobs/${id}/overbilling-settled`, { reason: "refund check 1044" })).status, 200);
    await signedCo(p, id, 500000);
    const job = await read(p, id);
    assert.equal(job.money.collected_cents, 9000000);
    assert.equal(job.money.refund_owed_cents, 500000);
    await p.goto(`${BASE}/jobs/${id}`);
    const row = p.locator('[data-blocker="outstanding"]');
    await row.filter({ hasText: "owed back" }).waitFor();
    await row.getByRole("button", { name: "Record a Payment" }).click();
    await p.locator('#match-amt').waitFor();
    await p.locator('[data-action="match"]').click();
    await p.waitForFunction(async (jobId) => (await (await fetch(`/api/jobs/${jobId}`)).json()).job.money.refund_owed_cents === 0, id);
    await p.context().close();
  });

  await step("A signed credit comes off the retainage from the job page, and the job can finish", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const id = await made(p, "Smoke credit", 10);
    await sendAndPay(p, id);
    await signedCo(p, id, -500000);
    assert.equal((await call(p, `/api/jobs/${id}/retainage-paid`, { amount_cents: 500000, reference: "chk 2" })).status, 200);
    await p.goto(`${BASE}/jobs/${id}`);
    await p.locator('[data-blocker="overbilled"]').getByRole("button", { name: "Take Credit off Retainage" }).click();
    await p.locator("#retcredit-reason").fill("CO 1 comes off retainage");
    await p.locator('[data-action="retainage-credit"]').click();
    await p.waitForFunction(async (jobId) => (await (await fetch(`/api/jobs/${jobId}`)).json()).job.money.retainage_open_cents === 0, id);
    const job = await read(p, id);
    assert.deepEqual(job.close_blocker_items.map((b) => b.kind).filter((k) => ["overbilled", "retainage"].includes(k)), []);
    await p.context().close();
  });

  await step("One tap clears a flagged file whose warning names everything, with no second prompt", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const id = await made(p, "Smoke one look");
    const draft = draftOf(await read(p, id));
    const item = draft.packet.find((i) => i.waiver_kind);
    await upload(p, id, "w.txt", "Lien waiver for somewhere else", { kind: "waiver", attach: item.id, waiver_kind: item.waiver_kind });
    await p.goto(`${BASE}/money/packet/${id}/${draft.id}`);
    const row = p.locator(`#packet-item-${item.id}`);
    await row.filter({ hasText: "Amount not found" }).filter({ hasText: "Job name not found" }).waitFor();
    await row.locator('[data-action="packet-confirm"]').click();   // round 1, item 3
    await p.waitForFunction(async ([jobId, itemId]) => (await (await fetch(`/api/jobs/${jobId}`)).json()).job.invoices.at(-1).packet.find((i) => i.id === itemId).present, [id, item.id]);
    await p.context().close();
  });

  await step("Confirm refused for a newer file says so on the page", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const id = await made(p, "Smoke newer file");
    const draft = draftOf(await read(p, id));
    const item = draft.packet.find((i) => i.waiver_kind);
    await upload(p, id, "first.txt", "Lien waiver for somewhere else", { kind: "waiver", attach: item.id, waiver_kind: item.waiver_kind });
    await p.goto(`${BASE}/money/packet/${id}/${draft.id}`);
    const button = p.locator(`[data-action="packet-confirm"][data-item="${item.id}"]`);
    await button.waitFor();
    // In another session, a newer file takes over while this page stays as it is.
    await upload(p, id, "newer.txt", "Another lien waiver for somewhere else", { kind: "waiver", attach: item.id, waiver_kind: item.waiver_kind });
    await button.click();
    await p.locator(".review-again").filter({ hasText: "changed since you opened it" }).waitFor();
    assert.equal(await confirmDialog(p).count(), 0, "no prompt about a file not looked at");
    await p.context().close();
  });
}
