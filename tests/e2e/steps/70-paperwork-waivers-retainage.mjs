// Browser steps for the paperwork-waivers-retainage stream (Stephen's Oct 29 review, M3-M6 and
// M13-M26): paperwork, lien waivers and retainage. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ browser, page, BASE, step, signedInPage, confirmDialog, confirmIfAsked, errors }) {
  await step("a job holding retainage has an open Retainage card", async () => {  // M23, M25, M26
    await page.goto(BASE + "/jobs/job-turner");
    const card = page.locator(".retainage-card");
    await card.waitFor();
    const text = await card.innerText();
    for (const want of ["Retainage", "Retainage held", "Retainage released", "Still held", "Times asked", "Pay apps holding it", "Releases"])
      assert.ok(text.includes(want), want);
  });

  await step("Closeout lists a waiver row per pay app and when retainage was asked for", async () => {  // M20, M25
    await page.goto(BASE + "/money/waivers");
    await page.getByRole("heading", { name: "Closeout" }).waitFor();
    // Each heading's own words, without its help icon.
    const heads = await page.locator(".closeout-waivers th").evaluateAll(ths => ths.map(th => [...th.childNodes].filter(n => !(n.classList && n.classList.contains("info"))).map(n => n.textContent).join("").trim()));
    assert.deepEqual(heads, ["Job", "Pay app", "Type", "Amount", "Status", "Action"]);
    const ret = await page.locator(".closeout-retainage th").allInnerTexts();
    for (const want of ["Done", "Times asked", "Last asked"]) assert.ok(ret.includes(want), want);
  });

  await step("the paperwork page shows what the client needs and the job records", async () => {  // M3, M5
    await page.goto(BASE + "/money/packet/job-grant");
    await page.getByRole("heading", { name: /needs to pay$/ }).waitFor();
    await page.getByRole("heading", { name: /^From the job records/ }).waitFor();
    await page.getByRole("heading", { name: "Required items" }).waitFor();
  });

  await step("a blank waiver warns, a look clears it, and an override shows on the pay app", async () => {  // Stephen's Nov 1 notes, Decisions 5, 6
    const p = await signedInPage({ width: 1280, height: 900 });
    const made = await p.evaluate(async () => {
      const call = async (path, body) => (await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })).json();
      let st = await call("/api/jobs", { name: "Smoke blank waiver", customer: "Smoke Blank Client", contract_cents: 1000000, address: "6 Test St, Atlanta, GA 30307",
        approver: "Dee", phone: "(404) 555-0100", ap_email: "dee@blank.example", role: "Owner", new_customer: true });
      const blank = st.jobs.find((j) => j.name === "Smoke blank waiver");
      const id = blank.id;
      st = await call(`/api/jobs/${id}/sov-progress`, { done: { [blank.sov.lines[0].id]: "0.4" } });
      const inv = st.jobs.find((j) => j.id === id).invoices.at(-1);
      const waiver = inv.packet.find((i) => i.waiver_kind);
      const other = inv.packet.find((i) => i !== waiver && !i.present);
      const res = await fetch(`/api/import/file?job=${id}&file=1&kind=waiver&waiver_kind=conditional_progress&attach=${waiver.id}`,
        { method: "POST", headers: { "Content-Type": "application/pdf", "X-Filename": "Signed conditional waiver.pdf" }, body: "%PDF-1.4 smoke" });
      return { id, inv: inv.id, status: res.status, other: other && other.id, waiver: waiver.id };
    });
    assert.equal(made.status, 200);
    await p.goto(BASE + `/money/pay-app/${made.id}/${made.inv}`);
    await p.getByRole("heading", { name: "Review & send" }).waitFor();
    // What is missing sits in Review & send, above an Approve & send that stays on (round 1, item 4).
    const warning = p.locator(".send-check .send-missing .packet-item .doc-warning");
    await warning.waitFor();
    assert.match(await warning.innerText(), /looks blank/);
    assert.equal(await p.locator('.send-check [data-action="send"]').isDisabled(), false);
    // One tap says which waiver it is, and is the look at the warning beside it, with no second dialog
    // (round 1, item 3; the Nov 6 redesign).
    await p.locator(".send-missing .which-waiver", { hasText: "Which waiver is this?" }).waitFor();
    await p.locator(`.send-missing [data-action="packet-confirm"][data-item="${made.waiver}"][data-answer="conditional"]`).click();
    await p.locator(".packet-item", { hasText: "Said by" }).waitFor();
    assert.equal(await confirmDialog(p).count(), 0);
    await p.locator(".send-confirmed", { hasText: "the conditional waiver" }).waitFor();
    await p.locator(".send-warnings").waitFor();
    if (made.other) {
      await p.locator(`[data-form="resolve-${made.other}"]`).click();
      await p.fill(`#resolve-reason-${made.other}`, "Original at the office");
      await p.getByRole("button", { name: "Save override" }).click();
      await p.locator(".send-overrides", { hasText: "Original at the office" }).waitFor();
      assert.match(await p.locator(".override-note").first().innerText(), /^Overridden by .+ on .+: Original at the office$/);
    }
    assert.match(await p.locator(".email-body").innerText(), /Please find pay application/);
    assert.ok(await p.locator(`.send-attachments a[href$="${made.inv}.pdf"]`).count());
    await p.context().close();
  });

  await step("an unconditional waiver on a pay app is refused until an owner gives the reason", async () => {  // M14, M16
    const p = await signedInPage({ width: 1280, height: 900 });
    const job = await p.evaluate(async () => {
      const call = async (path, body) => (await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })).json();
      let st = await call("/api/jobs", { phone: "(404) 555-0100", name: "Smoke waiver porch", customer: "Smoke Waiver Client", contract_cents: 1000000, address: "5 Test St, Atlanta, GA 30307",
        approver: "Dee", ap_email: "dee@smoke.example", role: "Owner", new_customer: true });
      const id = st.jobs.find((j) => j.name === "Smoke waiver porch").id;
      const line = st.jobs.find((j) => j.id === id).sov.lines[0].id;
      st = await call(`/api/jobs/${id}/sov-progress`, { done: { [line]: "0.4" } });
      const inv = st.jobs.find((j) => j.id === id).invoices.at(-1);
      const waiver = inv.packet.find((i) => i.waiver_kind);
      for (const item of inv.packet) if (item !== waiver && !item.present) await call(`/api/jobs/${id}/resolve/${item.id}`, { reason: "smoke" });
      const res = await fetch(`/api/import/file?job=${id}&file=1&kind=waiver&waiver_kind=unconditional_progress&attach=${waiver.id}`,
        { method: "POST", headers: { "Content-Type": "application/pdf", "X-Filename": "Unconditional waiver.pdf" }, body: "%PDF-1.4 smoke" });
      // A blank file: it asks for a look (Decision 6); the person's tap says it is the unconditional one.
      await call(`/api/jobs/${id}/packet-confirm/${waiver.id}`, { looked: true, answer: "unconditional" });
      return { id, inv: inv.id, status: res.status };
    });
    assert.equal(job.status, 200);
    await p.goto(BASE + `/money/pay-app/${job.id}/${job.inv}`);
    const warn = p.locator(".unconditional-warning");
    await warn.waitFor();
    assert.match(await warn.innerText(), /An unconditional waiver is attached/);
    await p.click('.send-check [data-action="send"]');
    await confirmIfAsked(p);   // every send the page makes asks first; the server refuses this one
    await p.locator(".action-error, .refusal").first().waitFor().catch(() => {});
    assert.equal((await p.evaluate(async (id) => (await (await fetch(`/api/jobs/${id}`)).json()).job.invoices.at(-1).status, job.id)), "awaiting_approval");
    await p.fill(`#override-reason-${job.inv}`, "Client paid by wire already");
    await p.click('[data-action="send-override"]');
    await confirmIfAsked(p);
    for (let t = 0; t < 100; t++) {
      const status = await p.evaluate(async (id) => (await (await fetch(`/api/jobs/${id}`)).json()).job.invoices.at(-1).status, job.id);
      if (status === "sent") break;
      await new Promise((r) => setTimeout(r, 100));
    }
    const sent = await p.evaluate(async (id) => (await (await fetch(`/api/jobs/${id}`)).json()).job.invoices.at(-1), job.id);
    assert.equal(sent.status, "sent");
    assert.equal(sent.unconditional_override.reason, "Client paid by wire already");
    await p.context().close();
  });
}
