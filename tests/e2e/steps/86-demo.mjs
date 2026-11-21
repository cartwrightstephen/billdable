// Browser steps for the live demo, Redbud Electric (demo_seed.py; Stephen, 10 Nov, decision 11).
// The rest of the suite runs on a server holding the Hartline fixture (DURACASH_DEMO_SEED=hartline),
// so these run against a second server started without it, named by DURACASH_DEMO_URL_UNDER_TEST
// (.github/workflows/e2e.yml starts one). Without it they are skipped. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

const DEMO = process.env.DURACASH_DEMO_URL_UNDER_TEST || "";
const dollars = (t) => Number(String(t).replace(/[^0-9.-]/g, ""));

export default async function ({ browser, step, errors }) {
  if (!DEMO && browser) {
    console.log("skip  Redbud Electric demo steps: DURACASH_DEMO_URL_UNDER_TEST is not set");
    return;
  }

  async function demoPage() {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const p = await context.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(DEMO + "/");
    const status = await p.evaluate(async () => (await fetch("/api/auth/demo", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })).status);
    assert.equal(status, 200, "demo sign-in");
    return p;
  }
  const state = (p) => p.evaluate(async () => (await fetch("/api/state")).json());

  await step("Redbud demo: an electrical sub with five live jobs and a GC as the main client", async () => {
    const p = await demoPage();
    const s = await state(p);
    assert.equal(s.company.name, "Redbud Electric Co.");
    const me = await p.evaluate(async () => (await (await fetch("/api/auth/me")).json()).user);
    assert.deepEqual([me.name, me.email], ["Dana Whitfield", "owner@redbudelectric.example"]);
    assert.equal(s.jobs.filter(j => !j.closed).length, 5);
    assert.ok(s.payers.some(c => c.name === "Granite Row Builders" && c.client_type === "gc"));
    await p.goto(`${DEMO}/dashboard`);
    const tiles = p.locator(".dash-tiles");
    await tiles.waitFor();
    for (const k of ["Done, not billed", "Outstanding", "Held back, still owed"]) {
      assert.ok(dollars(await tiles.locator(".metric", { hasText: k }).locator(".v").innerText()) > 0, k);
    }
    await p.context().close();
  });

  await step("Redbud demo: the money strip and Financials show a believable small sub", async () => {
    const p = await demoPage();
    await p.goto(`${DEMO}/jobs`);
    const strip = p.locator("#money-strip .money-strip");
    await strip.waitFor();
    for (const v of await strip.locator(".strip-item").allInnerTexts()) assert.ok(dollars(v.split("\n").pop()) > 0, v);
    await p.goto(`${DEMO}/money/financials`);
    await p.locator("#fin-report").waitFor();
    const tile = (await p.locator("#fin-report .metric", { hasText: "Owed by clients" }).locator(".v").innerText()).trim();
    const total = (await p.locator("#fin-report section", { hasText: "Receivables aging" }).locator("tr.total td.num").first().innerText()).trim();
    assert.equal(total.replace(/\.00$/, ""), tile);
    assert.ok(dollars(tile) > 50000 && dollars(tile) < 150000, tile);
    await p.context().close();
  });

  await step("Redbud demo: a job's schedule of values and its job records, with the delivery ticket PDF on file", async () => {
    const p = await demoPage();
    const s = await state(p);
    const job = s.jobs.find(j => j.name === "Westside Lofts, Building B");
    await p.goto(`${DEMO}/jobs/${job.id}`);
    await p.getByText("Feeders, 4/0 copper in EMT").first().waitFor();
    await p.getByText("Branch rough-in, apartments").first().waitFor();
    const full = await p.evaluate(async (id) => (await fetch(`/api/jobs/${id}`)).json(), job.id);
    const recs = (full.job || full).records || [];
    const ticket = recs.find(r => r.ticket_type === "delivery" && (r.file_ids || []).length);
    assert.ok(ticket, "a delivery ticket with its file");
    const got = await p.evaluate(async (fid) => { const r = await fetch(`/api/files/${fid}`); return [r.status, r.headers.get("content-type"), (await r.text()).slice(0, 8)]; }, ticket.file_ids[0]);
    assert.deepEqual(got, [200, "application/pdf", "%PDF-1.4"]);
    await p.context().close();
  });

  await step("Redbud demo: Reset the demo brings back the electrical demo", async () => {
    const p = await demoPage();
    const before = await state(p);
    const res = await p.evaluate(async () => (await fetch("/api/company", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: "Changed while looking around" }) })).status);
    assert.equal(res, 200);
    await p.goto(`${DEMO}/settings/company`);
    await p.locator("details.danger-zone summary").click();
    await p.click("#reset");
    await p.locator("dialog.confirm-dialog .confirm-actions .btn:not(.secondary)").click();
    await p.waitForFunction(async () => (await (await fetch("/api/state")).json()).company.name === "Redbud Electric Co.");
    const after = await state(p);
    assert.equal(after.company.name, "Redbud Electric Co.");
    assert.deepEqual(after.jobs.map(j => j.name).sort(), before.jobs.map(j => j.name).sort());
    await p.context().close();
  });
}
