// Browser steps for the job page's actions: all of them showing, and Log a conversation. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ BASE, step, signedInPage, confirmDialog }) {
  const waitingJob = (p) => p.evaluate(async () => {
    const st = await (await fetch("/api/state")).json();
    const j = st.jobs.find((x) => !x.closed && x.invoices.some((i) => ["sent", "approved", "partial"].includes(i.status)));
    return j && j.id;
  });

  await step("Log a conversation: a record on the job that holds the follow-up until its date", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const id = await waitingJob(p);
    assert.ok(id, "the demo has a job waiting for payment");
    await p.goto(`${BASE}/jobs/${id}`);
    await p.locator(".job-actions").waitFor();
    assert.equal(await p.getByRole("button", { name: /Spoke to them/ }).count(), 0);
    await p.getByRole("button", { name: "Log a conversation" }).click();
    await p.click('[data-action="save-conversation"]');
    await p.locator('[data-form-key="conversation"] .field-error', { hasText: "Enter what they said" }).waitFor();
    await p.fill("#conversation-text", "Check run is the 5th");
    const next = new Date(Date.now() + 5 * 86400000).toISOString().slice(0, 10);
    await p.fill("#conversation-next", next);
    await p.click('[data-action="save-conversation"]');
    await p.locator("#conversation-text").waitFor({ state: "detached" });
    await p.locator('.record-group[data-group="conversation"]', { hasText: "Check run is the 5th" }).waitFor();
    const job = await p.evaluate(async (jid) => (await (await fetch("/api/state")).json()).jobs.find((j) => j.id === jid), id);
    assert.equal(job.expected_payment_on, next);
    await p.context().close();
  });

  await step("phone: the job page's actions all show, and nothing runs off the side", async () => {
    const p = await signedInPage({ width: 375, height: 812 });
    const id = await waitingJob(p);
    await p.goto(`${BASE}/jobs/${id}`);
    await p.locator(".job-actions").waitFor();
    const buttons = p.locator(".job-actions .btn");
    assert.ok(await buttons.count() > 1);
    for (const b of await buttons.all()) assert.ok(await b.isVisible());
    const wide = await p.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    assert.ok(wide <= 0, `page scrolls sideways by ${wide}px`);
    // Round 1, item 13: Money and the long cards start folded on a phone, each with its summary line.
    assert.equal(await p.locator("details.money-file").getAttribute("open"), null, "Money is folded on a phone");
    for (const card of ["money-file", "sov-file", "job-records", "job-history"]) {
      assert.ok(await p.locator(`details.${card} > summary .sub`).isVisible(), `${card} shows its summary`);
    }
    assert.equal(await p.locator(".job-actions h3").count(), 0, "no group headings");
    // The four buttons two by two on a phone, each the same width.
    const boxes = await p.locator(".main-actions .main-action").evaluateAll((els) => els.map((e) => { const r = e.getBoundingClientRect(); return [Math.round(r.top), Math.round(r.width)]; }));
    assert.equal(boxes.length, 4);
    assert.equal(boxes[0][0], boxes[1][0], "first row");
    assert.equal(boxes[2][0], boxes[3][0], "second row");
    assert.ok(Math.abs(boxes[0][1] - boxes[3][1]) <= 1, "same width");
    // A fold opened stays open after the page redraws.
    await p.locator("details.money-file > summary").click();
    await p.getByRole("button", { name: "Log a conversation" }).click();
    assert.equal(await p.locator("details.money-file").getAttribute("open"), "");
    await p.context().close();
  });

  await step("Log a conversation from New job record: the same form, with its check-again date, for the job picked", async () => {  // criterion 59
    const p = await signedInPage({ width: 375, height: 800 });
    const id = await waitingJob(p);
    await p.goto(`${BASE}/jobs/${id}`);
    await p.locator(".job-actions").waitFor();
    await p.locator('[data-action="daily-for-job"]').first().click();
    await p.getByRole("heading", { name: "New job record" }).waitFor();
    await p.fill("#daily-notes", "Typed before the call");
    await p.getByRole("button", { name: "Log a conversation" }).click();
    await p.fill("#conversation-text", "Check goes out Friday");
    const next = new Date(Date.now() + 7 * 86400000).toISOString().slice(0, 10);
    await p.fill("#conversation-next", next);
    await p.click('[data-action="save-conversation"]');
    await p.locator("#conversation-text").waitFor({ state: "detached" });
    assert.equal(await p.inputValue("#daily-notes"), "Typed before the call", "the record being written stays");
    const job = await p.evaluate(async (jid) => (await (await fetch("/api/state")).json()).jobs.find((j) => j.id === jid), id);
    assert.equal(job.expected_payment_on, next);
    assert.ok(job.records.some((r) => r.kind === "conversation" && /Check goes out Friday/.test(`${r.label} ${r.note}`)));
    await p.context().close();
  });

  await step("A job from before schedules has the dollar form: where it stands and what the pay app comes to (R3-01)", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const id = await p.evaluate(async () => (await (await fetch("/api/state")).json()).jobs.find((j) => !j.closed && !j.sov_view.present && j.work_status !== "complete" && !j.invoices.some((i) => ["draft", "awaiting_approval"].includes(i.status)))?.id);
    assert.ok(id, "the demo has a job with no schedule");
    await p.goto(`${BASE}/jobs/${id}`);
    await p.locator('.main-actions [data-form="progress"]').click();
    await p.locator(".progress-context", { hasText: "Billed so far" }).waitFor();
    await p.fill("#progress-draw", "3,000");
    assert.equal(await p.locator("#progress-preview").innerText(), "This pay app: $3,000.00.");
    await p.context().close();
  });

  await step("Final pay app confirms first: the amount and what it does, and Cancel changes nothing", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const id = await p.evaluate(async () => {
      const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const st = await (await post("/api/jobs", { name: "Final check", address: "9 Elm St", customer: "Rae Final", role: "Owner", approver: "Rae", phone: "(404) 555-0100", ap_email: "rae@example.com", contract_cents: 1000000 })).json();
      return st.jobs.find((j) => j.name === "Final check").id;
    });
    await p.goto(`${BASE}/jobs/${id}`);
    assert.equal(await p.locator(".task-card").count(), 0, "nothing to do yet: no banner");
    await p.locator('.main-actions [data-form="done"]').click();
    await p.locator('[data-form-key="done"] h2', { hasText: "Final pay app" }).waitFor();
    await p.click('[data-action="done"]');
    await confirmDialog(p).waitFor();
    assert.match(await confirmDialog(p).innerText(), /Final pay app for Final check: \$[\d,.]+.*marks the work finished/s);
    await confirmDialog(p).getByRole("button", { name: "Cancel" }).click();
    const job = await p.evaluate(async (jid) => (await (await fetch("/api/state")).json()).jobs.find((j) => j.id === jid), id);
    assert.notEqual(job.work_status, "complete", "Cancel left the job open");
    await p.context().close();
  });

  await step("Job records has New job record and the records only; files dropped on the job open New job record with them", async () => {  // Yusuf, 10 Nov
    const p = await signedInPage({ width: 1280, height: 900 });
    const id = await waitingJob(p);
    await p.goto(`${BASE}/jobs/${id}`);
    const card = p.locator("details.job-records");
    await card.waitFor();
    assert.equal(await card.locator(".dropzone, [data-action='pick-files'], [data-action='import-for-job']").count(), 0);
    await card.getByRole("button", { name: "New job record" }).waitFor();
    const drop = (names) => p.evaluate((list) => {
      const dt = new DataTransfer();
      for (const [name, type] of list) dt.items.add(new File(["x"], name, { type }));
      document.dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
    }, names);
    await drop([["delivery-ticket.pdf", "application/pdf"], ["site.png", "image/png"]]);
    await p.getByRole("heading", { name: "New job record" }).waitFor();
    assert.equal(await p.inputValue("#daily-job"), id, "on the job it was dropped on");
    await p.locator('[data-record-section="other"] .daily-files', { hasText: "delivery-ticket.pdf" }).waitFor();
    await p.locator('[data-record-section="photos"] .daily-files', { hasText: "site.png" }).waitFor();
    // The form has its own drop area; a file dropped there joins the others.
    await p.locator(".record-drop", { hasText: "Drop files here" }).waitFor();
    await drop([["invoice.pdf", "application/pdf"]]);
    await p.locator('[data-record-section="other"] .daily-files', { hasText: "invoice.pdf" }).waitFor();
    const job = await p.evaluate(async (jid) => (await (await fetch("/api/state")).json()).jobs.find((j) => j.id === jid), id);
    assert.ok(!job.records.some((r) => /delivery-ticket\.pdf|invoice\.pdf/.test(`${r.label} ${r.file_name || ""}`)), "nothing uploads until Save");
    await p.context().close();
  });
}
