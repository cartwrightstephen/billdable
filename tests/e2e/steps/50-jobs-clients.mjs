// Browser steps for the jobs-clients stream (Stephen's Oct 29 review): New job with the address in
// four boxes and Import contract, Add to a job record from the Jobs page, the Next step card with
// its blockers, a finished job's file, and the Client playbook's jobs and paperwork checkboxes.
// Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const CONTRACT = new URL("../../fixtures/contracts/subcontract.docx", import.meta.url);

export default async function ({ browser, page, BASE, step, signedInPage, confirmDialog, confirmIfAsked, errors }) {
  await step("New job: Import contract fills the four address boxes and the rest; Save files the contract (J1, G4, S12)", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(BASE + "/jobs");
    await p.getByRole("button", { name: "New job" }).click();
    const form = p.locator('[data-form-key="newjob"]');
    // A ZIP that isn't one is marked when the box is left (G1).
    await form.locator("#nj-zip").fill("3030");
    await form.locator("#nj-city").focus();
    assert.match(await form.locator("#nj-zip-err").innerText(), /5-digit ZIP/);
    const chooser = p.waitForEvent("filechooser");
    await form.getByRole("button", { name: "Import contract" }).click();
    await (await chooser).setFiles({ name: "subcontract.docx", mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      buffer: readFileSync(CONTRACT) });
    await form.locator(".contract-status", { hasText: "From the contract" }).waitFor();
    assert.equal(await form.locator("#nj-street").inputValue(), "418 Oak Street");
    assert.equal(await form.locator("#nj-city").inputValue(), "Decatur");
    assert.equal(await form.locator("#nj-state").inputValue(), "GA");
    assert.equal(await form.locator("#nj-zip").inputValue(), "30030");
    assert.equal(await form.locator("#nj-customer").inputValue(), "Ironleaf General Contractors, LLC");
    assert.equal(await form.locator("#nj-amount").inputValue(), "$48,500.00");
    assert.ok(await form.locator("#nj-street[data-from-contract]").count(), "the box says it came from the contract");
    await form.getByRole("button", { name: "Save job" }).click();
    await p.locator(".job-head h1", { hasText: "Oak Street Kitchen" }).waitFor();
    assert.match(await p.locator(".job-head .sub").innerText(), /418 Oak Street, Decatur, GA 30030/);
    await p.locator(".record-group", { hasText: "Contract and change orders" }).waitFor();
    await p.getByRole("heading", { name: "Where this job stands" }).waitFor();
    assert.match(await p.locator(".key-dates").innerText(), /Started\s+Oct 5, 2026/);
    await p.context().close();
  });

  await step("New job: changing the amount and clicking Save job straight away takes the first click (the line under the schedule redraws in place)", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(BASE + "/jobs");
    await p.getByRole("button", { name: "New job" }).click();
    const form = p.locator('[data-form-key="newjob"]');
    const chooser = p.waitForEvent("filechooser");
    await form.getByRole("button", { name: "Import contract" }).click();
    await (await chooser).setFiles({ name: "subcontract.docx", mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      buffer: readFileSync(CONTRACT) });
    await form.locator(".contract-status", { hasText: "From the contract" }).waitFor();
    await form.locator("#nj-amount").fill("50,000");
    // One click, with the amount box still focused: leaving it redraws the difference line, and
    // the click must still reach Save job (it used to be lost to a whole-page redraw).
    await form.getByRole("button", { name: "Save job" }).click();
    await form.locator("#nj-amount-err", { hasText: "The schedule of values from the contract adds up to $48,500.00" }).waitFor({ timeout: 3000 });
    assert.match(await form.locator("#nj-sov-diff").innerText(), /\$1,500\.00 less/);
    await p.context().close();
  });

  await step("Add to a job record from the Jobs page, with the row's job picked (J4)", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(BASE + "/jobs");
    await p.locator('[data-action="jr-open"][data-id="job-oak"]').click();
    const card = p.locator(".add-to-record");
    assert.equal(await card.locator("#jr-job").inputValue(), "job-oak");
    await card.locator("summary", { hasText: "Add a note to the record" }).click();
    await card.locator("#jr-label").fill("Tile delivered to site");
    await card.locator("#jr-kind").selectOption("ticket");
    await card.getByRole("button", { name: "Add to record" }).click();
    await p.locator(".toast", { hasText: "Added to the job record" }).waitFor();
    const rec = await p.evaluate(async () => (await (await fetch("/api/jobs/job-oak")).json()));
    const records = (rec.job || rec).records || [];
    assert.ok(records.some(r => r.label === "Tile delivered to site" && r.kind === "ticket"));
    await p.context().close();
  });

  await step("a blocked job: one Next step card that lists every blocker; Activity at the bottom (J6, J7)", async () => {
    await page.goto(BASE + "/jobs/job-oak");
    const card = page.locator(".next-card");
    await card.waitFor();
    assert.equal(await page.locator(".blockers-card").count(), 0);
    if (await card.locator(".blocker-list li").count()) assert.match(await card.locator("strong").first().innerText(), /^(Before it can go:|Attach the paperwork, then send\.)$/);
    await page.getByRole("heading", { name: "Activity" }).waitFor();
  });

  await step("a finished job is its job file: Download job file and Add more work (J9)", async () => {
    await page.goto(BASE + "/jobs/job-hist-maple");
    await page.getByRole("heading", { name: "Job file" }).waitFor();
    assert.equal(await page.locator(".next-card").count(), 0);
    await page.getByRole("button", { name: "Add more work" }).waitFor();
    const res = await page.evaluate(async () => {
      const r = await fetch("/api/jobs/job-hist-maple/archive/job-file.zip");
      return [r.status, r.headers.get("content-type")];
    });
    assert.deepEqual(res, [200, "application/zip"]);
    await page.goto(BASE + "/jobs");
    assert.equal(await page.locator('#view a[data-id="job-hist-maple"]').count(), 0, "not on the open jobs list");
  });

  await step("Client playbook: a client's open and finished jobs, the paperwork as checkboxes (S10, S11)", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(BASE + "/jobs/clients");
    const card = p.locator('[data-payer-card="payer-ellison"]');
    await card.locator(":scope > summary").click();
    await card.locator(".client-jobs h3", { hasText: "Finished jobs" }).waitFor();
    assert.ok(await card.locator(".client-job", { hasText: "Maple Residence" }).count());
    const box = card.locator('[data-packet-choice="Insurance certificate"]');
    await box.check();
    await card.getByRole("button", { name: "Save client" }).click();
    await p.waitForFunction(async () => {
      const st = await (await fetch("/api/state")).json();
      return (st.payers.find(x => x.id === "payer-ellison").packet_items || []).includes("Insurance certificate");
    });
    await p.context().close();
  });
}
