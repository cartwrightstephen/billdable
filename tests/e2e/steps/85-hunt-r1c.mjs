// Browser step for the last waiver gaps (hunt-r1c, Nov 6): a lien waiver whose title is too long to
// read as a heading, filed as a note, is asked about on Review & send, with Yes, unconditional filled
// in; a scan the app can't read picks nothing. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ BASE, step, signedInPage, confirmIfAsked }) {
  const TITLE = "Acme Electric - Unconditional Waiver and Release of Lien - Elm addition - Pay App 3";
  const BODY = "\nProject: Elm addition\nAmount: $5,000.00\nThe undersigned has been paid in full and releases all lien rights through 10/31/2026.\nSigned ____\n";

  await step("hunt r1c: a long-titled lien waiver filed as a note is asked about, then takes the owner's reason", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const made = await p.evaluate(async ([title, body]) => {
      const call = async (path, b) => (await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b || {}) })).json();
      const st = await call("/api/jobs", { name: "Smoke long title", customer: "Smoke long title client", contract_cents: 1000000, address: "7 Test St, Atlanta, GA 30307",
        approver: "Dee", phone: "(404) 555-0100", ap_email: "dee@hunt.example", role: "Owner", new_customer: true });
      const job = st.jobs.find((j) => j.name === "Smoke long title");
      const read = async () => (await (await fetch(`/api/jobs/${job.id}`)).json()).job;
      await call(`/api/jobs/${job.id}/sov-progress`, { done: { [(await read()).sov.lines[0].id]: "0.5" } });
      const draft = (await read()).invoices.at(-1);
      for (const item of draft.packet) if (item.required && !item.present) await call(`/api/jobs/${job.id}/resolve/${item.id}`, { reason: "In hand" });
      const upload = async (name, kind, data, type) => (await (await fetch(`/api/import/file?job=${job.id}&file=1&kind=${kind}`,
        { method: "POST", headers: { "Content-Type": type, "X-Filename": name }, body: data })).json()).uploaded_record_ids[0];
      const note = await upload("acme-oct.txt", "note", title + body, "text/plain");
      const scan = await upload("scan-0042.jpg", "other", "\xff\xd8 jpeg", "image/jpeg");
      for (const rid of [note, scan]) await call(`/api/jobs/${job.id}/payapp/${draft.id}/attachments`, { action: "add_record", record_id: rid });
      return { id: job.id, inv: draft.id, note };
    }, [TITLE, BODY]);
    await p.goto(`${BASE}/money/pay-app/${made.id}/${made.inv}`);
    const question = p.locator(".waiver-questions", { hasText: "Is this a lien waiver?" });
    await question.locator("a", { hasText: "Open acme-oct.txt" }).waitFor();
    // It read as unconditional, so Yes, unconditional is filled in; nothing else was asked (a photo reads as nothing).
    await question.locator('[data-action="waiver-answer"][data-answer="unconditional"][aria-current="true"]').click();
    const warning = p.locator(".unconditional-warning");
    await warning.filter({ hasText: "acme-oct.txt" }).waitFor();
    await warning.locator(`#override-reason-${made.inv}`).fill("Paid by wire");
    await warning.locator('[data-action="send-override"]').click();
    await confirmIfAsked(p);
    await p.waitForFunction(async (jobId) => (await (await fetch(`/api/jobs/${jobId}`)).json()).job.invoices.at(-1).unconditional_override?.reason === "Paid by wire", made.id);
    await p.context().close();
  });
}
