// Browser steps for the tasks-daily stream: To Do, Tasks and New job record. Run by tests/e2e/smoke.mjs.
// They sign up a company of their own, so the demo's data stays as the other steps expect it.
import assert from "node:assert/strict";

export default async function ({ browser, page, BASE, step, signedInPage, confirmDialog, confirmIfAsked, errors }) {
  let ctx, p, jobId;
  // A company of its own, made by the first step that needs it.
  async function company() {
    if (p) return;
    // No service worker: a request it handles never reaches page.route, which the failed upload below needs.
    ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, serviceWorkers: "block" });
    p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    jobId = await p.evaluate(async (email) => {
      const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      await post("/api/auth/signup", { accept_terms: true, email, password: "smoke test password", name: "Tia Tasks", company_name: "Task Builders" });
      const st = await (await post("/api/jobs", { phone: "(404) 555-0100", approver: "Kim Homeowner", name: "Maple deck", address: "3 Maple St", customer: "Kim Homeowner", role: "Owner",
        ap_email: "kim@example.com", contract_cents: 900000 })).json();
      return st.jobs[0].id;
    }, `tasks-${Date.now()}@example.com`);
  }

  await step("To Do: add a task, assign a row, and Mine shows only what is mine (T3, G5)", async () => {
    await company();
    await p.goto(BASE + "/dashboard");
    await p.getByRole("heading", { name: /To Do/ }).waitFor();
    for (const head of ["Activity", "Job", "Ball in court", "Assignee", "Due"]) await p.locator(".todo-table th", { hasText: head }).first().waitFor();
    await p.locator(".action-queue").getByRole("button", { name: "Add task" }).click();
    await p.fill("#task-title", "Order the decking boards");
    await p.selectOption("#task-job", jobId);
    await p.locator('[data-action="task-add"]').click();
    const task = p.locator(".action-item", { hasText: "Order the decking boards" });
    await task.waitFor();
    assert.equal(await task.locator(".todo-assign").inputValue() !== "", true, "assigned to whoever added it");
    // The job's own row is whoever has the job: the person who set it up, until it is reassigned.
    const row = p.locator(".action-item", { hasText: "Not billed yet" });
    await p.locator('[data-action="todo-who"][data-who="mine"]').click();
    await task.waitFor();
    await row.waitFor();
    await p.locator('[data-action="todo-who"][data-who="everyone"]').click();
    const me = await task.locator(".todo-assign").inputValue();
    await row.locator(".todo-assign").selectOption(me);
    await p.getByText("Assigned.").waitFor();
    await p.locator('[data-action="todo-who"][data-who="mine"]').click();
    await p.locator(".action-item", { hasText: "Not billed yet" }).waitFor();
    // A due date set by hand beside the assignee: saved, and the row reads Overdue once it is past
    // (an overdue item shows in every period).
    const due = await p.evaluate(() => { const d = new Date(); d.setDate(d.getDate() - 1); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; });
    await row.locator(".todo-due-date").fill(due);
    await row.locator(".todo-due-date").dispatchEvent("change");
    await p.locator(".action-item.overdue", { hasText: "Not billed yet" }).waitFor();
    assert.equal(await p.locator(".action-item", { hasText: "Not billed yet" }).locator(".todo-due-date").inputValue(), due);
    // The Calendar's Tasks block lists it too.
    await p.goto(BASE + "/dashboard/calendar");
    await p.getByRole("heading", { name: "Tasks" }).waitFor();
    await p.locator(".tasks-block .due-item", { hasText: "Order the decking boards" }).waitFor();
    // Done takes it off the list.
    await p.goto(BASE + "/dashboard");
    await p.locator(".action-item", { hasText: "Order the decking boards" }).getByRole("button", { name: "Done" }).click();
    await p.locator(".action-item", { hasText: "Order the decking boards" }).waitFor({ state: "detached" });
  });

  await step("New job record: every section makes its own record, a failed upload loses nothing, and a phone fits (T11, T12)", async () => {
    await company();
    const jpg = (name) => ({ name, mimeType: "image/jpeg", buffer: Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0, 16]) });
    await p.goto(BASE + "/jobs/daily");
    await p.locator("#daily-job").waitFor();
    assert.equal(await p.inputValue("#daily-job"), jobId);
    await p.fill("#daily-crew", "3");
    await p.selectOption("#daily-weather", "Rain");
    await p.fill("#daily-notes", "Set the ledger board");
    await p.fill("#daily-delivery-supplier-0", "ABC Supply");
    await p.locator("#daily-delivery_0-files").setInputFiles(jpg("ticket.jpg"));
    await p.locator(".daily-files", { hasText: "ticket.jpg" }).waitFor();
    await p.locator("#daily-photos_0-files").setInputFiles([jpg("deck1.jpg"), jpg("deck2.jpg")]);
    await p.fill("#daily-time_sheets-who-0", "Ana's crew");
    await p.fill("#daily-time_sheets-hours-0", "24");
    await p.fill("#daily-invoices-supplier-0", "ABC Supply");
    await p.fill("#daily-inspections-who-0", "City inspector");
    await p.selectOption("#daily-other-kind-0", "insurance");
    await p.fill("#daily-other-label-0", "COI 2026");

    // The first upload fails: nothing typed is lost, and the file stays picked.
    await p.route(/\/api\/import\/file/, (route) => route.fulfill({ status: 500, contentType: "application/json", body: '{"error":"Upload failed."}' }), { times: 1 });
    await p.locator('[data-action="daily-save"]').click();
    await p.locator(".toast", { hasText: "Not saved" }).waitFor();
    assert.equal(await p.inputValue("#daily-notes"), "Set the ledger board");
    assert.equal(await p.inputValue("#daily-delivery-supplier-0"), "ABC Supply");
    await p.locator(".daily-files", { hasText: "ticket.jpg" }).waitFor();

    await p.locator('[data-action="daily-save"]').click();
    await p.locator(".saved-result", { hasText: "2 progress photos" }).waitFor();
    const st = await p.evaluate(async () => await (await fetch("/api/state")).json());
    const saved = st.daily_records.slice(-1)[0];
    assert.equal(saved.weather, "Rain");
    assert.equal(saved.crew_count, 3);
    assert.equal(saved.attachments.length, 3);
    const records = st.jobs.find((j) => j.id === jobId).records;
    const has = (label, kind) => assert.ok(records.some((r) => r.label === label && r.kind === kind), label);
    has("Delivery ticket: ABC Supply", "ticket");
    has("Progress photo: deck1.jpg", "photo");
    has("Progress photo: deck2.jpg", "photo");
    has("Time sheet: Ana's crew", "ticket");
    has("Invoice: ABC Supply", "supplier_invoice");
    has("Inspection: City inspector", "inspection");
    has("COI 2026", "insurance");
    assert.equal(records.filter((r) => r.label === "Delivery ticket: ABC Supply").length, 1, "the retry didn't send it twice");
    assert.equal(await p.inputValue("#daily-notes"), "", "the form is empty for the next one");
    assert.equal(await p.inputValue("#daily-job"), jobId, "stays on the job");

    // The Job records card lists them under their own groups and links to Import.
    await p.goto(`${BASE}/jobs/${jobId}`);
    const card = p.locator(".job-records");
    await card.locator(".record-group", { hasText: "Time sheets" }).waitFor();
    await card.locator(".record-group", { hasText: "Delivery tickets" }).waitFor();
    assert.equal(await card.getByText("Add a note to the record").count(), 0);
    assert.equal(await card.getByText("Paste an email").count(), 0);
    // Its Import link starts on this job, and the paste box files onto the job picked.
    await card.getByRole("link", { name: "Import" }).click();
    await p.locator("#paste-text").waitFor();
    assert.equal(new URL(p.url()).pathname, "/data/import");
    assert.equal(await p.inputValue("#upload-job"), jobId);
    await p.fill("#paste-text", "Gate code is 4411");
    await p.getByRole("button", { name: "Add to job" }).click();
    await p.locator(".toast", { hasText: "Added to the job records" }).waitFor();
    assert.ok((await p.evaluate(async (id) => (await (await fetch("/api/state")).json()).jobs.find((j) => j.id === id).records, jobId)).some((r) => /Gate code/.test(`${r.label} ${r.note}`)));

    // A phone: the camera inputs are there and nothing scrolls sideways.
    await p.setViewportSize({ width: 375, height: 812 });
    await p.goto(BASE + "/jobs/daily");
    await p.locator("#daily-job").waitFor();
    assert.equal(await p.getAttribute("#daily-photos_0-camera", "capture"), "environment");
    assert.equal(await p.getAttribute("#daily-delivery_0-camera", "capture"), "environment");
    for (const width of [375, 320]) {
      await p.setViewportSize({ width, height: 812 });
      const wide = await p.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      assert.equal(wide, 0, `the form is ${wide}px wider than a ${width}px phone`);
    }
    await p.setViewportSize({ width: 1280, height: 900 });
  });

  if (ctx) await ctx.close();
}
