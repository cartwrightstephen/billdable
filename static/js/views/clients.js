// Client playbook: who each client is, its jobs and contracts, where pay apps go, the paperwork
// they need and how long they take to pay.
import { esc, unesc, escDeep, money, fmtDate } from "../format.js";
import { askConfirm } from "../dom.js";
import { field, formErrorLine, refuseForm, formValues, formErrors, clearForm } from "../forms.js";
import { CLIENT_TYPES, clientTypeLabel, paperworkChoices, packetChoices, packetText } from "../jobs.js";
import { readJson } from "../net.js";
import { pathFor } from "../routes.js";
import { state, me, formOpen, onSessionEnd } from "../state.js";
import { post, render, emptyState } from "../app-core.js";
import { sameCustomerBox, showSameCustomer, hideSameCustomer, keptNames, deletePayerHtml, payerJobCount, contractImportHtml, contractMark, forgetClosedContract } from "./jobs.js";

// Each client's jobs, finished ones too (S10), asked for when its card opens:
// payer id -> { forState, jobs } (jobs escaped). Asked again once the page's data changes.
const clientJobs = {};
onSessionEnd(() => { for (const k of Object.keys(clientJobs)) delete clientJobs[k]; ticks.clear(); openCards.clear(); });

function loadClientJobs(id) {
  const seen = state;
  const entry = clientJobs[id];
  if (entry && (entry.forState === seen || entry.loading)) return;
  clientJobs[id] = { ...(entry || {}), forState: seen, loading: true };
  if (typeof fetch !== "function") return;
  fetch(`/api/payers/${encodeURIComponent(unesc(id))}/jobs`).then(r => (r.ok ? readJson(r) : null)).then(data => {
    if (state !== seen) { delete clientJobs[id]; return; }
    clientJobs[id] = { forState: seen, jobs: data ? escDeep(data.jobs || []) : [], loading: false };
    render();
  }).catch(() => { clientJobs[id] = { forState: null, jobs: entry ? entry.jobs : null, loading: false }; });
}

function jobRow(j) {
  return `<li class="client-job"><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a>
    <span class="sub">${j.code}${j.contract_number ? ` · Contract ${j.contract_number}` : ""} · ${money(j.contract_total_cents)} · ${j.closed ? "Finished" : "Open"}</span>
    ${j.contract_file_id ? `<a href="/api/files/${j.contract_file_id}" target="_blank" rel="noopener">Contract</a>` : ""}
    ${j.closed ? "" : `<button class="linkish" data-action="pick-files" data-job="${j.id}" data-kind="contract">Add contract</button>`}</li>`;
}

// The card's Jobs: open and finished, each with its contract.
function clientJobsHtml(p, open) {
  const entry = clientJobs[p.id];
  if (open && (!entry || entry.forState !== state)) loadClientJobs(p.id);
  const jobs = entry && entry.jobs;
  if (!jobs) return `<div class="client-jobs"><h3>Jobs</h3><p class="sub">Loading…</p></div>`;
  const list = (rows) => rows.length ? `<ul class="client-job-list">${rows.map(jobRow).join("")}</ul>` : `<p class="sub">None.</p>`;
  return `<div class="client-jobs">
      <h3>Open jobs</h3>${list(jobs.filter(j => !j.closed))}
      <h3>Finished jobs</h3>${list(jobs.filter(j => j.closed))}
    </div>`;
}

// The paperwork a client needs (S11): a checkbox per typical item, Other for the rest, and
// Nothing needed. What is ticked and not saved yet stays through a redraw.
const ticks = new Map();   // form key -> { checked: [labels], none: bool }
function paperworkHtml(key, p = null) {
  const catalog = state.paperwork_catalog;
  const saved = p ? packetChoices(p.packet_items || [], catalog) : { checked: [], other: "" };
  const t = ticks.get(key) || { checked: saved.checked, none: !!(p && p.no_paperwork) };
  return `<fieldset class="span-2 paperwork-choices" data-paperwork="${key}">
      <legend data-help="packet">Paperwork needed with each pay app</legend>
      ${paperworkChoices(catalog).map(([title, list]) => `<div class="paperwork-group"><h4>${title}</h4>${list.map((label, n) => {
        const id = `${key}-pw-${title === "At closeout" ? "c" : "p"}${n}`;
        return `<label class="check-line" for="${id}"><input type="checkbox" id="${id}" data-packet-choice="${esc(label)}"${t.checked.includes(label) ? " checked" : ""}${t.none ? " disabled" : ""}> ${esc(label)}</label>`;
      }).join("")}</div>`).join("")}
      ${field({ form: key, name: "packet_other", id: `${key}-pw-other`, label: "Other", value: saved.other, placeholder: "Certified payroll", wide: true })}
      <label class="check-line" for="${key}-pw-none"><input type="checkbox" id="${key}-pw-none" data-no-paperwork${t.none ? " checked" : ""}> Nothing needed</label>
    </fieldset>`;
}

// The ticked labels, Other and Nothing needed of a form, as the save sends them.
function paperworkValue(key, other) {
  const box = document.querySelector(`[data-paperwork="${key}"]`);
  if (!box) return null;
  const none = !!box.querySelector("[data-no-paperwork]")?.checked;
  const checked = [...box.querySelectorAll("[data-packet-choice]")].filter(i => i.checked).map(i => unesc(i.dataset.packetChoice));
  return { none, text: none ? "" : packetText(checked, other, state.paperwork_catalog) };
}

// The open cards, kept open through a redraw.
const openCards = new Set();

if (typeof document !== "undefined" && document.addEventListener) document.addEventListener("change", (e) => {
  const box = e.target && e.target.closest && e.target.closest("[data-paperwork]");
  if (!box) return;
  const key = box.dataset.paperwork;
  const none = !!box.querySelector("[data-no-paperwork]")?.checked;
  ticks.set(key, { none, checked: [...box.querySelectorAll("[data-packet-choice]")].filter(i => i.checked).map(i => unesc(i.dataset.packetChoice)) });
  box.querySelectorAll("[data-packet-choice]").forEach(i => { i.disabled = none; });
});

// A card opened: its jobs are asked for, and it stays open through a redraw.
if (typeof document !== "undefined" && document.addEventListener) document.addEventListener("toggle", (e) => {
  const card = e.target;
  if (!card || !card.dataset || !card.dataset.payerCard) return;
  if (!card.open) { openCards.delete(card.dataset.payerCard); return; }
  openCards.add(card.dataset.payerCard);
  const p = (state && state.payers || []).find(x => x.id === card.dataset.payerCard);
  if (p) loadClientJobs(p.id);
}, true);

// This module's pages, by the name their address opens (routes.js).
export const views = {
  playbook() {
    forgetClosedContract();
    const clean = (v) => (v && unesc(v) !== "None" ? v : "");
    const jobsOf = (p) => payerJobCount(p, state.jobs);
    const addForm = formOpen.addpayer ? `
      <div class="card" data-form-key="addpayer">
        <h2>Add client</h2>
        ${contractImportHtml("addpayer")}
        ${formErrorLine("addpayer")}
        <div class="form-grid">
          ${field({ form: "addpayer", name: "name", id: "ap-name", label: "Client name", helpKey: "customer_name", required: true, autocomplete: "off", rule: "name" , ...contractMark("addpayer", "name") })}
          ${sameCustomerBox("addpayer")}
          ${field({ form: "addpayer", name: "client_type", id: "ap-client-type", label: "Client type", helpKey: "role", options: CLIENT_TYPES, value: "gc", ...contractMark("addpayer", "client_type") })}
          ${field({ form: "addpayer", name: "approver", id: "ap-approver", label: "Who signs off on your bills", helpKey: "approver", placeholder: "The client, unless you say otherwise" })}
          ${field({ form: "addpayer", name: "ap_email", id: "ap-email", label: "Email for pay apps (can add later)", helpKey: "ap_email", type: "email", autocomplete: "off", rule: "email", placeholder: "name@example.com" , ...contractMark("addpayer", "ap_email") })}
          ${field({ form: "addpayer", name: "phone", id: "ap-phone", label: "Phone", helpKey: "customer_phone", type: "tel", autocomplete: "off", rule: "phone" , ...contractMark("addpayer", "phone") })}
          ${field({ form: "addpayer", name: "payapp_due_day", id: "ap-due-day", label: "Pay apps due by (day of the month)", helpKey: "payapp_due_day", inputmode: "numeric", autocomplete: "off", placeholder: "e.g. 25" , ...contractMark("addpayer", "payapp_due_day") })}
          ${field({ form: "addpayer", name: "payment_terms_days", id: "ap-terms", label: "Payment terms, days", helpKey: "payment_terms_days", inputmode: "numeric", autocomplete: "off", placeholder: "30" , ...contractMark("addpayer", "payment_terms_days") })}
          ${paperworkHtml("addpayer")}
        </div>
        <div class="form-inline"><button class="btn" data-action="add-payer">Add client</button></div>
      </div>` : "";
    return `
      <div class="job-head">
        <div>
          <h1>Client playbook</h1>
        </div>
        <button class="btn secondary small" data-action="toggle-form" data-form="addpayer" aria-expanded="${!!formOpen.addpayer}">${formOpen.addpayer ? "Cancel" : "Add client"}</button>
      </div>
      ${addForm}
      ${state.payers.length ? state.payers.map(p => {
        const key = `payer-${p.id}`, f = (o) => field({ form: key, id: `${key}-${o.name}`, ...o });
        const others = state.payers.filter(o => o.id !== p.id);
        const n = jobsOf(p);
        const open = (p.packet_suggestions || []).some(x => x.status === "open") || !!formErrors[key] || openCards.has(p.id);
        return `
        <details class="card playbook-card" data-payer-card="${p.id}"${open ? " open" : ""}>
          <summary><strong>${p.name}</strong> <span class="sub">${clientTypeLabel(p)} · ${p.approver || "no one signs off yet"} · ${p.ap_email || "no email yet"} · ${n === 1 ? "1 job" : `${n} jobs`}</span>${(p.packet_suggestions || []).some(x => x.status === "open") ? ` <span class="pill copper">${p.packet_suggestions.filter(x => x.status === "open").length} suggested</span>` : ""}</summary>
          ${(p.packet_suggestions || []).filter(x => x.status === "open").map(x => `<div class="list-item suggestion">
            <div><strong>Add to the paperwork?</strong> “${x.text}” <span class="sub">from ${x.source}${x.job ? " on " + x.job : ""}, ${fmtDate(x.at)}</span></div>
            <div class="form-inline"><button class="btn small" data-action="sug-add" data-id="${p.id}" data-sug="${x.id}">Add to paperwork</button><button class="btn small secondary" data-action="sug-dismiss" data-id="${p.id}" data-sug="${x.id}">Dismiss</button></div>
          </div>`).join("")}
          ${clientJobsHtml(p, open)}
          ${p.paid_count ? `<p class="sub payer-timing">Pays in about ${(n => n === 1 ? "1 day" : `${n} days`)(Math.round(Number(unesc(String(p.paid_days_avg)))))} after a pay app is sent, on average (${p.paid_count} paid). ${(() => {
            const avg = Math.round(Number(unesc(String(p.paid_days_avg)))), set = Number(unesc(String(p.usual_approval_days || 14)));
            if (!(p.paid_count >= 2 && Math.abs(avg - set) >= 2)) return "That matches your setting.";
            // Offered only for a number the server takes: 1 to 365 days (domain.update_payer).
            return avg >= 1 && avg <= 365
              ? `Your setting says ${set}. <button class="btn small secondary" data-action="use-approval-days" data-id="${p.id}" data-days="${avg}">Use ${avg} day${avg === 1 ? "" : "s"}</button>`
              : `Your setting says ${set}.`;
          })()}</p>` : ""}
          <div data-form-key="${key}">
            ${formErrorLine(key)}
            <div class="form-grid">
              ${f({ name: "name", label: "Client name", helpKey: "customer_name", required: true, autocomplete: "off", rule: "name", value: unesc(p.name) })}
              ${f({ name: "client_type", label: "Client type", helpKey: "role", options: CLIENT_TYPES, value: unesc(p.client_type || (unesc(p.role || "") === "GC" ? "gc" : "owner")) })}
              ${f({ name: "approver", label: "Who signs off on your bills", helpKey: "approver", value: unesc(clean(p.approver)) })}
              ${f({ name: "ap_email", label: "Email for pay apps", helpKey: "ap_email", type: "email", autocomplete: "off", rule: "email", value: unesc(clean(p.ap_email)) })}
              ${f({ name: "phone", label: "Phone", helpKey: "customer_phone", type: "tel", autocomplete: "off", rule: "phone", value: unesc(clean(p.phone)) })}
              ${f({ name: "follow_up_days", label: "Days between follow-ups", helpKey: "follow_up_days", inputmode: "numeric", autocomplete: "off", value: String(p.follow_up_days || ""), placeholder: "7" })}
              ${f({ name: "payapp_due_day", label: "Pay apps due by (day of the month)", helpKey: "payapp_due_day", inputmode: "numeric", autocomplete: "off", value: String(p.payapp_due_day || ""), placeholder: "e.g. 25" })}
              ${f({ name: "usual_approval_days", label: "Days they usually take to pay", helpKey: "usual_approval_days", inputmode: "numeric", autocomplete: "off", value: String(p.usual_approval_days || ""), placeholder: "14" })}
              ${f({ name: "payment_terms_days", label: "Payment terms, days", helpKey: "payment_terms_days", inputmode: "numeric", autocomplete: "off", value: String(p.payment_terms_days || ""), placeholder: "30" })}
              ${paperworkHtml(key, p)}
              ${f({ name: "rejects_when", label: "They send pay apps back when", helpKey: "rejects_when", multiline: true, wide: true, value: unesc(clean(p.rejects_when)) })}
              ${f({ name: "copy", label: "Also email (CC)", helpKey: "copy", wide: true, autocomplete: "off", value: unesc(clean(p.copy)), placeholder: "other@example.com, pm@example.com" })}
              ${f({ name: "escalation_email", label: "Also email from the third follow-up", helpKey: "escalation_email", type: "email", autocomplete: "off", value: unesc(clean(p.escalation_email)), placeholder: "owner@example.com" })}
            </div>
            <div class="form-inline"><button type="button" class="btn small" data-action="save-playbook" data-id="${p.id}">Save client</button></div>
          </div>
          ${others.length ? `<details class="merge-payer">
            <summary>Same client as another one?</summary>
            <div class="form-inline">
              <label for="merge-${p.id}" class="inline-label">Merge into</label>
              <select id="merge-${p.id}" data-merge-into>${others.map(o => `<option value="${o.id}">${o.name}</option>`).join("")}</select>
              <button type="button" class="btn small secondary" data-action="merge-payer" data-id="${p.id}" data-name="${p.name}">Merge</button>
            </div>
            <p class="sub">Every job of ${p.name} moves to the client you pick, which keeps its own settings. ${p.name} is then removed.</p>
          </details>` : ""}
          ${deletePayerHtml(p, { owner: !me || me.role === "owner", jobs: state.jobs, canMerge: others.length > 0 })}
        </details>`;
      }).join("") : emptyState("No clients yet", "", `<button class="btn" data-action="toggle-form" data-form="addpayer">Add client</button>`)}
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
    // Blank clears a field (the day fields go back to the usual number), except who signs off and
    // the email, which a send needs (R3-07). The paperwork is the ticked boxes and Other (S11);
    // nothing ticked is refused unless Nothing needed is, which the server keeps as no paperwork.
    for (const k of ["approver", "ap_email"]) if (!updates[k]) delete updates[k];
    const paperwork = paperworkValue(key, updates.packet_other);
    delete updates.packet_other;
    const errors = {};
    if (paperwork) {
      if (!paperwork.none && !paperwork.text) errors.packet = "Tick what they need with each pay app, or Nothing needed.";
      updates.packet = paperwork.text;
    }
    if (!updates.name) errors.name = "Enter the client's name.";
    for (const [k, label] of [["follow_up_days", "the days between follow-ups"], ["usual_approval_days", "the days they usually take to pay"], ["payment_terms_days", "the payment terms in days"]]) {
      if (updates[k] && !/^\d+$/.test(updates[k])) errors[k] = `Enter ${label} as a whole number from 1 to 365.`;
    }
    if (refuseForm(key, errors)) return;
    const r = await post(`/api/payers/${id}/update`, updates, { formKey: key });
    if (r && r.ok) ticks.delete(key);
  },
  "add-payer": async () => {
    const v = formValues("addpayer");
    if (refuseForm("addpayer", v.name ? {} : { name: "Enter the client's name." })) return;
    const paperwork = paperworkValue("addpayer", v.packet_other);
    delete v.packet_other;
    // Nothing ticked: the client starts with the usual paperwork for its type.
    const body = { ...v, new_customer: keptNames.addpayer === v.name };
    if (paperwork && paperwork.none) body.no_paperwork = true;
    else if (paperwork && paperwork.text) body.packet = paperwork.text;
    for (const k of ["payapp_due_day", "payment_terms_days"]) if (!body[k]) delete body[k];
    const r = await post("/api/payers", body, { formKey: "addpayer", close: "addpayer" });
    if (r && !r.ok) showSameCustomer("addpayer", "ap", r.data);
    if (r && r.ok) { delete keptNames.addpayer; ticks.delete("addpayer"); }
  },
  // "Same as ABC Builders?" while adding a client: open the saved one instead...
  "ap-same-use": (el) => {
    formOpen.addpayer = false;
    clearForm("addpayer");
    render();
    const card = document.querySelector(`[data-payer-card="${CSS.escape(el.dataset.id || "")}"]`);
    if (card) { card.open = true; card.scrollIntoView({ block: "start", behavior: "smooth" }); }
  },
  // ...or add the typed name as a new client, on purpose.
  "ap-same-keep": (el) => {
    keptNames.addpayer = el.dataset.name || "";
    hideSameCustomer("addpayer");
    return actions["add-payer"]();
  },
  "delete-payer": async (el, id) => {
    const name = unesc(el.dataset.name || "this client");
    if (!(await askConfirm(`Delete ${name}? No job uses this client. It is removed from the Client playbook, and the activity log keeps a note of it.`, "Delete client", true))) return;
    await post(`/api/payers/${id}/delete`, {});
  },
  "merge-payer": async (el, id) => {
    const select = el.closest(".merge-payer").querySelector("[data-merge-into]");
    const into = state.payers.find(p => p.id === select.value);
    const from = state.payers.find(p => p.id === id);
    if (!into) return;
    const n = from ? payerJobCount(from, state.jobs) : state.jobs.filter(j => j.payer_id === id).length;
    const name = unesc(el.dataset.name || "this client");
    const jobs = n === 0 ? "It has no jobs." : `${n === 1 ? "Its job moves" : `Its ${n} jobs move`} to ${unesc(into.name)}.`;
    if (!(await askConfirm(`Merge ${name} into ${unesc(into.name)}? ${jobs} ${unesc(into.name)} keeps its own settings, and ${name} is removed.`, "Merge", true))) return;
    await post(`/api/payers/${id}/merge`, { into: into.id });
  },
};
