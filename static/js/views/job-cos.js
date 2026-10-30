// The job page's change orders: the sign controls, the New change order card and its form.
import { money, centsFrom, fmtDate } from "../format.js";
import { coSignature } from "../jobs.js";
import { field, moneyField, formErrorLine, refuseForm, formValues, clearForm } from "../forms.js";
import { state, formOpen, onSessionEnd } from "../state.js";
import { post, render, toggleForm } from "../app-core.js";


// The change order just made from the job page's New change order card, shown there with
// "Request signature" in place of the form (R3-06): {job, co}.
let newCoMade = null;
// Signed out: the change order just made is that company's.
onSessionEnd(() => { newCoMade = null; });

// Change orders are approved by a signature: online through a link the payer opens,
// or a signed copy on the job record (P2-3). There is no bare "approve" any more.
export function coSignControls(j, co, extra = "") {
  if (co.status !== "pending" || j.closed) return "";
  const key = `cosign-${co.id}`;
  const records = j.records || [];
  // The signed copy is most likely the newest change-order record (R3-06).
  const newestCo = records.filter(r => r.kind === "change_order").pop();
  return `<div class="form-inline">
      <button class="btn small" data-action="request-co-signature" data-id="${j.id}" data-co="${co.id}">${co.signing_page ? "Send the signing link again" : "Request signature"}</button>
      <button class="btn small secondary" data-action="toggle-form" data-form="${key}" aria-expanded="${!!formOpen[key]}">Signed on paper…</button>
      <button class="btn small secondary" data-action="withdraw-co" data-id="${j.id}" data-co="${co.id}">Withdraw</button>
      ${extra}
    </div>
    ${co.signing_page ? `<p class="sub">Signing page: <a href="/api/jobs/${j.id}/co/${co.id}/page" target="_blank" rel="noopener">open it</a>${co.signature_requested_at ? ` · requested ${fmtDate(co.signature_requested_at)}` : ""}</p>` : ""}
    ${formOpen[key] ? `<div class="form-inline">
        <button class="btn small" data-action="pick-files" data-job="${j.id}" data-co="${co.id}" data-kind="change_order" data-capture="1">Take a photo of the signed copy</button>
        ${records.length ? `<label for="cosign-rec-${co.id}" class="inline-label">or pick it from the job record</label><select id="cosign-rec-${co.id}"><option value="">Choose a record…</option>${records.map(r => `<option value="${r.id}"${newestCo && r.id === newestCo.id ? " selected" : ""}>${r.label}</option>`).join("")}</select><button class="btn small secondary" data-action="approve-co" data-id="${j.id}" data-co="${co.id}">Save as signed</button>` : ""}
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
  return `<div class="card form-card" data-form-key="newco"><h2>New change order</h2>${formErrorLine("newco")}<div class="form-row">
      ${coFormFields()}
      <button class="btn small" data-action="add-co-here" data-id="${j.id}">Create change order</button>
      <button class="btn small secondary" data-action="new-co-done">Cancel</button>
    </div></div>`;
}

// The New change order boxes (job page, Change orders tab, a job with none yet). A negative amount
// is a credit (R3-06).
export function coFormFields() {
  return `${field({ form: "newco", name: "title", id: "co-title", label: "Title", placeholder: "Add two outlets in the kitchen" })}
    ${moneyField({ form: "newco", name: "amount_cents", id: "co-amt", label: "Amount ($), negative for a credit", inputmode: "text" })}
    ${field({ form: "newco", name: "description", id: "co-desc", label: "What changed (optional)", wide: true })}
    ${field({ form: "newco", name: "schedule_days", id: "co-days", label: "Schedule change, days (optional)", inputmode: "numeric", placeholder: "3" })}`;
}

// The New change order form: check it here, then save.
export async function addChangeOrder(jobId) {
  const v = formValues("newco");
  const amount = centsFrom(v.amount_cents);
  const days = String(v.schedule_days || "").trim();
  const errors = {};
  if (!v.title) errors.title = "Enter a title for the change order.";
  if (amount.error || amount.cents === 0) errors.amount_cents = "Enter the change order amount in dollars, like 1,250, or -1,250 for a credit.";
  if (days && !/^[-+]?\d{1,3}$/.test(days)) errors.schedule_days = "Enter the schedule change in whole days, like 3, or leave it blank.";
  if (refuseForm("newco", errors)) return null;
  return post(`/api/jobs/${jobId}/add-co`, { title: v.title, amount_cents: amount.cents, description: v.description, schedule_days: days ? Number(days) : 0 }, { formKey: "newco" });
}

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  "include-co": (el, id) => post(`/api/jobs/${id}/include-co/${el.dataset.co}`),
  "new-co": () => { newCoMade = null; toggleForm("newco"); requestAnimationFrame(() => document.getElementById("co-title")?.focus()); },
  "new-co-done": () => { newCoMade = null; formOpen.newco = false; clearForm("newco"); render(); },
  "add-co-here": async (el, id) => {
    const r = await addChangeOrder(id);
    if (!(r && r.ok)) return;
    const j = state.jobs.find(x => x.id === id);
    const co = j && j.change_orders.reduce((a, c) => (!a || c.number > a.number ? c : a), null);
    if (co) { newCoMade = { job: id, co: co.id }; render(); }
  },
};
