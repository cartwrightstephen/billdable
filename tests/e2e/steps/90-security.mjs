// Browser steps for the Nov 27 security fixes: Settings, Company shows the file storage used (H5),
// My account lists where you're signed in with Sign out everywhere else (L2), Export offers the
// .zip with every uploaded file (L4), and a common password is refused at sign-up (L3). A company
// of its own, so the demo's data stays as the other steps expect it. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ browser, BASE, step, errors }) {
  const email = `security-${Date.now()}@example.com`;
  let ctx, p;
  async function company() {
    if (p) return;
    ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    const status = await p.evaluate(async (email) => {
      const r = await fetch("/api/auth/signup", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accept_terms: true, email, password: "smoke test password", name: "Sec Owner", company_name: "Secure Build" }) });
      return r.status;
    }, email);
    assert.equal(status, 200);
  }

  await step("Security: a common password is refused at sign-up, with the reason (L3)", async () => {
    const c = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const page = await c.newPage();
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(BASE + "/");
    const card = page.locator("#signup");
    if (!(await card.getByRole("button", { name: "Create account" }).count())) await card.locator("#auth-switch").click();
    await page.locator("#auth-name").fill("Weak Pass");
    await page.locator("#auth-email").fill(`weak-${Date.now()}@example.com`);
    await page.locator("#auth-password").fill("Password123!");
    await page.locator("#auth-terms").check();
    await card.getByRole("button", { name: "Create account" }).click();
    await card.getByText("one of the most common ones").waitFor();
    await c.close();
  });

  await step("Security: Settings, Company shows how much file storage is used (H5)", async () => {
    await company();
    await p.goto(BASE + "/settings/company");
    const box = p.locator("#storage-use");
    await box.waitFor({ state: "attached" });
    await p.locator('[data-more="danger"] summary').click();
    await p.locator("#storage-use", { hasText: /Files: \d+ MB of 1\.0 GB used/ }).waitFor();
    assert.equal(await p.locator("#storage-use progress.storage-meter").count(), 1);
  });

  await step("Security: My account lists where you're signed in, and Sign out everywhere else ends the others (L2)", async () => {
    await company();
    // A second browser signs in to the same account.
    const other = await browser.newContext();
    const op = await other.newPage();
    await op.goto(BASE + "/");
    const status = await op.evaluate(async (email) => (await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password: "smoke test password" }) })).status, email);
    assert.equal(status, 200);
    await p.goto(BASE + "/settings/account");
    const card = p.locator("#sessions-card");
    await card.locator(".pill", { hasText: "This browser" }).waitFor();
    assert.equal(await card.locator(".money-row").count(), 2);
    await card.getByRole("button", { name: "Sign out everywhere else" }).click();
    await card.getByText("Signed in on this browser only.").waitFor();
    const after = await op.evaluate(async () => (await fetch("/api/auth/me")).status);
    assert.equal(after, 401, "the other browser is signed out");
    await other.close();
  });

  await step("Security: Export offers everything with the uploaded files as a .zip (L4)", async () => {
    await company();
    await p.goto(BASE + "/data/export");
    const link = p.locator("#export-everything").getByRole("link", { name: "Everything with files (.zip)" });
    await link.waitFor();
    assert.equal(await link.getAttribute("href"), "/api/export/with-files.zip");
    const res = await p.evaluate(async () => { const r = await fetch("/api/export/with-files.zip"); return [r.status, r.headers.get("Content-Type")]; });
    assert.deepEqual(res, [200, "application/zip"]);
    await ctx.close();
  });
}
