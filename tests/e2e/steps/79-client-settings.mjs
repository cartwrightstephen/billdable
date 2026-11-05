// Browser step for who changes a client: a Member reads the card and the server refuses a change; an
// owner has the form. Filling what is missing needs a client from older data: tests/js/client-settings.test.mjs
// and tests/test_client_settings_perm.py. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ browser, BASE, step, errors }) {
  await step("Client playbook: without the permission the card reads only, and the server refuses a change", async () => {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    const post = (path, body) => p.evaluate(async ([to, what]) => {
      const r = await fetch(to, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(what || {}) });
      return { status: r.status, data: await r.json() };
    }, [path, body]);
    const stamp = Date.now();
    await post("/api/auth/signup", { accept_terms: true, email: `clients-${stamp}@example.com`, password: "smoke test password", name: "Cora Owner", company_name: "Clients Co" });
    const made = await post("/api/payers", { name: "Holt Partners", client_type: "gc", approver: "Dee", ap_email: "dee@holt.example", phone: "(404) 555-0100" });
    assert.equal(made.status, 200, JSON.stringify(made.data));
    const id = made.data.payers[0].id;
    const card = p.locator(`[data-payer-card="${id}"]`);

    await p.goto(BASE + "/jobs/clients");
    await card.locator(":scope > summary").click();
    await card.getByRole("button", { name: "Save client" }).waitFor();

    const invite = await post("/api/team/invite", { email: `clients-m-${stamp}@example.com`, role: "member" });
    const token = invite.data.invite_path.split("#invite=")[1];
    assert.equal((await post("/api/auth/accept-invite", { accept_terms: true, token, name: "Max Member", password: "member password 1" })).status, 200);

    await p.goto(BASE + "/jobs/clients");
    await card.locator(":scope > summary").click();
    await card.locator(".client-settings-read").getByText("dee@holt.example").waitFor();
    assert.equal(await card.getByRole("button", { name: "Save client" }).count(), 0);
    assert.equal(await card.locator(`#payer-${id}-ap_email`).count(), 0, "a saved email has no box");

    const refused = await post(`/api/payers/${id}/update`, { ap_email: "other@holt.example" });
    assert.equal(refused.status, 403);
    await card.locator(".client-settings-read").getByText("Changing these takes the permission").waitFor();

    await ctx.close();
  });
}
