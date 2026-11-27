// Browser steps for the client's own forms (gcforms.py, views/gcforms.js): upload a client's blank
// fillable pay app form on their card, check the matched field and save it, open the preview,
// then see the pay app page's card with the preview link. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

async function api(p, path, body) {
  return p.evaluate(async ([path, body]) => {
    const res = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = null;
    try { data = await res.json(); } catch { data = null; }
    return { status: res.status, data };
  }, [path, body]);
}
const ok = (r) => assert.equal(r.status, 200, JSON.stringify(r.data));

// A one-page fillable form with two text fields, written byte by byte (a classic xref table).
function fillableForm() {
  const objs = {
    1: "<</Type /Catalog /Pages 2 0 R /AcroForm 6 0 R>>",
    2: "<</Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 612 792]>>",
    3: "<</Type /Page /Parent 2 0 R /Contents 4 0 R /Resources <</Font <</F1 5 0 R>>>> /Annots [7 0 R 8 0 R]>>",
    4: "<</Length 60>>\nstream\nBT /F1 14 Tf 72 740 Td (APPLICATION FOR PAYMENT) Tj ET      \nendstream",
    5: "<</Type /Font /Subtype /Type1 /BaseFont /Helvetica>>",
    6: "<</Fields [7 0 R 8 0 R] /DA (/Helv 0 Tf 0 g)>>",
    7: "<</Type /Annot /Subtype /Widget /FT /Tx /T (Original Contract Sum) /Rect [300 690 450 710] /P 3 0 R>>",
    8: "<</Type /Annot /Subtype /Widget /FT /Tx /T (Architect) /Rect [300 660 450 680] /P 3 0 R>>",
  };
  let out = "%PDF-1.4\n";
  const at = {};
  for (const n of Object.keys(objs).map(Number)) { at[n] = out.length; out += `${n} 0 obj\n${objs[n]}\nendobj\n`; }
  const xref = out.length;
  out += "xref\n0 9\n0000000000 65535 f\r\n";
  for (let n = 1; n <= 8; n++) out += `${String(at[n]).padStart(10, "0")} 00000 n\r\n`;
  out += `trailer\n<</Size 9 /Root 1 0 R>>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, "latin1");
}

export default async function ({ BASE, step, signedInPage }) {
  let made = null;

  await step("Client's own forms: upload a fillable form on the client's card, check the guessed match, save, preview", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const run = Date.now().toString(36);
    const name = `Forms depot 90 ${run}`, client = `Formline Builders 90 ${run}`;
    const res = await api(p, "/api/jobs", { name, address: "12 Form St, Atlanta, GA 30303", customer: client, approver: "Dana Fox",
      ap_email: `ap-${run}@formline90.example`, phone: "(404) 555-0190", contract_cents: 2_500_000, retainage_pct: 10, role: "GC",
      client_type: "gc", new_customer: true, approver_confirmed: true });
    ok(res);
    const job = res.data.jobs.find((j) => j.name === name);
    made = { jobId: job.id, payerId: job.payer_id };
    await p.goto(`${BASE}/jobs/clients`);
    const card = p.locator(`[data-payer-card="${job.payer_id}"]`);
    await card.locator("summary").first().click();
    const section = card.locator(`[data-cf-section="${job.payer_id}"]`);
    await section.locator("summary").click();
    await section.getByText("Upload their blank", { exact: true }).waitFor();
    const [chooser] = await Promise.all([p.waitForEvent("filechooser"), section.getByRole("button", { name: "Choose the PDF" }).click()]);
    await chooser.setFiles({ name: "formline-pay-app.pdf", mimeType: "application/pdf", buffer: fillableForm() });
    const editor = section.locator(".cf-match");
    await editor.waitFor();
    const select = editor.locator('select[data-cf-field="Original Contract Sum"]');
    assert.equal(await select.inputValue(), "original_contract", "guessed from the field's name");
    await editor.locator('select[data-cf-field="Architect"]').selectOption("text");
    await editor.locator('input[data-cf-field="Architect"][data-cf-key="text"]').fill("Lee Architects");
    await editor.getByRole("button", { name: "Save matches" }).click();
    await editor.waitFor({ state: "detached" });
    const view = (await api(p, `/api/payers/${job.payer_id}/client-forms`)).data;
    const tpl = view.templates[0];
    assert.deepEqual(tpl.mapping.map((m) => [m.field, m.source, m.text || ""]),
      [["Original Contract Sum", "original_contract", ""], ["Architect", "text", "Lee Architects"]]);
    await section.getByText("version 1 · fillable, 2 fields, 2 matched").waitFor();
    const preview = await p.evaluate(async (u) => {
      const r = await fetch(u);
      const bytes = new Uint8Array(await r.arrayBuffer());
      return { status: r.status, text: Array.from(bytes, (b) => String.fromCharCode(b)).join("") };
    }, `/api/payers/${job.payer_id}/client-forms/${tpl.id}/sample.pdf`);
    assert.equal(preview.status, 200);
    assert.match(preview.text, /\(\$100,000\.00\) Tj/, "the sample contract sum drawn in its field");
    assert.match(preview.text, /\(Lee Architects\) Tj/);
    await p.context().close();
  });

  await step("Client's own forms: the pay app page links its preview on the client's form", async () => {
    assert.ok(made, "the job from the step before");
    const p = await signedInPage({ width: 1280, height: 900 });
    const job = (await api(p, `/api/jobs/${made.jobId}`)).data.job;
    const line = job.sov_view && job.sov_view.lines && job.sov_view.lines[0];
    ok(line ? await api(p, `/api/jobs/${made.jobId}/sov-progress`, { done: { [line.id]: "0.4" }, notes: "", period_to: "" })
      : await api(p, `/api/jobs/${made.jobId}/progress`, { completed_cents: 1_000_000, notes: "", period_to: "" }));
    const draft = (await api(p, `/api/jobs/${made.jobId}`)).data.job.invoices.find((i) => i.status === "draft" || i.status === "awaiting_approval");
    await p.goto(`${BASE}/money/pay-app/${made.jobId}/${draft.id}`);
    const box = p.locator(".client-forms-payapp");
    await box.waitFor();
    assert.match(await box.innerText(), /Every matched field has its value/);
    const link = box.getByRole("link", { name: "Preview it filled in" });
    assert.equal(await link.getAttribute("href"), `/api/jobs/${made.jobId}/payapp/${draft.id}/client-form/payapp.pdf`);
    const status = await p.evaluate(async (u) => (await fetch(u)).status, await link.getAttribute("href"));
    assert.equal(status, 200);
    await p.context().close();
  });
}
