// Browser steps for round 1 of the Nov 5 audits, the pay app part: on a phone, what is missing sits
// in Review & send above an Approve & send that stays on and says why in place; the numbers fold
// under one line; a photo added to a waiver item is in with one tap that says which waiver; a GC job at 0% retainage is named;
// the Activity log can leave out the app's own entries. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ BASE, step, signedInPage, confirmDialog }) {
  const call = (p, path, body) => p.evaluate(async ([to, what]) => {
    const r = await fetch(to, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(what || {}) });
    return { status: r.status, data: await r.json() };
  }, [path, body]);
  const read = (p, id) => p.evaluate(async (jobId) => (await (await fetch(`/api/jobs/${jobId}`)).json()).job, id);

  await step("round 1: a GC pay app on a phone puts what is missing above Approve & send, which says why in place", async () => {
    const p = await signedInPage({ width: 375, height: 812 });
    const st = (await call(p, "/api/jobs", { name: "Smoke R1 GC", customer: "Smoke R1 GC client", client_type: "gc", contract_cents: 4000000, retainage_pct: 0,
      address: "7 Test St, Atlanta, GA 30307", approver: "Dee", phone: "(404) 555-0100", ap_email: "dee@r1.example", new_customer: true })).data;
    const id = st.jobs.find((j) => j.name === "Smoke R1 GC").id;
    const line = (await read(p, id)).sov.lines[0].id;
    await call(p, `/api/jobs/${id}/sov-progress`, { done: { [line]: "0.25" } });
    const inv = (await read(p, id)).invoices.at(-1);
    await p.goto(`${BASE}/money/pay-app/${id}/${inv.id}`);
    const review = p.locator(".send-check");
    await review.locator(".send-missing .packet-item.absent").first().waitFor();
    const send = review.locator('[data-action="send"]');
    assert.equal(await send.isDisabled(), false, "Approve & send stays on");
    const missingTop = await review.locator(".send-missing").evaluate((el) => el.getBoundingClientRect().top);
    const sendTop = await send.evaluate((el) => el.getBoundingClientRect().top);
    assert.ok(missingTop < sendTop, "the missing items are above the button");
    await review.locator(".retainage-zero", { hasText: "usually holds 10%" }).waitFor();
    await send.click();
    // It asks first even so (Round 1 hunt): the server says why it can't go.
    await confirmDialog(p).waitFor();
    await confirmDialog(p).locator(".confirm-actions .btn:not(.secondary)").click();
    await p.locator(".action-error", { hasText: "Pay app blocked" }).waitFor();
    // The numbers fold under one line.
    const fold = p.locator(".payapp-numbers");
    assert.equal(await fold.getAttribute("open"), null);
    assert.match(await fold.locator("> summary").innerText(), /^Show the numbers · \$[\d,.]+ due · \d+ lines?$/);
    // A photo added to the missing waiver is in, with no second question.
    const item = inv.packet.find((i) => i.waiver_kind);
    const [chooser] = await Promise.all([p.waitForEvent("filechooser"),
      p.locator(`#packet-item-${item.id} [data-action="pick-files"]:not([data-capture])`).click()]);
    await chooser.setFiles({ name: "signed-waiver.jpg", mimeType: "image/jpeg", buffer: Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0, 16, 74, 70, 73, 70]) });
    // On a waiver item any file, a photo too, takes one tap that says which waiver it is (the Nov 6
    // redesign): Conditional is filled in, since that is the waiver the item needs.
    await p.locator(`#packet-item-${item.id}`, { hasText: "Which waiver is this?" }).waitFor();
    const look = p.locator(`#packet-item-${item.id} [data-action="packet-confirm"][data-answer="conditional"][aria-current="true"]`);
    await look.waitFor();
    await look.click();
    await p.locator(`#packet-item-${item.id}`, { hasText: "Said by" }).waitFor();
    assert.equal(await confirmDialog(p).count(), 0);
    assert.equal((await read(p, id)).invoices.at(-1).packet.find((i) => i.id === item.id).present, true);
    await p.context().close();
  });

  await step("round 1: the Activity log can leave out the app's own entries", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(`${BASE}/settings/audit`);
    await p.getByRole("heading", { name: "Activity log" }).waitFor();
    await p.locator('[data-action="audit-who"][data-who="people"]').click();
    await p.locator('[data-action="audit-who"][data-who="people"][aria-pressed="true"]').waitFor();
    const page = await p.evaluate(async () => (await (await fetch("/api/audit?offset=0&limit=200&who=people")).json()).entries);
    assert.ok(page.every((e) => e.actor !== "duracash"));
    await p.context().close();
  });
}
