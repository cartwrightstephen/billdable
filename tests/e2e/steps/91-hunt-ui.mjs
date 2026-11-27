// Browser steps for the Nov 27 UI hunt: every page walked at desktop, iPad and phone widths.
// Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ step, signedInPage, BASE }) {
  await step("hunt ui: the phone's What do these mean? stays inside the page after a job record is saved", async () => {
    const p = await signedInPage({ width: 375, height: 812 });
    await p.goto(`${BASE}/jobs/daily`);
    await p.locator("#daily-job").selectOption("job-grant");
    await p.fill("#daily-notes", "Hunt: hung drywall");
    await p.click('[data-action="daily-save"]');
    await p.locator("#toast", { hasText: "Job record saved" }).waitFor();
    const where = await p.evaluate(() => [...document.querySelectorAll(".help-list")].map(h => !!h.closest("#view")));
    assert.ok(where.length <= 1, "one list at most");
    assert.ok(where.every(Boolean), "the list is in the page, not after the menu");
    await p.goto(`${BASE}/dashboard`);
    await p.goto(`${BASE}/jobs/daily`);
    assert.ok(await p.evaluate(() => [...document.querySelectorAll(".help-list")].every(h => h.closest("#view"))));
    await p.context().close();
  });

  await step("hunt ui: Remove on a time sheet row keeps the keyboard focus in the time sheet", async () => {
    const p = await signedInPage({ width: 1024, height: 768 });
    await p.goto(`${BASE}/jobs/daily`);
    await p.locator('[data-action="daily-more"][data-kind="crew"]').focus();
    await p.keyboard.press("Enter");
    await p.locator('[data-action="daily-row-remove"][data-kind="crew"][data-n="1"]').focus();
    await p.keyboard.press("Enter");
    const at = await p.evaluate(() => document.activeElement && document.activeElement.dataset.action);
    assert.equal(at, "daily-row-remove");
    await p.context().close();
  });
}
