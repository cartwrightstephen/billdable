// The job page's change orders: the sign controls, the New change order card and its form.
import { money, centsFrom, fmtDate, unesc } from "../format.js";
import { coSignature, RECORD_KINDS } from "../jobs.js";
import { field, moneyField, formErrorLine, refuseForm, formValues, clearForm } from "../forms.js";
import { termText } from "../terms.js";
import { state, formOpen, onSessionEnd } from "../state.js";
import { post, render, toggleForm } from "../app-core.js";


// The change order just made from the job page's New change order card, shown there with
// "Request signature" in place of the form (R3-06): {job, co}.
let newCoMade = null;
// How many cost lines the New change order form shows (M8), and the backup records ticked on it:
// checkboxes aren't kept by forms.js, so they are read before a redraw.
let coItemRows = 0;
let coBackup = new Set();
// Signed out: the change order just made is that company's.
onSessionEnd(() => { newCoMade = null; coItemRows = 0; coBackup = new Set(); });

// Why the scope changed (domain.CO_REASONS, M7) and how it is priced (domain.CO_PRICING, M8).
export const CO_REASONS = {
  owner_request: "Owner request", unforeseen_condition: "Unforeseen condition", design_issue: "Design issue",
  value_engineering: "Value engineering", other: "Other",
};
export const CO_PRICING = { lump_sum: "Lump sum", tm: "Time and materials" };
const MAX_CO_ITEMS = 50;

// Today on this device (YYYY-MM-DD), the latest a signed or work-started date can be. The
// server allows a day of slack, so a device ahead of UTC isn't refused.
export function localToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
// Record kinds that can back up a change order: quotes, supplier invoices, T&M sheets, photos.
const BACKUP_KINDS = new Set(["note", "photo", "contract", "change_order", "ticket", "supplier_invoice", "stored", "other"]);

// The amber pill when work began before the client signed (M9); it stays once signed, as a record.
export function workStartedPill(co) {
  return co.work_started_unsigned ? ` <span class="pill amber">Work started before signing</span>` : "";
}

// Change orders are approved by a signature: online through a link the client opens,
// or a signed copy on the job record, with who signed it and the date (P2-3, M9).
// opts.flag false: the page shows the work-started pill elsewhere.
export function coSignControls(j, co, extra = "", opts = {}) {
  const flag = opts.flag === false ? "" : workStartedPill(co);
  if (co.status !== "pending" || j.closed) return flag;
  const key = `cosign-${co.id}`;
  const records = j.records || [];
  // The signed copy is most likely the newest change-order record (R3-06).
  const newestCo = records.filter(r => r.kind === "change_order").pop();
  return `${flag}<div class="form-inline">
      <button class="btn small" data-action="request-co-signature" data-id="${j.id}" data-co="${co.id}">${co.signing_page ? "Send the signing link again" : "Request signature"}</button>
      <button class="btn small secondary" data-action="toggle-form" data-form="${key}" aria-expanded="${!!formOpen[key]}">Signed on paper…</button>
      <button class="btn small secondary" data-action="withdraw-co" data-id="${j.id}" data-co="${co.id}">Withdraw</button>
      ${extra}
    </div>
    ${co.signing_page ? `<p class="sub">Signing page: <a href="/api/jobs/${j.id}/co/${co.id}/page" target="_blank" rel="noopener">open it</a>${co.signature_requested_at ? ` · requested ${fmtDate(co.signature_requested_at)}` : ""}</p>` : ""}
    ${formOpen[key] ? `<div class="co-paper" data-form-key="${key}">${formErrorLine(key)}
        <div class="form-grid">
          ${field({ form: key, name: "signer_name", id: `cosign-name-${co.id}`, label: "Signed by (name)", required: true, autocomplete: "off" })}
          ${field({ form: key, name: "signed_on", id: `cosign-date-${co.id}`, label: "Date signed", type: "date", required: true, attrs: `max="${localToday()}"` })}
        </div>
        <div class="form-inline">
          <button class="btn small secondary" data-action="pick-files" data-job="${j.id}" data-kind="change_order" data-capture="1">Take a photo of the signed copy</button>
          ${records.length ? `<label for="cosign-rec-${co.id}" class="inline-label">The signed copy</label><select id="cosign-rec-${co.id}"><option value="">Choose a record…</option>${records.map(r => `<option value="${r.id}"${newestCo && r.id === newestCo.id ? " selected" : ""}>${r.label}</option>`).join("")}</select>
          <button class="btn small" data-action="approve-co" data-id="${j.id}" data-co="${co.id}">Save as signed</button>` : ""}
        </div>
      </div>` : ""}`;
}

// The job page's New change order card (R3-06). Once saved, it shows the change order with the
// ways to get it signed in place of the form.
export function newCoCardHtml(j) {
  const made = newCoMade && newCoMade.job === j.id && j.change_orders.find(c => c.id === newCoMade.co);
  if (made) return `<div class="card form-card new-co-card"><h2>CO ${made.number}: ${made.title}</h2>
    <p class="sub">${made.amount_cents < 0 ? `A credit of ${money(-made.amount_cents)}` : money(made.amount_cents)}${made.schedule_days ? ` · ${made.schedule_days > 0 ? "+" : ""}${made.schedule_days} days on the schedule` : ""}. ${made.status === "pending" ? "Saved. Now get it signed." : "Signed."}</p>
    ${made.status === "pending" ? coSignControls(j, made) : coSignature(made)}
    <div class="form-inline"><button class="btn small secondary" data-action="new-co-done">Done</button></div></div>`;
  return `<div class="card form-card" data-form-key="newco"><h2>New change order</h2>${formErrorLine("newco")}
      ${coFormFields(j)}
      <div class="form-inline">
        <button class="btn small" data-action="add-co-here" data-id="${j.id}">Create change order</button>
        <button class="btn small secondary" data-action="new-co-done">Cancel</button>
      </div></div>`;
}

// The New change order boxes (the job page's card, and a job's change order page when it has
// none yet). A negative amount is a credit (R3-06). Stephen's Oct 29 review: the reason (M7),
// pricing, cost lines, the contract section it changes and the backup (M8), and the day work
// started, if it has (M9).
export function coFormFields(j) {
  const items = [];
  for (let n = 0; n < coItemRows; n++) {
    items.push(`<div class="form-grid co-item-row">
      ${field({ form: "newco", name: `item_label_${n}`, id: `co-item-label-${n}`, label: `Cost line ${n + 1}`, placeholder: n ? "Material" : "Labor" })}
      ${moneyField({ form: "newco", name: `item_amount_${n}`, id: `co-item-amt-${n}`, label: "Amount ($)", inputmode: "text" })}
    </div>`);
  }
  const backup = ((j && j.records) || []).filter(r => BACKUP_KINDS.has(unesc(r.kind)));
  return `<div class="form-grid">
      ${field({ form: "newco", name: "title", id: "co-title", label: "Title", required: true, placeholder: "Add two outlets in the kitchen" })}
      ${field({ form: "newco", name: "reason", id: "co-reason", label: "Reason", required: true, options: [["", "Pick why the scope changed…"], ...Object.entries(CO_REASONS)] })}
      ${field({ form: "newco", name: "description", id: "co-desc", label: "Description (required for Other)", wide: true, multiline: true })}
      ${field({ form: "newco", name: "contract_ref", id: "co-ref", label: "Contract section or original scope it changes (optional)", wide: true, placeholder: "Section 09 30 00, tile in baths 1-3" })}
      ${field({ form: "newco", name: "pricing", id: "co-pricing", label: "Pricing", options: Object.entries(CO_PRICING) })}
      ${moneyField({ form: "newco", name: "amount_cents", id: "co-amt", label: coItemRows ? "Amount ($), blank adds up the cost lines" : "Amount ($), negative for a credit", inputmode: "text" })}
    </div>
    <fieldset class="co-items"><legend>Cost breakdown (optional)</legend>
      ${items.join("")}
      ${coItemRows < MAX_CO_ITEMS ? `<button class="btn small secondary" data-action="co-add-item">Add a cost line</button>` : ""}
    </fieldset>
    <div class="form-grid">
      ${field({ form: "newco", name: "schedule_days", id: "co-days", label: "Schedule change, days (optional)", inputmode: "numeric", placeholder: "3" })}
      ${field({ form: "newco", name: "work_started_on", id: "co-started", label: "Work started on (optional)", type: "date", attrs: `max="${localToday()}"` })}
    </div>
    ${backup.length ? `<fieldset class="co-backup"><legend>Backup from the job record (optional)</legend>
      ${backup.map(r => `<label class="co-backup-item"><input type="checkbox" data-co-backup="${r.id}"${coBackup.has(unesc(r.id)) ? " checked" : ""}> <span class="pill">${termText(RECORD_KINDS[unesc(r.kind)] || r.kind)}</span> ${r.label}</label>`).join("")}
    </fieldset>` : ""}`;
}

// The ticked backup records, kept across a redraw of the form.
function readBackup() {
  const boxes = document.querySelectorAll('[data-form-key="newco"] [data-co-backup]');
  if (boxes && boxes.length) coBackup = new Set([...boxes].filter(b => b.checked).map(b => b.dataset.coBackup));
  return [...coBackup];
}

// The New change order form: check it here, then save. The server checks the same.
export async function addChangeOrder(jobId) {
  const v = formValues("newco");
  const errors = {};
  const items = [];
  for (let n = 0; n < coItemRows; n++) {
    const label = v[`item_label_${n}`] || "", raw = v[`item_amount_${n}`] || "";
    if (!label && !raw) continue;
    const amt = centsFrom(raw);
    if (!label) errors[`item_label_${n}`] = "Enter what this cost line is, like Labor.";
    if (amt.error || amt.cents === 0) errors[`item_amount_${n}`] = "Enter this line's amount, like 450.";
    items.push({ label, amount_cents: amt.cents || 0 });
  }
  const sum = items.reduce((s, i) => s + i.amount_cents, 0);
  const typed = String(v.amount_cents || "").trim();
  const amount = typed ? centsFrom(typed) : { cents: items.length ? sum : 0 };
  const days = String(v.schedule_days || "").trim();
  if (!v.title) errors.title = "Enter a title for the change order.";
  if (!CO_REASONS[v.reason]) errors.reason = "Pick why the scope changed.";
  else if (v.reason === "other" && !v.description) errors.description = "Enter a description: what changed and why.";
  if (amount.error || amount.cents === 0) errors.amount_cents = "Enter the change order amount in dollars, like 1,250, or -1,250 for a credit.";
  else if (items.length && !Object.keys(errors).some(k => k.startsWith("item_")) && amount.cents !== sum) errors.amount_cents = `The cost lines add up to ${money(sum)}. Make the amount match, or leave it blank.`;
  if (days && !/^[-+]?\d{1,3}$/.test(days)) errors.schedule_days = "Enter the schedule change in whole days, like 3, or leave it blank.";
  if (refuseForm("newco", errors)) return null;
  const r = await post(`/api/jobs/${jobId}/add-co`, {
    title: v.title, reason: v.reason, description: v.description, contract_ref: v.contract_ref || "", pricing: v.pricing || "lump_sum",
    amount_cents: amount.cents, items, schedule_days: days ? Number(days) : 0, work_started_on: v.work_started_on || "",
    support_record_ids: readBackup(),
  }, { formKey: "newco" });
  if (r && r.ok) { coItemRows = 0; coBackup = new Set(); }
  return r;
}

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  "include-co": (el, id) => post(`/api/jobs/${id}/include-co/${el.dataset.co}`),
  "new-co": () => { newCoMade = null; coItemRows = 0; coBackup = new Set(); toggleForm("newco"); requestAnimationFrame(() => document.getElementById("co-title")?.focus()); },
  "new-co-done": () => { newCoMade = null; coItemRows = 0; coBackup = new Set(); formOpen.newco = false; clearForm("newco"); render(); },
  "co-add-item": () => {
    readBackup();
    coItemRows = Math.min(MAX_CO_ITEMS, coItemRows + 1);
    render();
    requestAnimationFrame(() => document.getElementById(`co-item-label-${coItemRows - 1}`)?.focus());
  },
  "add-co-here": async (el, id) => {
    const r = await addChangeOrder(id);
    if (!(r && r.ok)) return;
    const j = state.jobs.find(x => x.id === id);
    const co = j && j.change_orders.reduce((a, c) => (!a || c.number > a.number ? c : a), null);
    if (co) { newCoMade = { job: id, co: co.id }; render(); }
  },
};
