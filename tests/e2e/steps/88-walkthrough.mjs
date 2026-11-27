// The tester walkthrough, docs/tester-walkthrough.md, step by step (Stephen, 18 Nov, answer 5: "a
// sample company with a one cycle history that a test user could physically enter in themselves").
// It signs up a fresh account through Create account and types the document's sample data into the
// same boxes, then checks the figures the document gives at each Checkpoint. When this fails
// because the app changed, update the document with it. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

// The document's sample data. Keep these and docs/tester-walkthrough.md the same.
const COMPANY = { name: "Peachtree Current Electric", phone: "(404) 555-0142", street: "1450 Marietta Blvd NW, Suite 210",
  city: "Atlanta", state: "GA", zip: "30318", license: "EN-218734", legal: "Peachtree Current Electric, LLC" };
const CLIENT = { name: "Brightline Builders Group", approver: "Dana Whitfield", email: "tess+gc@example.com", phone: "(404) 555-0187", terms: "30" };
const JOB = { name: "Peachtree Commons tenant build-out", street: "2150 Peachtree Commons Dr NW", city: "Atlanta", state: "GA", zip: "30318",
  amount: "82,000", contract: "BBG-2611-E" };
const SOV = [["Temporary power", "1", "LS", "4500"], ["Branch conduit and wire", "2400", "LF", "12.50"],
  ["Light fixtures, set and wired", "120", "EA", "175"], ["Panelboards", "4", "EA", "3250"], ["Receptacles and switches", "300", "EA", "45"]];
const CO = { title: "Four EV charger circuits in the garage", amount: "6,400" };
const TESTER = "Tess Tester";

// Any file stands in for the signed copies, photos and time sheet the document asks for.
const file = (name) => ({ name, mimeType: "image/jpeg", buffer: Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0, 16]) });

export default async function ({ browser, BASE, step, errors, signIfAsked }) {
  let p, jobUrl, broken = "";
  // The steps follow one another like the document: after one fails, the rest say so at once
  // instead of each waiting out its own timeout.
  const walk = (name, fn) => step(name, async () => {
    if (broken) throw new Error(`not run: "${broken}" failed`);
    try { await fn(); } catch (err) { broken = name; throw err; }
  });
  const moneyRow = (label) => p.locator(".money-file .money-row").filter({ has: p.locator(".label", { hasText: new RegExp(`^${label}i?$`) }) }).locator("span").last();
  async function money(expected) {
    for (const [label, value] of Object.entries(expected)) assert.equal((await moneyRow(label).innerText()).trim(), value, label);
  }
  const periodRow = (label) => p.locator(".sov-period .money-row").filter({ has: p.locator(".label", { hasText: label }) }).locator("span").last();
  async function openJob() {
    await p.goto(jobUrl);
    await p.getByRole("heading", { name: JOB.name }).waitFor();
  }
  // A file picked through a button that opens the file chooser.
  async function pick(button, name) {
    const [chooser] = await Promise.all([p.waitForEvent("filechooser"), button.click()]);
    await chooser.setFiles(file(name));
  }
  async function confirm(buttonName) {
    const dialog = p.locator("dialog.confirm-dialog");
    await dialog.getByRole("button", { name: buttonName, exact: true }).click();
    await dialog.waitFor({ state: "detached" });
  }
  const today = () => p.evaluate(() => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; });

  await walk("Walkthrough 1: Create account lands on the Dashboard's first-pay-app card", async () => {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    await p.locator("#signup h2", { hasText: "Create your company" }).waitFor();
    await p.fill("#auth-name", TESTER);
    await p.fill("#auth-company", COMPANY.name);
    await p.fill("#auth-email", `tess+tester-${Date.now()}@example.com`);
    await p.fill("#auth-password", "walkthrough password");
    await p.check("#auth-terms");
    await p.locator("#auth-form").getByRole("button", { name: "Create account" }).click();
    await p.waitForURL(/\/dashboard$/);
    await p.getByText("Get your first pay app out").waitFor();
    await p.getByText("0 of 5 steps done.").waitFor();
  });

  await walk("Walkthrough 2: company details save, and the Dashboard ticks that step", async () => {
    await p.getByRole("button", { name: "Company details" }).click();
    await p.waitForURL(/\/settings\/company$/);
    await p.fill("#co-phone", COMPANY.phone);
    await p.fill("#co-address", COMPANY.street);
    await p.fill("#co-city", COMPANY.city);
    await p.selectOption("#co-state", COMPANY.state);
    await p.fill("#co-zip", COMPANY.zip);
    await p.fill("#co-license", COMPANY.license);
    await p.fill("#co-legal-name", COMPANY.legal);
    await p.getByRole("button", { name: "Save", exact: true }).click();
    await p.locator(".toast", { hasText: "Saved" }).waitFor();
    await p.goto(BASE + "/dashboard");
    await p.getByText("1 of 5 steps done.").waitFor();
    await p.getByText("✓ Add your company address, phone and licence number").waitFor();
  });

  await walk("Walkthrough 3: Add client in the Client playbook", async () => {
    await p.goto(BASE + "/jobs");
    await p.getByRole("button", { name: "Client playbook" }).click();
    await p.getByRole("button", { name: "Add client" }).first().click();
    await p.fill("#ap-name", CLIENT.name);
    await p.selectOption("#ap-client-type", { label: "General contractor" });
    await p.fill("#ap-approver", CLIENT.approver);
    await p.fill("#ap-email", CLIENT.email);
    await p.fill("#ap-phone", CLIENT.phone);
    await p.fill("#ap-terms", CLIENT.terms);
    await p.getByLabel("Progress photos").check();
    await p.locator('[data-form-key="addpayer"]').getByRole("button", { name: "Add client" }).click();
    await p.getByText(`General contractor · ${CLIENT.approver} · ${CLIENT.email} · 0 jobs`).waitFor();
  });

  await walk("Walkthrough 4: New job with a five-line schedule of values totalling $82,000.00, retainage 10%", async () => {
    await p.getByRole("button", { name: "Open jobs" }).click();
    await p.getByRole("button", { name: "New job", exact: true }).first().click();
    await p.fill("#nj-name", JOB.name);
    await p.fill("#nj-street", JOB.street);
    await p.fill("#nj-city", JOB.city);
    await p.selectOption("#nj-state", JOB.state);
    await p.fill("#nj-zip", JOB.zip);
    await p.fill("#nj-amount", JOB.amount);
    await p.getByRole("button", { name: "Existing client" }).click();
    await p.selectOption("#nj-client", CLIENT.name);
    await p.getByText(`General contractor. Pay apps go to ${CLIENT.approver} · ${CLIENT.email} · ${CLIENT.phone}.`).waitFor();
    assert.equal(await p.inputValue("#nj-retainage"), "10", "retainage fills in from the client");
    await p.fill("#nj-contract", JOB.contract);
    await p.locator("details.nj-sov summary").click();
    await p.getByRole("button", { name: "Add line" }).click();
    await p.getByRole("button", { name: "Add line" }).click();
    for (const [i, [desc, qty, unit, rate]] of SOV.entries()) {
      await p.fill(`#nj-sov-desc-${i}`, desc);
      await p.fill(`#nj-sov-qty-${i}`, qty);
      await p.fill(`#nj-sov-unit-${i}`, unit);
      await p.fill(`#nj-sov-rate-${i}`, rate);
    }
    await p.getByRole("button", { name: "Save job" }).click();
    await p.getByRole("heading", { name: JOB.name }).waitFor();
    jobUrl = p.url();
    await money({ "Contract": "$82,000.00", "Contract total": "$82,000.00", "Retainage": "10%" });
    const total = p.locator(".sov-table tr", { hasText: "Total" }).last();
    assert.match(await total.innerText(), /\$82,000\.00/);
  });

  await walk("Walkthrough 5: CO 1 created, signed on paper, contract total $88,400.00", async () => {
    await p.getByRole("button", { name: "New Change Order" }).click();
    await p.fill("#co-title", CO.title);
    await p.selectOption("#co-reason", { label: "Owner request" });
    await p.fill("#co-amt", CO.amount);
    await p.getByRole("button", { name: "Create change order" }).click();
    await p.getByText(`CO 1 · ${CO.title} · $6,400.00 Unsigned`).waitFor();
    await p.getByRole("button", { name: "Signed on paper…" }).first().click();
    // The new change order's card and its row under Change orders both open the same form.
    await p.locator('[id^="cosign-name-"]').first().fill(CLIENT.approver);
    await p.locator('[id^="cosign-date-"]').first().fill(await today());
    await pick(p.getByRole("button", { name: "Take a photo of the signed copy" }).first(), "CO1-signed.jpg");
    await p.locator(".toast", { hasText: "1 file added to the job records." }).waitFor();
    await p.getByRole("button", { name: "Save as signed" }).first().click();
    await p.getByText(`CO 1 · ${CO.title} · $6,400.00 Signed`).waitFor();
    await money({ "Signed change orders": "$6,400.00", "Contract total": "$88,400.00" });
    assert.match(await p.locator(".sov-table tr", { hasText: "Total" }).last().innerText(), /\$88,400\.00/);
  });

  // A line's box on New job record, by the line's description.
  const qty = (desc) => p.locator(`input[aria-label^="${desc}"]`);

  await walk("Walkthrough 6: two job records with quantities, a photo and a time sheet; $36,200.00 done, $32,580.00 suggested", async () => {
    await p.locator("main").getByRole("button", { name: "New job record" }).first().click();
    await p.waitForURL(/\/jobs\/daily$/);
    await p.fill("#daily-notes", "Temporary power set. Ran conduit on level 1. Set panels LP-1 and LP-2.");
    // The time sheet: a row per person, the team's or someone else typed (Nov 26).
    await p.selectOption("#daily-ts-pick-0", "__other");
    await p.fill("#daily-ts-name-0", "Level 1 crew");
    await p.fill("#daily-ts-hours-0", "32");
    await qty("Temporary power:").fill("100");
    await qty("Branch conduit and wire:").fill("700");
    await qty("Panelboards:").fill("2");
    await p.getByRole("button", { name: "Save job record" }).click();
    await p.locator(".saved-result", { hasText: "1 diary entry" }).waitFor();
    await p.getByText("Done, not billed $19,750").waitFor();

    await p.fill("#daily-notes", "Finished level 1 conduit. Hung 40 fixtures. Roughed in the EV circuits.");
    await qty("Branch conduit and wire:").fill("500");
    await qty("Light fixtures, set and wired:").fill("40");
    await qty("CO 1 ").fill("50");
    await p.getByRole("button", { name: "+ Photos", exact: true }).click();
    await p.locator("#daily-photos_0-files").setInputFiles(file("level1-fixtures.jpg"));
    await p.selectOption("#daily-ts-pick-0", "__other");
    await p.fill("#daily-ts-name-0", "Level 1 crew");
    await p.fill("#daily-ts-hours-0", "40");
    await p.locator("#daily-time_sheets_0-files").setInputFiles(file("timesheet-level1.jpg"));
    await p.getByRole("button", { name: "Save job record" }).click();
    await p.locator(".saved-result", { hasText: "1 diary entry, 1 progress photo, 1 time sheet" }).waitFor();

    await openJob();
    await money({ "Done, not billed": "$36,200.00" });
    assert.equal(await periodRow("Suggested pay app value").innerText(), "$36,200.00");
    assert.equal(await periodRow("Less retainage 10%").innerText(), "-$3,620.00");
    assert.equal(await periodRow("Suggested amount due").innerText(), "$32,580.00");
  });

  await walk("Walkthrough 7: Progress Pay App drafts PA-PEAC-01 for $32,580.00 with 3 supporting documents missing", async () => {
    await p.getByRole("button", { name: "Progress Pay App" }).click();
    await p.waitForURL(/\/money\/new-pay-app\//);   // its own page (Nov 26)
    const done = await p.locator('[data-form-key="sovprogress"] input[id^="sovp-done-"]').evaluateAll(els => els.map(e => e.value));
    assert.deepEqual(done, ["100", "1200", "40", "2", "0", "50"], "Done to date comes from the job records");
    await p.getByRole("button", { name: "Draft progress pay app" }).click();
    await p.waitForURL(/\/money\/pay-app\//);   // the draft's own page
    await openJob();
    const row = p.locator(".card", { hasText: "Pay apps" }).getByText(/PA-PEAC-01\s+Draft\s+\$32,580\.00/);
    await row.waitFor();
    // Progress photos are a check (soft), not a hard stop (Nov 26). Billing CO 1's work asks for its
    // daily tickets and its time sheets, two items since the Nov 27 audit, each a hard stop for
    // change order work: with the waiver, three are missing.
    await p.getByText("3 supporting documents missing").waitFor();
    assert.equal(await periodRow("On PA-PEAC-01").innerText(), "$36,200.00");
    assert.equal(await periodRow("Amount due").innerText(), "$32,580.00");
  });

  await walk("Walkthrough 8: the supporting documents, then Ready to send", async () => {
    await p.getByRole("button", { name: "Add or check 3 documents" }).click();
    await p.waitForURL(/\/money\/pay-app\//);
    await p.getByText("Missing (3)").waitFor();
    const item = (label) => p.locator(".packet-item", { hasText: label });
    for (const label of ["Conditional waiver on progress payment", "Progress photos", "Daily tickets", "Time sheets"]) await item(label).first().waitFor();
    const waiver = item("Conditional waiver on progress payment");
    await pick(waiver.getByRole("button", { name: "Choose file" }), "conditional-waiver.jpg");
    await waiver.getByText("Which waiver is this?").waitFor();
    await waiver.getByRole("button", { name: "Conditional", exact: true }).click();
    await waiver.locator(".mark", { hasText: "✓" }).waitFor();
    await item("Progress photos").getByRole("button", { name: "I checked this file" }).click();
    await item("Progress photos").locator(".mark", { hasText: "✓" }).waitFor();
    // The time sheet saved with job record 2 is found in the job records, as the photo is.
    const sheets = item("Time sheets");
    await sheets.getByText("timesheet-level1.jpg").waitFor();
    await sheets.getByRole("button", { name: "I checked this file" }).click();
    await sheets.locator(".mark", { hasText: "✓" }).waitFor();
    // No delivery tickets on this work: the owner (who can send pay apps) lets it go with a reason.
    const tickets = p.locator(".packet-item", { hasText: "Daily tickets" }).filter({ hasNotText: "Time sheets" });
    await tickets.getByRole("button", { name: "I have this…" }).click();
    await tickets.getByLabel("Why it can go without a checked file").fill("CO 1 was billed on the time sheet; no delivery tickets");
    await tickets.getByRole("button", { name: "Save override" }).click();
    await tickets.locator(".mark", { hasText: "✓" }).waitFor();
    await p.locator("h1 + .sub", { hasText: "Ready to send" }).waitFor();
    // The email preview's sums, as the document has the tester check the PDF.
    await p.getByText("Less retainage 10%: -$3,620.00").first().waitFor();
    await p.getByText("Amount due this application: $32,580.00").first().waitFor();
    assert.match(await p.locator("h1 + .sub").innerText(), /\$32,580\.00/);
  });

  await walk("Walkthrough 9: sign, Review & send, then $32,580.00 outstanding and $3,620.00 held back", async () => {
    await p.getByText(`${CLIENT.approver} · ${CLIENT.email}`).first().waitFor();
    // Every pay app is signed before it goes (Stephen, 18 Nov); with no approvers named, the owner
    // (the tester) signs on Review & send, as the walkthrough's step 9 says.
    assert.equal(await p.locator(".send-check .approve-sign").count(), 1, "the owner is asked to sign");
    await signIfAsked(p, TESTER);
    await p.getByRole("button", { name: `Approve & send to ${CLIENT.email}` }).click();
    assert.match(await p.locator("dialog.confirm-dialog").innerText(), /Email PA-PEAC-01 for \$32,580\.00.*to Dana Whitfield/s);
    await confirm("Send");
    await p.locator("h1 + .sub .pill", { hasText: "Sent" }).waitFor();
    await openJob();
    await money({ "Billed": "$32,580.00", "Received": "$0.00", "Outstanding": "$32,580.00", "Held back, still owed": "$3,620.00" });
  });

  await walk("Walkthrough 10: Record a Payment of $32,580.00; nothing outstanding, $3,620.00 still held back", async () => {
    await p.getByRole("button", { name: "Record a Payment" }).click();
    const form = p.locator('[data-form-key="match"]');
    await form.getByText("PA-PEAC-01 · $32,580.00 · Sent").waitFor();
    assert.equal(await p.inputValue("#match-amt"), "$32,580.00");
    assert.equal(await p.inputValue("#match-date"), await today());
    await p.fill("#match-ref", "Check 10442");
    await form.getByRole("button", { name: "Record", exact: true }).click();
    await p.locator(".card", { hasText: "Pay apps" }).getByText(/PA-PEAC-01\s+Paid\s+\$32,580\.00/).waitFor();
    await money({ "Received": "$32,580.00", "Outstanding": "$0.00", "Held back, still owed": "$3,620.00" });
  });

  await walk("Walkthrough 11: Choose waiver and send: the unconditional progress waiver for $32,580.00 goes out", async () => {
    await p.getByRole("button", { name: "Choose waiver and send" }).click();
    await p.waitForURL(/\/money\/waiver\//);
    await p.getByText("PA-PEAC-01 · $32,580.00 · Due").waitFor();
    assert.equal(await p.locator('[id^="waiver-kind-"]').inputValue(), "unconditional_progress");
    await p.getByText("Amount covered: $32,580.00").waitFor();
    await p.getByText("Not released: the $3,620.00 retainage held to date on this job").waitFor();
    await pick(p.getByRole("button", { name: "Choose file" }), "unconditional-waiver.jpg");
    await p.locator(".toast", { hasText: "1 file added to the job records." }).waitFor();
    await p.getByRole("button", { name: "Approve & send" }).click();
    // The look-first box is followed by the send box: no wait for it to go.
    await p.locator("dialog.confirm-dialog").getByRole("button", { name: "I opened it and it's right" }).click();
    await p.locator("dialog.confirm-dialog").getByRole("button", { name: "Send the waiver" }).waitFor();
    assert.match(await p.locator("dialog.confirm-dialog").innerText(), /Email the signed unconditional waiver for PA-PEAC-01, \$32,580\.00 to Dana Whitfield/);
    await confirm("Send the waiver");
    await p.getByText("Unconditional, progress payment: Sent", { exact: true }).waitFor();
    await p.getByText("Conditional, progress payment: Sent", { exact: true }).waitFor();
    await p.getByText("Backlog $52,200").waitFor();
    await p.context().close();
  });
}
