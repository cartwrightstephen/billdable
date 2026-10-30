// Browser steps for the settings-auth stream (Stephen's Oct 29 review): appearance, the idle
// sign-out warning, permission profiles, the Change password page and one Save per page. Run by
// tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ browser, page, BASE, step, signedInPage, confirmDialog, confirmIfAsked, errors }) {
  await step("Appearance: Dark and an accent apply at Save and stay after a reload (G9)", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(BASE + "/settings/account");
    await p.getByRole("heading", { name: "Appearance" }).waitFor();
    await p.locator("#app-theme").selectOption("dark");
    await p.locator("#app-accent").selectOption("green");
    await p.locator('.save-bar [data-action="save-account"]').click();
    await p.waitForFunction(() => document.documentElement.dataset.theme === "dark");
    assert.equal(await p.evaluate(() => document.documentElement.dataset.accent), "green");
    const bg = await p.evaluate(() => getComputedStyle(document.body).backgroundColor);
    await p.reload();
    await p.getByRole("heading", { name: "My account" }).waitFor();
    assert.equal(await p.evaluate(() => document.documentElement.dataset.theme), "dark", "kept after a reload");
    assert.equal(await p.evaluate(() => getComputedStyle(document.body).backgroundColor), bg);
    assert.equal(await p.locator("#app-theme").inputValue(), "dark");
    await p.locator("#app-theme").selectOption("light");
    await p.locator('.save-bar [data-action="save-account"]').click();
    await p.waitForFunction(() => document.documentElement.dataset.theme === "light");
    await p.context().close();
  });

  await step("two minutes before the idle sign-out, a warning offers Stay signed in (G2)", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.clock.install();
    await p.goto(BASE + "/dashboard");
    await p.getByRole("heading", { name: "Overview" }).waitFor();
    await p.clock.fastForward("58:30");
    await p.locator("#idle-warning").waitFor();
    assert.match(await p.locator("#idle-warning").innerText(), /Signing you out in 2 minutes/);
    await p.locator("#idle-stay").click();
    await p.locator("#idle-warning").waitFor({ state: "detached" });
    await p.context().close();
  });

  await step("Team lists the permission profiles; Owner holds every permission (S6)", async () => {
    await page.goto(BASE + "/settings/team");
    await page.getByRole("heading", { name: "Permission profiles" }).waitFor();
    const heads = await page.locator(".profiles-table th").allInnerTexts();
    assert.deepEqual(heads.slice(0, 3), ["Permission", "Owner", "Member"]);
    const ownerColumn = await page.locator(".profiles-table tr td:nth-child(2)").allInnerTexts();
    assert.ok(ownerColumn.filter(t => t.trim()).every(t => t.trim() === "Yes"), ownerColumn.join());
    assert.equal(await page.locator('#view [data-action="delete-account"]').count(), 0);
  });

  await step("Company has one Save at the bottom and no Members can send switch (S1, S6)", async () => {
    await page.goto(BASE + "/settings/company");
    await page.getByRole("heading", { name: "Company details" }).waitFor();
    assert.equal(await page.locator('[data-action="save-company"]').count(), 1);
    assert.equal(await page.getByText("Members can send").count(), 0);
    assert.equal(await page.getByText("Delete the company").count(), 0, "deletion is on its own page, for the Company Admin");
  });

  await step("Change password opens its own page under My account (S2)", async () => {
    await page.goto(BASE + "/settings/account/password");
    await page.getByRole("heading", { name: "Change password" }).waitFor();
    assert.equal(await page.locator(".back").innerText(), "← My account");
    assert.equal(await page.locator('.tabs .tab.active').innerText(), "My account");
  });
}
