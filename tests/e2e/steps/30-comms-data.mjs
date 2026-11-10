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
      await post("/api/jobs", { phone: "(404) 555-0100", approver_confirmed: true, approver: "Northline GC", name: "Birch deck", address: "5 Birch Rd", customer: "Northline GC", role: "GC", ap_email: "ap@northline.example", contract_cents: 2000000 });
    }, `${prefix}-${Date.now()}@example.com`);
    return { ctx, p };
  }

  await step("Import: notes pasted there wait in the Inbox, and Everything received lists them with a filter", async () => {  // C1, I1
    const { ctx, p } = await newCompany("received");
    await p.goto(BASE + "/data/import");
    await p.selectOption("#upload-job", "");   // Not sure, file later: it waits in the Inbox
    await p.locator("#paste-text").fill("Railing posts set on the Birch deck today.");
    await p.getByRole("button", { name: "Add to Inbox" }).click();
    await p.getByText("Added to the Inbox.").waitFor();
    await p.locator("#paste-text").fill("Gate code 4411");
    await p.getByRole("button", { name: "Add to Inbox" }).click();
    // The toast goes to the Inbox (round 1, item 20).
    await p.locator("#toast").getByRole("button", { name: "Open the Inbox" }).click();
    await p.waitForURL(/\/communications$/);
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

  await step("Import: a QuickBooks payments CSV is read there; Pay apps' Payments filter links to it", async () => {  // I1, round 1 item 19
    const { ctx, p } = await newCompany("qbo-import");
    await p.goto(BASE + "/money/payments");
    await p.locator('[data-action="payapp-filter"][data-filter="payments"][aria-pressed="true"]').waitFor();
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

  await step("Print from dark mode is black on white (D7)", async () => {
    const dark = await signedInPage({ width: 1280, height: 900 });
    await dark.emulateMedia({ colorScheme: "dark" });
    await dark.goto(BASE + "/communications/outbox");
    await dark.locator("#view tr").nth(1).waitFor();
    await dark.evaluate(() => { window.print = () => {}; });
    await dark.locator("#view tr").nth(1).getByRole("button", { name: "Print" }).click();
    await dark.emulateMedia({ media: "print", colorScheme: "dark" });
    const [ink, sub, paper] = await dark.evaluate(() => [getComputedStyle(document.querySelector(".print-sheet")).color,
      getComputedStyle(document.querySelector(".print-sheet .print-body") || document.querySelector(".print-sheet")).color,
      getComputedStyle(document.body).backgroundColor]);
    assert.deepEqual([ink, sub, paper], ["rgb(0, 0, 0)", "rgb(0, 0, 0)", "rgb(255, 255, 255)"]);
    await dark.context().close();
  });

  await step("Everything received on a phone: each filter's label stays with its box (D5)", async () => {
    const phone = await signedInPage({ width: 375, height: 812 });
    await phone.goto(BASE + "/communications");
    await phone.locator("#received-sort").waitFor();
    // The list's first page arrives a moment later and redraws the filters: measure the settled page.
    await phone.waitForLoadState("networkidle");
    for (const name of ["source", "status", "job", "sort"]) {
      const label = await phone.locator(`label[for="received-${name}"]`).boundingBox();
      const box = await phone.locator(`#received-${name}`).boundingBox();
      const mid = label.y + label.height / 2;
      assert.ok(mid > box.y && mid < box.y + box.height, `${name}: the label sits on its box's row`);
      assert.ok(label.x < box.x && box.x + box.width <= 375, `${name}: label, then box, inside the screen`);
    }
    await phone.context().close();
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

  await step("Import quantities on the job's schedule card: a CSV file shows row by row, then goes onto the schedule of values", async () => {  // M1, round 1 item 19
    const { ctx, p } = await newCompany("qty");
    const jobId = await p.evaluate(async () => {
      const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const st = await (await post("/api/jobs", { phone: "(404) 555-0100", approver_confirmed: true, approver: "Park GC", name: "Elm wiring", address: "9 Elm St", customer: "Park GC", role: "GC",
        ap_email: "ap@park.example", contract_cents: 3450000 })).json();
      const id = st.jobs.find((j) => j.name === "Elm wiring").id;
      await post(`/api/jobs/${id}/sov`, { op_pct: "15", lines: [
        { item: "1", description: "Rough-in wiring", quantity: "400", unit: "ea", rate: "45.00" },
        { item: "2", description: "Panel and feeders", quantity: "1", unit: "LS", rate: "12000" },
      ] });
      return id;
    });
    await p.goto(BASE + "/data/import");
    await p.getByRole("heading", { name: "Import" }).waitFor();
    assert.equal(await p.locator("#qty-import").count(), 0, "Import has no Quantities card");
    await p.goto(BASE + `/jobs/${jobId}`);
    await p.locator("#sov-card").getByRole("button", { name: "Import quantities" }).click();
    const card = p.locator("#qty-import");
    await card.getByRole("heading", { name: "Import quantities" }).waitFor();
    const [chooser] = await Promise.all([p.waitForEvent("filechooser"), card.getByRole("button", { name: "Choose the file" }).click()]);
    await chooser.setFiles({ name: "foreman-log.csv", mimeType: "text/csv",
      buffer: Buffer.from("Item,Description,Qty this period\n1,Rough-in wiring,30\n2,Panel and feeders,25%\n9,Gutters,4\n") });
    const table = card.locator("table.qty-preview");
    await table.waitFor();
    assert.match(await table.locator("tr", { hasText: "1. Rough-in wiring" }).innerText(), /0 ea\s+30 ea\s+30 ea\s+400 ea/);
    await table.locator("tr.qty-problem", { hasText: "Gutters" }).waitFor();
    const before = await p.evaluate(async (id) => (await (await fetch("/api/state")).json()).jobs.find((j) => j.id === id).invoices.length, jobId);
    assert.equal(before, 0, "the preview changes nothing");
    await card.getByRole("button", { name: "Add 2 to the schedule of values" }).click();
    await card.getByText(/2 lines added\./).waitFor();
    await card.locator(".qty-result").getByText(/Row 4 \(Gutters\)/).waitFor();
    const job = await p.evaluate(async (id) => (await (await fetch(`/api/jobs/${id}`)).json()), jobId);
    const j = job.job || job;
    assert.deepEqual(j.sov.lines.map((l) => l.field_milli), [30000, 250]);
    assert.equal(j.invoices.length, 0, "only Progress pay app makes a draft");
    await ctx.close();
  });

  await step("Export: jobs, pay apps, payments and change orders download as CSV files", async () => {  // I1
    const { ctx, p } = await newCompany("csv");
    await p.goto(BASE + "/data/export");
    const lists = p.locator("#export-lists");
    const [download] = await Promise.all([p.waitForEvent("download"), lists.getByRole("link", { name: "Jobs" }).click()]);
    assert.match(download.suggestedFilename(), /^duracash-jobs-\d{8}\.csv$/);
    const files = await p.evaluate(async () => {
      const out = {};
      for (const kind of ["jobs", "payapps", "payments", "change-orders"]) {
        const res = await fetch(`/api/export/${kind}.csv`);
        out[kind] = [res.status, (await res.text()).replace(/^\ufeff/, "").split("\r\n")[0]];
      }
      return out;
    });
    assert.equal(files.jobs[0], 200);
    assert.match(files.jobs[1], /^Job,Code,Client,Address,Contract,/);
    assert.match(files.payapps[1], /^Number,Job,Client,Period to,Amount,Retainage,Status/);
    assert.match(files.payments[1], /^Date,Job,Client,Pay app,For,Amount,Reference$/);
    assert.match(files["change-orders"][1], /^Number,Job,Title,Reason,Amount,Status,Signed on,On a pay app$/);
    await ctx.close();
  });

  await step("Reminders start in the app, live on My account under Notifications, and a fired one is a To do row that Open reads", async () => {  // round 1, item 19
    const { ctx, p } = await newCompany("reminders");
    // Work on a job record that no change order covers: a reminder the job's own To do row doesn't say.
    await p.evaluate(async () => {
      const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const j = (await (await fetch("/api/state")).json()).jobs[0];
      await post(`/api/jobs/${j.id}/daily-record`, { notes: "Hall outlets", sections: { co_work: [{ description: "Two extra outlets in the hall" }] } });
    });
    await p.goto(BASE + "/automations");
    await p.getByRole("heading", { name: "Notifications" }).waitFor();
    assert.equal(new URL(p.url()).pathname, "/settings/account");
    // The card says Loading… under its heading until the reminders arrive: count once the boxes are in.
    await p.locator('#notifications input[data-reminder]').first().waitFor();
    assert.equal(await p.locator('input[data-reminder][value="app"]:checked').count(), 3, "in the app, from the start");
    assert.equal(await p.locator('input[data-reminder][value="email"]:checked, input[data-reminder][value="text"]:checked').count(), 0);
    // No phone yet: Text is refused under its box, and nothing is saved.
    const box = (kind, channel) => p.locator(`input[data-reminder="${kind}"][value="${channel}"]`);
    await box("missing", "text").check();
    await p.locator(".save-bar").getByRole("button", { name: "Save" }).click();
    await p.locator("#reminder-missing-err").getByText("agree to texts", { exact: false }).waitFor();
    await box("missing", "text").uncheck();
    await p.goto(BASE + "/dashboard");
    const row = p.locator(".action-item", { hasText: "Two extra outlets in the hall" });
    await row.locator(".pill", { hasText: "Reminder" }).waitFor();
    await row.getByRole("button", { name: "Open", exact: true }).click();
    await p.locator("#sov-card").waitFor();
    await p.goto(BASE + "/dashboard");
    await p.getByRole("heading", { name: /To do/ }).waitFor();
    assert.equal(await p.locator(".action-item", { hasText: "Two extra outlets in the hall" }).count(), 0, "read once opened");
    await ctx.close();
  });

  await step("My account: a mobile number is kept, and Text can't be picked until the number is confirmed", async () => {
    const { ctx, p } = await newCompany("texts");
    await p.goto(BASE + "/settings/account");
    await p.locator("#my-phone").fill("555-01");
    await p.locator(".save-bar").getByRole("button", { name: "Save" }).click();
    await p.locator("#my-phone-err").filter({ hasText: /\S/ }).waitFor();
    await p.locator("#my-phone").fill("(404) 555-0142");
    await p.locator(".save-bar").getByRole("button", { name: "Save" }).click();
    await p.getByText("Saved", { exact: true }).first().waitFor();
    await p.reload();
    assert.equal(await p.locator("#my-phone").inputValue(), "(404) 555-0142");
    assert.equal(await p.locator("#my-sms-opt-in").isChecked(), false);
    await p.goto(BASE + "/settings/account");
    await p.locator('input[data-reminder="dates"][value="text"]').check();
    await p.locator(".save-bar").getByRole("button", { name: "Save" }).click();
    await p.getByText("agree to texts", { exact: false }).first().waitFor();
    await ctx.close();
  });

  await step("My account: texts turn on only once a code texted to the number comes back", async () => {
    const { ctx, p } = await newCompany("smscode");
    await p.goto(BASE + "/settings/account");
    await p.locator("#my-phone").waitFor();
    // This server has no Twilio, so it says the number can't be confirmed and saves nothing.
    await p.fill("#my-phone", "(404) 555-0143");
    await p.locator("#my-sms-opt-in").check();
    await p.locator('[data-action="save-account"]').click();
    await p.locator("#my-phone-err", { hasText: "can't be confirmed" }).waitFor();
    assert.equal(await p.locator("#my-sms-code").count(), 0);
    // As a server with Twilio answers (the whole path is tested against a stand-in in test_sms_proof.py).
    await p.evaluate(() => {
      const real = window.fetch;
      const answer = (status, body) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
      window.fetch = async (path, options) => {
        if (path === "/api/auth/sms") {
          const user = (await (await real("/api/auth/me")).json()).user;
          return answer(200, { user, code_sent: true, sent_to: "+14045550143" });
        }
        if (path === "/api/auth/sms/confirm") {
          const { code } = JSON.parse(options.body);
          if (code !== "123456") return answer(409, { error: "That code didn't match, or it has expired. Ask for a new one.", fields: { sms_code: "That code didn't match, or it has expired. Ask for a new one." } });
          const user = (await (await real("/api/auth/me")).json()).user;
          return answer(200, { user: { ...user, phone: "+14045550143", sms_opt_in: true } });
        }
        return real(path, options);
      };
    });
    await p.locator('[data-action="save-account"]').click();
    await p.locator("#my-sms-code").waitFor();
    assert.equal(await p.inputValue("#my-phone"), "(404) 555-0143");
    await p.fill("#my-sms-code", "000000");
    await p.locator('[data-action="sms-confirm"]').click();
    await p.locator("#my-sms-code-err", { hasText: "didn't match" }).waitFor();
    await p.fill("#my-sms-code", "123456");
    await p.locator('[data-action="sms-confirm"]').click();
    await p.locator("#my-sms-code").waitFor({ state: "detached" });
    assert.equal(await p.inputValue("#my-phone"), "(404) 555-0143");
    assert.ok(await p.locator("#my-sms-opt-in").isChecked());
    await ctx.close();
  });

  await step("A text to Duracash waits in the Inbox saying who sent it, and is filed to a job with a chosen kind", async () => {
    const { ctx, p } = await newCompany("textin");
    await p.goto(BASE + "/");
    await p.evaluate(async () => {
      const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      await post("/api/auth/sms", { phone: "(404) 555-0143", sms_opt_in: false });
      const settings = await (await fetch("/api/import/settings")).json();
      const path = "/api/inbound/sms/" + settings.sms_webhook.split("/").pop();
      const send = (From, Body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ From, Body }) });
      await send("+14045550143", "Footings poured, tickets to follow");
      await send("+14045550144", "Who is this for");
      await send("+14045550143", "STOP");
    });
    await p.goto(BASE + "/communications");
    const item = p.locator(".inbox-item", { hasText: "Footings poured" });
    await item.getByText("from Cora Comms (+14045550143)").waitFor();
    await p.locator(".inbox-item", { hasText: "Who is this for" }).getByText("from Unknown sender (+14045550144)").waitFor();
    assert.equal(await p.locator(".inbox-item", { hasText: "STOP" }).count(), 0, "STOP isn't a message to file");
    const id = await item.getAttribute("data-item");
    await p.locator(`#inb-kind-${id}`).selectOption("ticket:delivery");
    await item.getByRole("button", { name: "Add to job" }).click();
    await item.waitFor({ state: "detached" });
    await ctx.close();
  });
}
