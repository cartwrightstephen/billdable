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

  await step("the paperwork page shows what the client needs and the job record", async () => {  // M3, M5
    await page.goto(BASE + "/money/packet/job-grant");
    await page.getByRole("heading", { name: /needs to pay$/ }).waitFor();
    await page.getByRole("heading", { name: /^From the job record/ }).waitFor();
    await page.getByRole("heading", { name: "Required items" }).waitFor();
  });

  await step("an unconditional waiver on a pay app is refused until an owner gives the reason", async () => {  // M14, M16
    const p = await signedInPage({ width: 1280, height: 900 });
    const job = await p.evaluate(async () => {
      const call = async (path, body) => (await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })).json();
      let st = await call("/api/jobs", { name: "Smoke waiver porch", customer: "Smoke Waiver Client", contract_cents: 1000000, address: "5 Test St, Atlanta, GA 30307",
        approver: "Dee", ap_email: "dee@smoke.example", role: "Owner", new_customer: true });
      const id = st.jobs.find((j) => j.name === "Smoke waiver porch").id;
      st = await call(`/api/jobs/${id}/progress`, { completed_cents: 400000 });
      const inv = st.jobs.find((j) => j.id === id).invoices.at(-1);
      const waiver = inv.packet.find((i) => i.waiver_kind);
      for (const item of inv.packet) if (item !== waiver && !item.present) await call(`/api/jobs/${id}/resolve/${item.id}`, { reason: "smoke" });
      const res = await fetch(`/api/import/file?job=${id}&file=1&kind=waiver&waiver_kind=unconditional_progress&attach=${waiver.id}`,
        { method: "POST", headers: { "Content-Type": "application/pdf", "X-Filename": "Unconditional waiver.pdf" }, body: "%PDF-1.4 smoke" });
      return { id, inv: inv.id, status: res.status };
    });
    assert.equal(job.status, 200);
    await p.goto(BASE + `/money/pay-app/${job.id}/${job.inv}`);
    const warn = p.locator(".unconditional-warning");
    await warn.waitFor();
    assert.match(await warn.innerText(), /An unconditional waiver is attached/);
    await p.click('.send-check [data-action="send"]');
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
