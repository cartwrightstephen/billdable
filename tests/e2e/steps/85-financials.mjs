// Browser steps for Stephen's Nov 13 request: the company's main figures in a strip on every page,
// and Money > Financials with what a bank asks for. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ BASE, step, signedInPage }) {
  await step("The money strip shows on a job page and opens Unpaid; the Overview leaves it to its tiles", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(`${BASE}/jobs`);
    const strip = p.locator("#money-strip .money-strip");
    await strip.waitFor();
    assert.deepEqual((await strip.locator(".strip-k").allInnerTexts()).map(t => t.trim()), ["Owed to you", "Done, not billed", "Retainage held", "Backlog"]);
    await strip.locator(".strip-item", { hasText: "Owed to you" }).click();
    await p.waitForURL(/\/money\/collections$/);
    await p.goto(`${BASE}/dashboard`);
    await p.locator(".dash-tiles").waitFor();
    assert.equal(await p.locator("#money-strip .money-strip").count(), 0);
    await p.context().close();
  });

  await step("Money > Financials: the tiles, the monthly chart, aging that adds up, the CSV and the printable copy", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(`${BASE}/money`);
    await p.click('.tabs [data-tab="financials"]');
    await p.waitForURL(/\/money\/financials$/);
    await p.locator("#fin-report").waitFor();
    const keys = (await p.locator("#fin-report .metric .k").allInnerTexts()).map(t => t.trim().replace(/\s*i$/, ""));   // a term's help icon follows
    assert.deepEqual(keys.slice(0, 4), ["Owed by clients", "Retainage held", "Backlog", "Done, not billed"]);
    assert.equal(await p.locator("#fin-report .fin-chart g").count(), 12, "twelve months of bars");
    // The aging total is the Owed by clients tile, to the dollar.
    const tile = (await p.locator("#fin-report .metric", { hasText: "Owed by clients" }).locator(".v").innerText()).trim();
    const total = (await p.locator("#fin-report section", { hasText: "Receivables aging" }).locator("tr.total td.num").first().innerText()).trim();
    assert.equal(total.replace(/\.00$/, ""), tile);
    const csv = await p.evaluate(async () => { const r = await fetch("/api/financials.csv"); return [r.status, (await r.text()).split("\n")[2]]; });
    assert.deepEqual(csv, [200, "Summary,Amount\r"]);
    await p.evaluate(() => { window.print = () => { window.__printed = true; }; });
    await p.click('[data-action="fin-print"]');
    assert.equal(await p.evaluate(() => window.__printed), true);
    assert.match(await p.locator("#print-sheet h1").innerText(), /financial summary$/);
    await p.context().close();
  });
}
