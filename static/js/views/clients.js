// Customer settings: who each customer is, where pay apps go, the paperwork they need and how
// long they take to pay.
import { unesc, fmtDate } from "../format.js";
import { askConfirm } from "../dom.js";
import { field, formErrorLine, refuseForm, formValues, formErrors, clearForm } from "../forms.js";
import { state, me, formOpen } from "../state.js";
import { post, render, emptyState } from "../app-core.js";
import { ROLE_OPTIONS, roleLabel, sameCustomerBox, showSameCustomer, hideSameCustomer, keptNames, deletePayerHtml, payerJobCount } from "./jobs.js";


// This module's pages, by the name their address opens (routes.js).
export const views = {
  playbook() {
    const clean = (v) => (v && unesc(v) !== "None" ? v : "");
    const jobsOf = (p) => payerJobCount(p, state.jobs);
    const addForm = formOpen.addpayer ? `
      <div class="card" data-form-key="addpayer">
        <h2>Add customer</h2>
        ${formErrorLine("addpayer")}
        <div class="form-grid">
          ${field({ form: "addpayer", name: "name", id: "ap-name", label: "Customer name", helpKey: "customer_name", required: true, autocomplete: "off" })}
          ${sameCustomerBox("addpayer")}
          ${field({ form: "addpayer", name: "role", id: "ap-role", label: "Who pays you?", helpKey: "role", options: ROLE_OPTIONS })}
          ${field({ form: "addpayer", name: "approver", id: "ap-approver", label: "Who signs off on your bills", helpKey: "approver", placeholder: "The customer, unless you say otherwise" })}
          ${field({ form: "addpayer", name: "ap_email", id: "ap-email", label: "Email for pay apps (can add later)", helpKey: "ap_email", type: "email", autocomplete: "off", placeholder: "name@example.com" })}
          ${field({ form: "addpayer", name: "phone", id: "ap-phone", label: "Phone", helpKey: "customer_phone", type: "tel", autocomplete: "off" })}
        </div>
        <div class="form-inline"><button class="btn" data-action="add-payer">Add customer</button></div>
      </div>` : "";
    return `
      <div class="job-head">
        <div>
          <h1>Customer settings</h1>
          <p class="sub">Who each customer is, where pay apps go, the paperwork they need, and how long they take to pay. These set follow-up timing and the calendar.</p>
        </div>
        <button class="btn secondary small" data-action="toggle-form" data-form="addpayer" aria-expanded="${!!formOpen.addpayer}">${formOpen.addpayer ? "Cancel" : "Add customer"}</button>
      </div>
      ${addForm}
      ${state.payers.length ? state.payers.map(p => {
        const key = `payer-${p.id}`, f = (o) => field({ form: key, id: `${key}-${o.name}`, ...o });
        const others = state.payers.filter(o => o.id !== p.id);
        const n = jobsOf(p);
        return `
        <details class="card playbook-card" data-payer-card="${p.id}"${(p.packet_suggestions || []).some(x => x.status === "open") || formErrors[key] ? " open" : ""}>
          <summary><strong>${p.name}</strong> <span class="sub">${roleLabel(p.role)} · ${p.approver || "no one signs off yet"} · ${p.ap_email || "no email yet"} · ${n === 1 ? "1 job" : `${n} jobs`}</span>${(p.packet_suggestions || []).some(x => x.status === "open") ? ` <span class="pill copper">${p.packet_suggestions.filter(x => x.status === "open").length} suggested</span>` : ""}</summary>
          ${(p.packet_suggestions || []).filter(x => x.status === "open").map(x => `<div class="list-item suggestion">
            <div><strong>Add to the paperwork?</strong> “${x.text}” <span class="sub">from ${x.source}${x.job ? " on " + x.job : ""}, ${fmtDate(x.at)}</span></div>
            <div class="form-inline"><button class="btn small" data-action="sug-add" data-id="${p.id}" data-sug="${x.id}">Add to paperwork</button><button class="btn small secondary" data-action="sug-dismiss" data-id="${p.id}" data-sug="${x.id}">Dismiss</button></div>
          </div>`).join("")}
          ${p.paid_count ? `<p class="sub payer-timing">Pays in about ${Math.round(Number(unesc(String(p.paid_days_avg))))} days after a pay app is sent, on average (${p.paid_count} paid). ${(() => {
            const avg = Math.round(Number(unesc(String(p.paid_days_avg)))), set = Number(unesc(String(p.usual_approval_days || 14)));
            if (!(p.paid_count >= 2 && Math.abs(avg - set) >= 2)) return "That matches your setting.";
            // Offered only for a number the server takes: 1 to 365 days (domain.update_payer).
            return avg >= 1 && avg <= 365
              ? `Your setting says ${set}. <button class="btn small secondary" data-action="use-approval-days" data-id="${p.id}" data-days="${avg}">Use ${avg} days</button>`
              : `Your setting says ${set}.`;
          })()}</p>` : ""}
          <div data-form-key="${key}">
            ${formErrorLine(key)}
            <div class="form-grid">
              ${f({ name: "name", label: "Customer name", helpKey: "customer_name", required: true, autocomplete: "off", value: unesc(p.name) })}
              ${f({ name: "role", label: "Who pays you?", helpKey: "role", options: ROLE_OPTIONS, value: unesc(p.role || "Owner") })}
              ${f({ name: "approver", label: "Who signs off on your bills", helpKey: "approver", value: unesc(clean(p.approver)) })}
              ${f({ name: "ap_email", label: "Email for pay apps", helpKey: "ap_email", type: "email", autocomplete: "off", value: unesc(clean(p.ap_email)) })}
              ${f({ name: "phone", label: "Phone", helpKey: "customer_phone", type: "tel", autocomplete: "off", value: unesc(clean(p.phone)) })}
              ${f({ name: "follow_up_days", label: "Days between follow-ups", helpKey: "follow_up_days", inputmode: "numeric", autocomplete: "off", value: String(p.follow_up_days || ""), placeholder: "7" })}
              ${f({ name: "payapp_due_day", label: "Pay apps due by (day of the month)", helpKey: "payapp_due_day", inputmode: "numeric", autocomplete: "off", value: String(p.payapp_due_day || ""), placeholder: "e.g. 25" })}
              ${f({ name: "usual_approval_days", label: "Days they usually take to pay", helpKey: "usual_approval_days", inputmode: "numeric", autocomplete: "off", value: String(p.usual_approval_days || ""), placeholder: "14" })}
              ${f({ name: "payment_terms_days", label: "Payment terms, days", helpKey: "payment_terms_days", inputmode: "numeric", autocomplete: "off", value: String(p.payment_terms_days || ""), placeholder: "30" })}
              ${f({ name: "packet", label: "Paperwork needed with each pay app", helpKey: "packet", multiline: true, wide: true, value: unesc(clean(p.packet)), placeholder: p.no_paperwork ? "Nothing needed" : "Lien waiver, insurance certificate" })}
              ${f({ name: "rejects_when", label: "They send pay apps back when", helpKey: "rejects_when", multiline: true, wide: true, value: unesc(clean(p.rejects_when)) })}
              ${f({ name: "copy", label: "Also email (CC)", helpKey: "copy", wide: true, autocomplete: "off", value: unesc(clean(p.copy)), placeholder: "other@example.com, pm@example.com" })}
              ${f({ name: "escalation_email", label: "Also email from the third follow-up", helpKey: "escalation_email", type: "email", autocomplete: "off", value: unesc(clean(p.escalation_email)), placeholder: "owner@example.com" })}
            </div>
            <div class="form-inline"><button type="button" class="btn small" data-action="save-playbook" data-id="${p.id}">Save customer</button></div>
          </div>
          ${others.length ? `<details class="merge-payer">
            <summary>Same customer as another one?</summary>
            <div class="form-inline">
              <label for="merge-${p.id}" class="inline-label">Merge into</label>
              <select id="merge-${p.id}" data-merge-into>${others.map(o => `<option value="${o.id}">${o.name}</option>`).join("")}</select>
              <button type="button" class="btn small secondary" data-action="merge-payer" data-id="${p.id}" data-name="${p.name}">Merge</button>
            </div>
            <p class="sub">Every job of ${p.name} moves to the customer you pick, which keeps its own settings. ${p.name} is then removed.</p>
          </details>` : ""}
          ${deletePayerHtml(p, { owner: !me || me.role === "owner", jobs: state.jobs, canMerge: others.length > 0 })}
        </details>`;
      }).join("") : emptyState("No customers yet", "Add one here, or one is added with the first job for that customer.", `<button class="btn" data-action="toggle-form" data-form="addpayer">Add customer</button>`)}
    `;
  },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  "use-approval-days": async (el, id) => { await post(`/api/payers/${id}/update`, { usual_approval_days: Number(el.dataset.days) }); },
  "sug-add": (el, id) => post(`/api/payers/${id}/suggestion/${el.dataset.sug}/add`),
  "sug-dismiss": (el, id) => post(`/api/payers/${id}/suggestion/${el.dataset.sug}/dismiss`),
  "save-playbook": async (el, id) => {
    const key = `payer-${id}`;
    const updates = formValues(key);
    // Blank clears a field (the day fields go back to the usual number, an empty paperwork list
    // means nothing is needed), except who signs off and the email, which a send needs (R3-07).
    for (const k of ["approver", "ap_email"]) if (!updates[k]) delete updates[k];
    const errors = {};
    if (!updates.name) errors.name = "Enter the customer's name.";
    for (const [k, label] of [["follow_up_days", "the days between follow-ups"], ["usual_approval_days", "the days they usually take to pay"], ["payment_terms_days", "the payment terms in days"]]) {
      if (updates[k] && !/^\d+$/.test(updates[k])) errors[k] = `Enter ${label} as a whole number from 1 to 365.`;
    }
    if (refuseForm(key, errors)) return;
    await post(`/api/payers/${id}/update`, updates, { formKey: key });
  },
  "add-payer": async () => {
    const v = formValues("addpayer");
    if (refuseForm("addpayer", v.name ? {} : { name: "Enter the customer's name." })) return;
    const r = await post("/api/payers", { ...v, new_customer: keptNames.addpayer === v.name }, { formKey: "addpayer", close: "addpayer" });
    if (r && !r.ok) showSameCustomer("addpayer", "ap", r.data);
    if (r && r.ok) delete keptNames.addpayer;
  },
  // "Same as ABC Builders?" while adding a customer: open the saved one instead...
  "ap-same-use": (el) => {
    formOpen.addpayer = false;
    clearForm("addpayer");
    render();
    const card = document.querySelector(`[data-payer-card="${CSS.escape(el.dataset.id || "")}"]`);
    if (card) { card.open = true; card.scrollIntoView({ block: "start", behavior: "smooth" }); }
  },
  // ...or add the typed name as a new customer, on purpose.
  "ap-same-keep": (el) => {
    keptNames.addpayer = el.dataset.name || "";
    hideSameCustomer("addpayer");
    return actions["add-payer"]();
  },
  "delete-payer": async (el, id) => {
    const name = unesc(el.dataset.name || "this customer");
    if (!(await askConfirm(`Delete ${name}? No job uses this customer. It is removed from Customer settings, and the activity log keeps a note of it.`, "Delete customer", true))) return;
    await post(`/api/payers/${id}/delete`, {});
  },
  "merge-payer": async (el, id) => {
    const select = el.closest(".merge-payer").querySelector("[data-merge-into]");
    const into = state.payers.find(p => p.id === select.value);
    const from = state.payers.find(p => p.id === id);
    if (!into) return;
    const n = from ? payerJobCount(from, state.jobs) : state.jobs.filter(j => j.payer_id === id).length;
    const name = unesc(el.dataset.name || "this customer");
    const jobs = n === 0 ? "It has no jobs." : `${n === 1 ? "Its job moves" : `Its ${n} jobs move`} to ${unesc(into.name)}.`;
    if (!(await askConfirm(`Merge ${name} into ${unesc(into.name)}? ${jobs} ${unesc(into.name)} keeps its own settings, and ${name} is removed.`, "Merge", true))) return;
    await post(`/api/payers/${id}/merge`, { into: into.id });
  },
};
