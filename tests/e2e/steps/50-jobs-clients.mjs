// Browser steps for the jobs-clients stream (Stephen's Oct 29 review): New job with the address in
// four boxes and Import contract, New job record from the Jobs page, the Next step card with
// its blockers, a finished job's file, and the Client playbook's jobs and paperwork checkboxes.
// Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const CONTRACT = new URL("../../fixtures/contracts/subcontract.docx", import.meta.url);

export default async function ({ browser, page, BASE, step, signedInPage, confirmDialog, confirmIfAsked, errors }) {
  await step("New job: Import contract fills the four address boxes and the rest; Save files the contract (J1, G4, S12)", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(BASE + "/jobs");
    await p.getByRole("button", { name: "New job", exact: true }).click();
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
    assert.equal(await form.getByRole("button", { name: "New client" }).getAttribute("aria-pressed"), "true", "a client not saved yet is a new one");
    assert.equal(await form.locator("#nj-street").inputValue(), "418 Oak Street");
    assert.equal(await form.locator("#nj-city").inputValue(), "Decatur");
    assert.equal(await form.locator("#nj-state").inputValue(), "GA");
    assert.equal(await form.locator("#nj-zip").inputValue(), "30030");
    assert.equal(await form.locator("#nj-customer").inputValue(), "Ironleaf General Contractors, LLC");
    assert.equal(await form.locator("#nj-amount").inputValue(), "$48,500.00");
    assert.ok(await form.locator("#nj-street[data-from-contract]").count(), "the box says it came from the contract");
    // The playbook needs a phone and an email; the contract may not say. Who signs off is the person's to enter.
    assert.equal(await form.locator("#nj-approver").inputValue(), "", "the client's name isn't taken for the signer");
    for (const [box, value] of [["#nj-email", "ap@ironleaf.example"], ["#nj-phone", "404-555-0100"], ["#nj-approver", "Rae Ironleaf"]]) {
      if (!(await form.locator(box).inputValue())) await form.locator(box).fill(value);
    }
    if (!(await form.locator("#nj-client-type").inputValue())) await form.locator("#nj-client-type").selectOption("gc");
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
    await p.getByRole("button", { name: "New job", exact: true }).click();
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

  await step("New job: pick a saved client, fill what its playbook is missing, or make a new one with everything (Decisions 3 and 4)", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    // A client saved before the rule: no phone.
    const added = await p.evaluate(async () => {
      const post = (url, body) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      await post("/api/payers", { name: "Rule Client", client_type: "gc", approver: "Rae", ap_email: "rae@rule.example", phone: "4045550111" });
      return (await (await post("/api/jobs", { name: "No client", contract_cents: 100000, address: "1 Rule St", customer: "Brand New Co" })).json());
    });
    assert.equal(added.fields.phone, "Enter the client's phone number.", "the server refuses, per field");
    assert.ok(added.fields.approver && added.fields.ap_email && added.fields.client_type);
    await p.goto(BASE + "/jobs");
    await p.getByRole("button", { name: "New job", exact: true }).click();
    const form = p.locator('[data-form-key="newjob"]');
    await form.locator("#nj-name").fill("Rule porch");
    await form.locator("#nj-street").fill("1 Rule St");
    await form.locator("#nj-city").fill("Decatur");
    await form.locator("#nj-state").selectOption("GA");
    await form.locator("#nj-zip").fill("30030");
    await form.locator("#nj-amount").fill("9,000");
    await form.locator("#nj-end").fill("2020-01-01");
    await form.getByRole("button", { name: "Save job" }).click();
    await form.locator(".form-error", { hasText: "Pick an existing client" }).waitFor();
    await form.getByRole("button", { name: "New client" }).click();
    assert.equal(await form.locator("#nj-phone").isVisible(), true);
    assert.equal(await form.locator("#nj-name").inputValue(), "Rule porch", "what was typed stays");
    await form.locator("#nj-customer").fill("Rule Client");
    assert.equal(await form.locator("#nj-phone").isVisible(), false, "a name that is saved shows its details instead");
    await form.getByRole("button", { name: "Existing client" }).click();
    await form.locator("#nj-client").selectOption("Rule Client");
    await form.locator("[data-known-payer-text]", { hasText: "rae@rule.example" }).waitFor();
    // To the playbook and Back: what was typed is still there.
    await form.getByRole("button", { name: "Change in the Client playbook" }).click();
    await p.getByRole("heading", { name: "Client playbook" }).waitFor();
    await p.goBack();
    await form.locator("#nj-name").waitFor();
    assert.equal(await form.locator("#nj-name").inputValue(), "Rule porch");
    assert.equal(await form.locator("#nj-client").inputValue(), "Rule Client");
    await form.getByRole("button", { name: "Save job" }).click();
    await form.locator("#nj-end-err", { hasText: "on or after the start date" }).waitFor();
    await form.locator("#nj-end").fill("2099-01-01");
    await form.getByRole("button", { name: "Save job" }).click();
    await form.locator("#nj-end-err", { hasText: "next 10 years" }).waitFor();
    const ahead = new Date(Date.now() + 90 * 864e5).toISOString().slice(0, 10);
    await form.locator("#nj-end").fill(ahead);
    await form.getByRole("button", { name: "Save job" }).click();
    await p.locator(".job-head h1", { hasText: "Rule porch" }).waitFor();
    assert.match(await p.locator(".key-dates").innerText(), /Anticipated end/);
    await p.context().close();
  });

  await step("Supporting documents for this job: starts from the client's list, changes for the job only and follows into a draft", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(BASE + "/jobs");
    await p.locator("a", { hasText: "Rule porch" }).click();
    const card = p.locator(".job-paperwork");
    assert.match(await card.innerText(), /Rule Client's list/);
    await card.getByRole("button", { name: "Change for this job" }).click();
    await card.getByLabel("Certified payroll").check();
    await card.getByRole("button", { name: "Save supporting documents" }).click();
    await card.locator("p", { hasText: "Changed for this job" }).waitFor();
    assert.match(await card.innerText(), /Certified payroll/);
    const after = await p.evaluate(async () => (await (await fetch("/api/state")).json()).payers.find(x => x.name === "Rule Client"));
    assert.doesNotMatch(after.packet, /payroll/i, "the client's list stays");
    await card.getByRole("button", { name: "Use Rule Client's list" }).click();
    await card.locator("p", { hasText: "Rule Client's list" }).waitFor();
    await p.context().close();
  });

  await step("New job record from the Jobs page, with the row's job picked, and a typed ticket goes on as a ticket (J4)", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(BASE + "/jobs");
    await p.locator('[data-action="daily-for-job"][data-id="job-oak"]').click();
    await p.getByRole("heading", { name: "New job record" }).waitFor();
    assert.equal(await p.inputValue("#daily-job"), "job-oak");
    await p.fill("#daily-delivery-supplier-0", "Tile delivered to site");
    await p.locator('[data-action="daily-save"]').click();
    await p.locator(".saved-result", { hasText: "1 delivery ticket" }).waitFor();
    const rec = await p.evaluate(async () => (await (await fetch("/api/jobs/job-oak")).json()));
    const records = (rec.job || rec).records || [];
    assert.ok(records.some(r => r.label === "Delivery ticket: Tile delivered to site" && r.kind === "ticket" && r.ticket_type === "delivery"));
    assert.equal(await p.inputValue("#daily-job"), "job-oak", "stays on the job");
    await p.context().close();
  });

  await step("a blocked job: one Next step card that lists every blocker; Activity at the bottom (J6, J7)", async () => {
    await page.goto(BASE + "/jobs/job-oak");
    const card = page.locator(".next-card");
    await card.waitFor();
    assert.equal(await page.locator(".blockers-card").count(), 0);
    if (await card.locator(".blocker-list li").count()) assert.match(await card.locator("strong").first().innerText(), /( before it can go|before sending):$/);
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
