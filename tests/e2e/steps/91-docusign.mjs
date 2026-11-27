// Browser steps for DocuSign before the server has its keys (founders' decision, Nov 27): Settings,
// Company always shows the DocuSign eSignature card with Connect, and "Sign with DocuSign" sits
// beside the typed-name Sign on a pay app. Pressing either says DocuSign is being switched on, with
// no error, and offers the typed name right there; nothing is marked signed. The browser suite's
// server has no DOCUSIGN_CLIENT_ID. A company of its own, so the demo's data stays as the other
// steps expect it. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ browser, BASE, step, errors }) {
  let p;
  async function company() {
    if (p) return;
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    const status = await p.evaluate(async (email) => (await fetch("/api/auth/signup", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accept_terms: true, email, password: "smoke test password", name: "Dana Signer", company_name: "Signing Build" }) })).status,
    `docusign-${Date.now()}@example.com`);
    assert.equal(status, 200);
  }
  const api = (path, body) => p.evaluate(async ([path, body]) => {
    const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return [r.status, await r.json()];
  }, [path, body]);

  await step("DocuSign: Settings, Company shows the DocuSign eSignature card; Connect says it is being switched on, no error (Nov 27)", async () => {
    await company();
    const [, st] = await api("/api/docusign/status");
    assert.equal(st.configured, false, "the suite's server has no DocuSign keys");
    await p.goto(`${BASE}/settings/company`);
    const card = p.locator(".docusign-card");
    await card.getByRole("heading", { name: "DocuSign eSignature" }).waitFor();
    assert.match(await card.innerText(), /Sign pay apps, lien waivers and change orders with DocuSign/);
    await card.getByRole("button", { name: "Connect DocuSign" }).click();
    const note = card.locator(".docusign-note[role=status]");
    await note.waitFor();
    const text = await note.innerText();
    assert.match(text, /DocuSign is being switched on for your account\. We'll email you when it's ready\./);
    assert.match(text, /sign with your typed name .* it's legally binding/);
    assert.equal(new URL(p.url()).pathname, "/settings/company", "stayed on the page");
    assert.equal(await p.locator(".toast", { hasText: /Couldn't|isn't set up/ }).count(), 0, "no error");
  });

  await step("DocuSign: Sign with DocuSign beside Sign on a pay app says so and offers the typed name, which then signs (Nov 27)", async () => {
    await company();
    let [status, st] = await api("/api/jobs", { phone: "(404) 555-0100", approver_confirmed: true, approver: "Elm Homeowner", name: "Elm porch",
      address: "4 Elm St", customer: "Elm Homeowner", role: "Owner", new_customer: true, ap_email: "pay@elm.example", contract_cents: 600000, retainage_pct: 0 });
    assert.equal(status, 200, JSON.stringify(st));
    const jobId = st.jobs.find(j => j.name === "Elm porch").id;
    [status, st] = await api(`/api/jobs/${jobId}/done`, { notes: "" });
    assert.equal(status, 200, JSON.stringify(st));
    let draft = st.jobs.find(j => j.id === jobId).invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
    for (const item of draft.packet || []) [, st] = await api(`/api/jobs/${jobId}/resolve/${item.id}`, { reason: "In hand" });
    await p.goto(`${BASE}/money/pay-app/${jobId}/${draft.id}`);
    const form = p.locator(".send-check .approve-sign");
    await form.waitFor();
    const ds = form.locator('[data-action="docusign-sign"]');
    assert.equal(await ds.innerText(), "Sign with DocuSign");
    await ds.click();
    const note = form.locator(".docusign-note[role=status]");
    await note.waitFor();
    assert.match(await note.innerText(), /being switched on for your account[\s\S]*legally binding/);
    [, st] = await api(`/api/jobs/${jobId}`);
    const still = st.job.invoices.find(i => i.id === draft.id);
    assert.ok((still.approval.waiting || []).length, "nothing was marked signed");
    assert.ok(!(st.job.audit || []).some(a => /^docusign_/.test(a.action || "")), "nothing went to DocuSign");
    await note.getByRole("button", { name: "Sign with your typed name" }).click();
    await note.waitFor({ state: "detached" });
    const box = form.locator('input[name="signature"]');
    assert.equal(await box.evaluate((el) => el === document.activeElement), true, "the name box has the cursor");
    await box.fill("Dana Signer");
    await form.locator('[data-action="payapp-approve"]').click();
    await form.waitFor({ state: "detached" });
    [, st] = await api(`/api/jobs/${jobId}`);
    assert.deepEqual(st.job.invoices.find(i => i.id === draft.id).approval.waiting, [], "signed with the typed name");
  });
}
