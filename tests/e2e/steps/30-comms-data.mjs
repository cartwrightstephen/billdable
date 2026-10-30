// Browser steps for the comms-data: Communications and Import/Export stream. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ browser, page, BASE, step, signedInPage, confirmDialog, confirmIfAsked, errors }) {
  // A fresh company, signed in in its own browser: these steps add and dismiss things.
  async function newCompany(prefix) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    await p.evaluate(async (email) => {
      const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      await post("/api/auth/signup", { accept_terms: true, email, password: "smoke test password", name: "Cora Comms", company_name: "Comms Builders" });
      await post("/api/jobs", { name: "Birch deck", address: "5 Birch Rd", customer: "Northline GC", role: "GC", ap_email: "ap@northline.example", contract_cents: 2000000 });
    }, `${prefix}-${Date.now()}@example.com`);
    return { ctx, p };
  }

  await step("Import: notes pasted there wait in the Inbox, and Everything received lists them with a filter", async () => {  // C1, I1
    const { ctx, p } = await newCompany("received");
    await p.goto(BASE + "/data/import");
    await p.locator("#paste-text").fill("Railing posts set on the Birch deck today.");
    await p.getByRole("button", { name: "Add to Inbox" }).click();
    await p.getByText("Added to the Inbox.").waitFor();
    await p.locator("#paste-text").fill("Gate code 4411");
    await p.getByRole("button", { name: "Add to Inbox" }).click();
    await p.goto(BASE + "/communications");
    const gate = p.locator(".inbox-item", { hasText: "Gate code 4411" });
    await gate.getByRole("button", { name: "Dismiss" }).click();
    await gate.waitFor({ state: "detached" });
    const received = p.locator(".card.received");
    await received.locator("tr", { hasText: "Gate code 4411" }).getByText("Dismissed").waitFor();
    await received.locator("tr", { hasText: "Railing posts set" }).getByText("Waiting (suggested: Birch deck)").waitFor();
    await p.selectOption("#received-status", "dismissed");
    await received.locator("tr", { hasText: "Railing posts set" }).waitFor({ state: "detached" });
    const row = received.locator("tr", { hasText: "Gate code 4411" });
    await row.getByRole("button", { name: "Undo" }).click();
    await p.locator(".inbox-item", { hasText: "Gate code 4411" }).waitFor();
    await ctx.close();
  });

  await step("Import: a QuickBooks payments CSV is read there; Payments links to it", async () => {  // I1
    const { ctx, p } = await newCompany("qbo-import");
    await p.goto(BASE + "/money/payments");
    await p.locator("#view").getByRole("link", { name: "Import" }).click();
    await p.waitForURL(/\/data\/import$/);
    await p.locator("#qbo-csv").fill('Date,Transaction Type,Num,Name,Memo/Description,Amount\n10/09/2026,Payment,4417,Northline GC,,"1,000.00"\n');
    await p.getByRole("button", { name: "Read payments" }).click();
    await p.locator("#qbo-import table").getByText("4417").waitFor();
    await p.locator("#qbo-import").getByRole("button", { name: "Cancel" }).click();
    await p.locator("#qbo-csv").waitFor();
    await ctx.close();
  });

  await step("Outbox: an email lists its attachments and prints on its own", async () => {  // M27
    await page.goto(BASE + "/communications/outbox");
    const first = page.locator("#view tr").nth(1);
    await first.waitFor();
    await page.evaluate(() => { window.print = () => { window.__printed = (window.__printed || 0) + 1; }; });
    await first.getByRole("button", { name: "Print" }).click();
    assert.equal(await page.evaluate(() => window.__printed), 1);
    assert.ok(await page.evaluate(() => document.body.classList.contains("printing")));
    const sheet = await page.locator("#print-sheet").textContent();
    assert.match(sheet, /To[\s\S]*When/);
    await page.evaluate(() => { window.dispatchEvent(new Event("afterprint")); });
    assert.ok(!(await page.evaluate(() => document.body.classList.contains("printing"))));
  });

  await step("Export: an owner downloads everything; a member sees the pay apps and the calendar but no Everything button", async () => {  // I1
    const { ctx, p } = await newCompany("export");
    await p.goto(BASE + "/data/export");
    await p.locator("#export-everything").getByRole("link", { name: "Download everything" }).waitFor();
    await p.locator("#export-payapps").getByText("No pay apps sent yet").waitFor();
    const token = await p.evaluate(async (email) => {
      const out = await (await fetch("/api/team/invite", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, role: "member" }) })).json();
      return out.invite_path.split("#invite=")[1];
    }, `export-member-${Date.now()}@example.com`);
    const status = await p.evaluate(async (t) => (await fetch("/api/auth/accept-invite", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accept_terms: true, token: t, name: "Max Member", password: "member password 1" }) })).status, token);
    assert.equal(status, 200, "member joined");
    await p.goto(BASE + "/data/export");
    await p.locator("#export-everything").getByText("An owner can download everything").waitFor();
    assert.equal(await p.locator('#view a[href="/api/export"]').count(), 0, "no Everything button for a member");
    assert.equal(await p.evaluate(async () => (await fetch("/api/export")).status), 403, "and the server refuses it");
    await p.locator("#export-calendar #cal-url").filter({ hasText: /\/api\/calendar\// }).waitFor();
    await ctx.close();
  });
}
