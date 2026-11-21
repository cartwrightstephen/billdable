// Browser steps for Stephen's answers of 18 Nov 2026 on pay app approvers: no pay app is ever sent
// unapproved (with nobody named, the owner signs, then sends), approving is signing a typed full
// name that the pay app and its PDF keep, and a client whose approvers list is empty can't be sent
// to, said plainly with the way to Client playbook. A company of its own, so the demo's data stays
// as the other steps expect it. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ browser, BASE, step, confirmIfAsked, errors }) {
  let ctx, p;
  async function company() {
    if (p) return;
    ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    await p.evaluate(async (email) => {
      await fetch("/api/auth/signup", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accept_terms: true, email, password: "smoke test password", name: "Sol Signer", company_name: "Signers Build" }) });
    }, `approvals-${Date.now()}@example.com`);
  }
  const api = (path, body) => p.evaluate(async ([path, body]) => {
    const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return [r.status, await r.json()];
  }, [path, body]);
  // A job for its own client, finished, its draft's documents marked in hand: [job id, draft].
  async function readyDraft(name, customer) {
    let [status, st] = await api("/api/jobs", { phone: "(404) 555-0100", approver_confirmed: true, approver: customer, name, address: "4 Birch St",
      customer, role: "Owner", new_customer: true, ap_email: "pay@birch.example", contract_cents: 600000, retainage_pct: 0 });
    assert.equal(status, 200, JSON.stringify(st));
    const jobId = st.jobs.find(j => j.name === name).id;
    [status, st] = await api(`/api/jobs/${jobId}/done`, { notes: "" });
    assert.equal(status, 200, JSON.stringify(st));
    let draft = st.jobs.find(j => j.id === jobId).invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
    for (const item of draft.packet || []) [, st] = await api(`/api/jobs/${jobId}/resolve/${item.id}`, { reason: "In hand" });
    draft = st.jobs.find(j => j.id === jobId).invoices.find(i => i.id === draft.id);
    return [jobId, draft];
  }

  await step("Approvals: the only owner signs with a typed name and title, then sends; the pay app and its PDF keep the signature (18 Nov)", async () => {
    await company();
    const [jobId, draft] = await readyDraft("Birch porch", "Birch Homeowner");
    assert.equal(draft.status, "awaiting_approval");
    assert.equal(draft.approval.source, "owners");
    await p.goto(`${BASE}/money/pay-app/${jobId}/${draft.id}`);
    const approvals = p.locator(".send-check .payapp-approvals");
    await approvals.locator(".money-row", { hasText: "Sol Signer" }).locator(".pill", { hasText: "Waiting" }).waitFor();
    assert.equal(await p.locator('.send-check [data-action="send"][data-blocked="1"]').count(), 1, "Approve & send says it waits");
    // Unsigned, the server refuses the send.
    const [refused, out] = await api(`/api/jobs/${jobId}/send`, { reviewed: draft.email_preview.stamp });
    assert.equal(refused, 409);
    assert.match(out.error, /needs a signature from Sol Signer/);
    // A blank name is asked for under the box.
    const form = approvals.locator(".approve-sign");
    await form.locator('[data-action="payapp-approve"]').click();
    await form.locator(`#approve-${draft.id}-signature-err`, { hasText: "Type your full name to sign." }).waitFor();
    await form.locator('input[name="signature"]').fill("Sol R. Signer");
    await form.locator('input[name="title"]').fill("Owner");
    await form.locator('[data-action="payapp-approve"]').click();
    await approvals.locator(".money-row", { hasText: "Signed /s/ Sol R. Signer, Owner" }).waitFor();
    assert.equal(await p.locator('.send-check [data-action="send"][data-blocked="1"]').count(), 0, "signed: ready to send");
    await p.click('.send-check [data-action="send"]');
    await confirmIfAsked(p);
    await p.locator("h1 + .sub .pill", { hasText: "Sent" }).waitFor();
    // Frozen with it: the numbers fold names who approved it.
    await p.locator(".payapp-numbers summary").click();
    await p.locator(".payapp-numbers .money-row", { hasText: "Approved by Sol Signer" }).filter({ hasText: "Signed /s/ Sol R. Signer, Owner" }).waitFor();
    const pdf = await p.evaluate(async ([j, i]) => (await (await fetch(`/api/jobs/${j}/payapp/${i}.pdf`)).text()), [jobId, draft.id]);
    assert.match(pdf, /APPROVED BY/);
    assert.match(pdf, /\/s\/ Sol R\. Signer/);
  });

  await step("Approvals: a client whose approvers list is empty can't be sent to; the page says so and opens Client playbook (18 Nov)", async () => {
    await company();
    const [jobId, draft] = await readyDraft("Cedar shed", "Cedar Homeowner");
    const payerId = (await api(`/api/jobs/${jobId}`))[1].job.payer_id;
    let [status, st] = await api(`/api/payers/${payerId}/approvers`, { user_ids: [] });
    assert.equal(status, 200, JSON.stringify(st));
    await p.goto(`${BASE}/money/pay-app/${jobId}/${draft.id}`);
    const none = p.locator(".send-check .approvals-none");
    await none.waitFor();
    assert.match(await none.innerText(), /^Nobody is named to approve Cedar Homeowner's pay apps, so they can't be sent\. Name approvers in Client playbook\.$/);
    assert.equal(await p.locator(".send-check .approve-sign").count(), 0, "nobody to sign");
    assert.equal(await p.locator('.send-check [data-action="send"][data-blocked="1"]').count(), 1);
    [status, st] = await api(`/api/jobs/${jobId}/send`, { reviewed: draft.email_preview.stamp });
    assert.equal(status, 409);
    assert.equal(st.approvals_none, true);
    await p.locator(".send-check .payapp-approvals").getByRole("button", { name: "Open Client playbook" }).click();
    await p.waitForURL(/\/jobs\/clients/);
    // The client's card (folded until opened) says the same under Pay app approvers.
    await p.locator(".client-approvers .blocker", { hasText: "Nobody. Its pay apps can't be sent until someone is named." }).first().waitFor({ state: "attached" });
    await ctx.close();
  });
}
