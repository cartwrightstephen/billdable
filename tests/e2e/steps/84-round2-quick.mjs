// Browser steps for Stephen's 10 Nov to-dos built before the Thursday merge: Results showing on the
// Dashboard, % after retainage, an Other client type, no example numbers in the boxes, no lump-sum
// line text, and a ZIP from another state questioned. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ BASE, step, signedInPage }) {
  await step("Results shows on the Dashboard, open, for a company with a job (to-do 4)", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(`${BASE}/dashboard`);
    await p.locator("details#results[open]").waitFor();
    await p.locator("#results .metric .k", { hasText: "Days from pay app sent to paid" }).waitFor();
    await p.context().close();
  });

  await step("New job: Retainage [ ] %, an Other client type, no example numbers, no lump-sum text, and a ZIP from another state questioned", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(`${BASE}/jobs`);
    await p.click('[data-action="toggle-form"][data-form="newjob"]');
    await p.click('[data-action="nj-client-mode"][data-mode="new"]');
    // To-do 6: the % sign after the box, and the label without it.
    assert.match((await p.locator('label[for="nj-retainage"]').innerText()).trim(), /^Retainage\s*i?$/);   // its help icon follows
    assert.equal(await p.locator("#nj-retainage + .suffix").innerText(), "%");
    // To-do 7: Other is a client type.
    assert.ok((await p.locator("#nj-client-type option").allInnerTexts()).includes("Other"));
    // To-do 8: no gray example numbers.
    const examples = await p.locator('[data-form-key="newjob"] input[placeholder]').evaluateAll((els) => els.map((e) => e.placeholder).filter((t) => /^[\d.,() -]+$|^e\.g\. \d/.test(t)));
    assert.deepEqual(examples, []);
    // To-do 9: no "One lump-sum line for the contract amount".
    assert.equal((await p.locator(".nj-sov > summary").innerText()).trim(), "Schedule of values");
    // To-do 5: a ZIP from another state is questioned when the box is left, and the question goes when it fits.
    await p.selectOption("#nj-state", "FL");
    await p.fill("#nj-zip", "30303");
    await p.press("#nj-zip", "Tab");
    await p.locator("#nj-zip-ask", { hasText: "ZIP 30303 is in Georgia, not Florida." }).waitFor();
    await p.selectOption("#nj-state", "GA");
    assert.equal(await p.locator("#nj-zip-ask").innerText(), "", "the question goes once the state fits");
    await p.context().close();
  });
}
