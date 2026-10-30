// Browser steps for the foundation stream (Stephen's Oct 29 review): the menu's seven sections,
// old addresses, Back to where you came from, and boxes that take what they say. Run by
// tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ browser, page, BASE, step, signedInPage, confirmDialog, confirmIfAsked, errors }) {
  await step("the menu has seven sections; each tab's heading matches its tab", async () => {
    await page.goto(BASE + "/dashboard");
    await page.getByRole("heading", { name: "Overview" }).waitFor();
    assert.deepEqual(await page.locator(".nav > button[data-section]").allInnerTexts().then(l => l.map(s => s.split("\n")[0].trim())),
      ["Dashboard", "Jobs", "Communications", "Import/Export", "Money", "Automations", "Settings"]);
    for (const [path, tabs] of [["/jobs", ["Open jobs", "Daily record", "Job history", "Client playbook"]], ["/communications", ["Inbox", "Outbox"]],
      ["/data/import", ["Import", "Export"]], ["/dashboard", ["Overview", "Results", "Calendar"]]]) {
      await page.goto(BASE + path);
      await page.locator(".tabs .tab", { hasText: tabs[tabs.length - 1] }).waitFor();
      assert.deepEqual(await page.locator(".tabs .tab").allInnerTexts(), tabs, path);
      assert.equal(await page.locator("#view h1").first().innerText(), await page.locator(".tabs .tab.active").innerText(), `${path}: the heading is the tab's name`);
    }
    await page.goto(BASE + "/automations");
    await page.getByRole("heading", { name: "Automations" }).waitFor();
    await page.getByText("Under construction").waitFor();
  });

  await step("old addresses still open their page, at its new address", async () => {
    for (const [old, now] of [["/today", "/dashboard"], ["/today/results", "/dashboard/results"], ["/today/calendar", "/dashboard/calendar"],
      ["/today/daily", "/jobs/daily"], ["/inbox", "/communications"], ["/money/outbox", "/communications/outbox"],
      ["/settings/playbook", "/jobs/clients"], ["/settings/help", "/settings/account"]]) {
      await page.goto(BASE + old);
      await page.locator("#view h1").first().waitFor();
      assert.equal(new URL(page.url()).pathname, now, old);
    }
  });

  await step("Back returns to the page you came from: Dashboard > job > pay app > Back > Back (G3)", async () => {
    await page.goto(BASE + "/dashboard");
    await page.getByRole("heading", { name: "Overview" }).waitFor();
    await page.locator('#view a[data-action="open-job"][data-id="job-oak"]').first().click();
    await page.getByRole("heading", { name: "Oak Street kitchen" }).waitFor();
    assert.equal(await page.locator(".back").innerText(), "← Dashboard");
    await page.locator('#view [data-action="open-payapp"]').first().click();
    await page.locator(".back", { hasText: "← Oak Street kitchen" }).waitFor();
    await page.locator(".back").click();
    await page.getByRole("heading", { name: "Oak Street kitchen" }).waitFor();
    await page.locator(".back").click();
    await page.getByRole("heading", { name: "Overview" }).waitFor();
    assert.equal(new URL(page.url()).pathname, "/dashboard");
    // Opened from a link, Back goes to the page's own list.
    await page.goto(BASE + "/jobs/job-oak");
    assert.equal(await page.locator(".back").innerText(), "← Jobs");
  });

  await step("from a link, Back climbs to each page's parent and never loops (D1)", async () => {
    await page.goto(BASE + "/money/pay-app/job-oak/inv-oak-1");
    await page.locator(".back", { hasText: "← Oak Street kitchen" }).waitFor();
    await page.locator(".back").click();
    await page.getByRole("heading", { name: "Oak Street kitchen" }).waitFor();
    assert.equal(new URL(page.url()).pathname, "/jobs/job-oak");
    assert.equal(await page.locator(".back").innerText(), "← Jobs");
    await page.locator(".back").click();
    await page.getByRole("heading", { name: "Open jobs" }).waitFor();
    assert.equal(new URL(page.url()).pathname, "/jobs");
  });

  await step("phone: five sections, with Import/Export and Automations under More", async () => {
    const phone = await signedInPage({ width: 375, height: 812 });
    await phone.goto(BASE + "/dashboard");
    await phone.locator("#menu-btn").click();
    const shown = await phone.locator(".nav > button[data-section]:visible").allInnerTexts();
    assert.deepEqual(shown.map(s => s.split("\n")[0].trim()), ["Dashboard", "Jobs", "Communications", "Money", "Settings"]);
    await phone.locator(".nav-more summary").click();
    await phone.locator('.nav-more button[data-section="automations"]').click();
    await phone.getByRole("heading", { name: "Automations" }).waitFor();
    assert.equal(new URL(phone.url()).pathname, "/automations");
    await phone.context().close();
  });

  await step("the Dashboard and Results carry no notes about the app (T2, T4, T5, T7, T8)", async () => {
    await page.goto(BASE + "/dashboard");
    await page.getByRole("heading", { name: "Overview" }).waitFor();
    assert.equal(await page.locator("#view .trial-banner").count(), 0, "no trial banner on the Overview");
    await page.goto(BASE + "/dashboard/results");
    await page.locator(".metric .k", { hasText: "Days from Job Completion to Pay App Sent" }).waitFor();
    assert.deepEqual((await page.locator(".metrics").first().locator(".metric .k").allInnerTexts()).map(s => s.trim()),
      ["Days from Job Completion to Pay App Sent", "Days from Pay App Sent to Paid", "Follow-ups per pay app", "Pay app rejections by Clients"]);
    await page.getByRole("heading", { name: "Jobs completed with outstanding billings" }).waitFor();
    for (const gone of ["Nothing here is estimated", "Email me last month's summary", "Every finished job has a pay app out"]) {
      assert.equal(await page.getByText(gone).count(), 0, gone);
    }
  });

  await step("the Daily record has a Diary, and a job page opens it with that job picked (T9, T10)", async () => {
    await page.goto(BASE + "/jobs/job-oak");
    await page.getByRole("button", { name: "New daily record" }).click();
    await page.getByRole("heading", { name: "Daily record" }).waitFor();
    assert.equal(new URL(page.url()).pathname, "/jobs/daily");
    assert.equal(await page.inputValue("#daily-job"), "job-oak");
    await page.locator("label", { hasText: "Diary" }).waitFor();
  });

  await step("retainage boxes have no number spinner (J3)", async () => {
    await page.goto(BASE + "/jobs?new=1");
    const box = page.locator("#nj-retainage");
    await box.waitFor({ state: "attached" });
    assert.equal(await box.getAttribute("type"), "text");
    assert.equal(await box.getAttribute("inputmode"), "decimal");
    await page.goto(BASE + "/jobs/job-oak");
    await page.getByRole("button", { name: "Edit job" }).click();
    assert.equal(await page.locator("#ej-retainage").getAttribute("type"), "text");
    assert.equal(await page.locator('#view input[type="number"]').count(), 0);
  });
}
