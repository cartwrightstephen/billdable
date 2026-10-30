// Browser steps for the tasks-daily stream: To Do, Tasks and the Daily record. Run by tests/e2e/smoke.mjs.
// They sign up a company of their own, so the demo's data stays as the other steps expect it.
import assert from "node:assert/strict";

export default async function ({ browser, page, BASE, step, signedInPage, confirmDialog, confirmIfAsked, errors }) {
  let ctx, p, jobId;
  // A company of its own, made by the first step that needs it.
  async function company() {
    if (p) return;
    ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    jobId = await p.evaluate(async (email) => {
      const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      await post("/api/auth/signup", { accept_terms: true, email, password: "smoke test password", name: "Tia Tasks", company_name: "Task Builders" });
      const st = await (await post("/api/jobs", { name: "Maple deck", address: "3 Maple St", customer: "Kim Homeowner", role: "Owner",
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

  await step("Daily record: weather, crew and a progress photo are saved on the job record (T11, T12)", async () => {
    await company();
    await p.goto(BASE + "/jobs/daily");
    await p.locator("#daily-job").waitFor();
    assert.equal(await p.inputValue("#daily-job"), jobId);
    await p.fill("#daily-crew", "3");
    await p.selectOption("#daily-weather", "Rain");
    await p.locator('[data-daily-section="photos"]').check();
    await p.locator("#daily-photos").setInputFiles({ name: "deck.jpg", mimeType: "image/jpeg", buffer: Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0, 16]) });
    await p.locator(".daily-files", { hasText: "deck.jpg" }).waitFor();
    await p.fill("#daily-notes", "Set the ledger board");
    await p.locator('[data-action="daily-save"]').click();
    await p.locator(".list-item", { hasText: "Set the ledger board" }).waitFor();
    const saved = await p.evaluate(async () => (await (await fetch("/api/state")).json()).daily_records.slice(-1)[0]);
    assert.equal(saved.weather, "Rain");
    assert.equal(saved.crew_count, 3);
    assert.equal(saved.attachments.length, 1);
    assert.equal(await p.inputValue("#daily-notes"), "", "the form is empty for the next one");
  });

  if (ctx) await ctx.close();
}
