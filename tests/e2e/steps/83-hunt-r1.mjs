// Browser steps for the independent hunt of Round 1 and its acceptance test (Nov 6): a draw under a
// tenth of a percent billed by dollars, one help fold and no explanatory paragraph on the progress
// form on a phone, and one New job button on an empty Jobs page. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ browser, BASE, step, signedInPage, errors }) {
  await step("hunt r1: a $5 draw on a $10,000 line bills $5, and the phone's progress form has one help fold", async () => {
    const p = await signedInPage({ width: 375, height: 812 });
    const id = await p.evaluate(async () => {
      const r = await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: `Tiny draw ${Date.now()}`, address: "5 Elm St", customer: "Tiny GC", client_type: "gc", approver: "Dee", phone: "(404) 555-0100", ap_email: "dee@example.com", contract_cents: 1000000, retainage_pct: 10 }) });
      return (await r.json()).jobs.find((j) => j.name.startsWith("Tiny draw")).id;
    });
    await p.goto(`${BASE}/jobs/${id}`);
    await p.locator(".next-card .primary-action").click();
    await p.locator('[data-sovp][data-lump="1"]').waitFor();
    assert.ok((await p.locator(".help-list").count()) <= 1, "at most one What do these mean? on the page");
    assert.equal(await p.getByText("Each line is filled in", { exact: false }).count(), 0);
    await p.getByRole("button", { name: "Bill by dollars instead" }).click();
    await p.fill("#progress-draw", "5");
    await p.click('[data-action="progress"]');
    await p.waitForFunction(async (jid) => {
      const j = (await (await fetch("/api/state")).json()).jobs.find((x) => x.id === jid);
      return j.invoices.some((i) => i.gross_cents === 500);
    }, id);
    await p.context().close();
  });

  await step("hunt r1: an empty Jobs page has one New job button", async () => {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const p = await context.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    const email = `empty-${Date.now()}@hunt.example`;
    const res = await p.evaluate(async (em) => (await fetch("/api/auth/signup", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accept_terms: true, email: em, password: "smoke test password", name: "Emma Empty", company_name: "Empty Co" }) })).status, email);
    assert.equal(res, 200);
    await p.goto(BASE + "/jobs");
    await p.locator(".empty-state").waitFor();
    assert.equal(await p.getByRole("button", { name: "New job", exact: true }).count(), 1);
    await context.close();
  });
}
