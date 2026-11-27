// Pay-app core (Nov 27): a job started mid-project with the pay apps it already had, retainage by
// schedule line and for one pay app, the retainage-only pay app, and each stored-materials line's
// backup. The rules are the server's (domain/payapp_core.py); this draws them and sends what is typed.
// j comes escaped from the state, so its fields go in as they are.
import { esc, unesc, money, centsFrom, fmtDate } from "../format.js";
import { field, moneyField, formErrorLine, refuseForm, formValues, draftValue, clearForm } from "../forms.js";
import { askConfirm, focusAfterDraw } from "../dom.js";
import { state, formOpen } from "../state.js";
import { may, post, render, nav } from "../app-core.js";

// "Pay app by pay app" or "Totals to date", and per line dollars or % complete.
let earlierMode = "payapp";
let earlierLinesAs = "amount";

function todayIso(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// ---------- earlier pay apps ----------

function earlierRowsHtml(j) {
  const rows = (j.invoices || []).filter(i => i.imported && i.status !== "void");
  if (!rows.length) return "";
  return rows.map(i => {
    const paid = (j.payments || []).filter(p => p.invoice_id === i.id).reduce((a, p) => a + p.amount_cents, 0);
    const files = (i.history_files || []).flatMap(h => (h.file_ids || []).map(f => `<a href="/api/files/${f}" target="_blank" rel="noopener">${h.label}</a>`));
    return `<div class="list-item earlier-row">
      <strong>${i.number}</strong> · to ${fmtDate(i.period_to)} · ${money(i.gross_cents)} of work${i.retainage_cents ? `, ${money(i.retainage_cents)} retainage held` : ""} · ${money(i.amount_cents)} billed · ${money(paid)} paid
      <div class="sub row-sub">${files.length ? `Copy: ${files.join(", ")}` : "No copy kept."}
        ${!j.closed ? `<button class="linkish" data-action="pick-files" data-job="${j.id}" data-kind="pay_app" data-history="${i.id}">Add the old PDF</button>` : ""}</div>
    </div>`;
  }).join("");
}

// The lines earlier pay apps are entered on: the schedule's, or for a lump-sum job from before
// every job had one, the contract amount as one line (the server makes it on save; Nov 27 audit).
export function earlierLines(j) {
  const v = j.sov_view || {};
  if (v.present) return v.lines || [];
  const contract = (j.money && j.money.contract_cents) || j.contract_cents || 0;
  return [{ id: "contract", item: "1", description: "Contract amount", scheduled_cents: contract, billed_cents: 0 }];
}

function lineBoxes(j, form) {
  const lines = earlierLines(j);
  const summary = earlierMode === "summary";
  const head = earlierLinesAs === "pct" ? "% complete to date" : summary ? "Billed to date ($)" : "Billed this period ($)";
  return `<div class="table-wrap sov-wrap"><table class="sov-table">
    <tr><th>Item</th><th>Description</th><th>Scheduled value</th><th>Billed before</th><th>${head}</th></tr>
    ${lines.map(l => `<tr><td>${l.item}</td><td class="sov-desc-cell">${l.description}</td><td class="num">${money(l.scheduled_cents)}</td>
      <td class="num">${money(l.billed_cents || 0)}</td>
      <td><input id="ep-line-${l.id}" class="sov-in sov-num" data-field="line_${l.id}" value="${esc(draftValue(form, `line_${l.id}`, ""))}" inputmode="decimal" aria-label="${l.description}: ${head}"></td></tr>`).join("")}
  </table></div>`;
}

function earlierFormHtml(j) {
  const form = "earlier";
  const e = j.earlier_payapps || {};
  const summary = earlierMode === "summary";
  const seg = (key, label, cur, act) => `<button class="${cur === key ? "btn" : "btn secondary"} small" data-action="${act}" data-mode="${key}" aria-pressed="${cur === key}">${label}</button>`;
  const next = unesc(e.next_number || "").replace(/^.*-0*/, "");
  return `<div data-form-key="${form}" class="earlier-form">${formErrorLine(form)}
    <p class="sub">Enter what was billed before Duracash. Pay apps made here then start at the next number, and their "previous" column counts these. Nothing here is emailed or put in QuickBooks.</p>
    <div class="filter-bar" role="group" aria-label="Enter">${seg("payapp", "Pay app by pay app", earlierMode, "earlier-mode")}${seg("summary", "Totals to date", earlierMode, "earlier-mode")}</div>
    <div class="filter-bar" role="group" aria-label="Lines as">${seg("amount", "Dollars", earlierLinesAs, "earlier-lines-as")}${seg("pct", "% complete", earlierLinesAs, "earlier-lines-as")}</div>
    <div class="form-grid">
      ${field({ form, name: "number_1", id: "ep-number", label: summary ? "Last pay app number" : "Pay app number", inputmode: "numeric", value: next, required: true })}
      ${field({ form, name: "period_to_1", id: "ep-period", label: "Period to", type: "date", required: true, attrs: `max="${todayIso()}"` })}
      ${moneyField({ form, name: "retainage_1", id: "ep-ret", label: summary ? "Retainage held to date ($)" : "Retainage held on it ($)", help: "Blank: the job's and lines' percents." })}
      ${moneyField({ form, name: "paid_1", id: "ep-paid", label: summary ? "Total paid ($)" : "Amount paid ($)" })}
      ${field({ form, name: "paid_on_1", id: "ep-paid-on", label: "Date paid", type: "date", attrs: `max="${todayIso()}"` })}
    </div>
    ${lineBoxes(j, form)}
    <div class="form-inline">
      <button class="btn small" data-action="earlier-save" data-id="${j.id}">${summary ? "Save totals" : "Save pay app"}</button>
      <button class="btn small secondary" data-action="toggle-form" data-form="earlier">Done</button>
    </div></div>`;
}

// The job page's Earlier pay apps card: shown once any are entered, or while the form is open.
export function earlierCardHtml(j) {
  const e = j.earlier_payapps || {};
  const open = formOpen.earlier && e.can_enter && may("earlier-payapps");
  if (!e.count && !open) return "";
  return `<div class="card earlier-payapps" id="earlier-card">
    <h2>Earlier pay apps (before Duracash)</h2>
    ${earlierRowsHtml(j)}
    ${open ? earlierFormHtml(j)
      : e.can_enter && may("earlier-payapps") ? `<div class="form-inline"><button class="btn small secondary" data-action="earlier-open" data-id="${j.id}">Enter another</button>
        ${e.count ? `<button class="btn small secondary danger-text" data-action="earlier-remove" data-id="${j.id}">Take them off</button>` : ""}</div>` : ""}
    ${e.count ? `<p class="sub">The next pay app here is ${e.next_number}.</p>` : ""}
  </div>`;
}

// The way in, from Edit job: "This job already has pay apps".
export function earlierLinkHtml(j) {
  const e = j.earlier_payapps || {};
  if (!e.can_enter || e.count || formOpen.earlier || !may("earlier-payapps")) return "";
  return `<button class="linkish" data-action="earlier-open" data-id="${j.id}">This job already has pay apps</button>`;
}

async function earlierSave(jobId) {
  const v = formValues("earlier");
  const j = state.jobs.find(x => x.id === jobId);
  const lines = {};
  const viewLines = j ? earlierLines(j) : [];
  for (const l of viewLines) {
    const raw = v[`line_${unesc(l.id)}`] || "";
    if (!raw) continue;
    lines[unesc(l.id)] = earlierLinesAs === "pct" ? { pct: raw.replace(/%$/, "") } : earlierMode === "summary" ? { to_date: raw } : { amount: raw };
  }
  const errors = {};
  if (!v.number_1) errors.number_1 = "Enter the pay app's number, like 3.";
  if (!v.period_to_1) errors.period_to_1 = "Enter the date it ran to.";
  for (const k of ["retainage_1", "paid_1"]) if (v[k] && centsFrom(v[k]).error) errors[k] = centsFrom(v[k]).error;
  if (!Object.keys(lines).length) errors._form = "Enter what was billed on at least one line.";
  if (refuseForm("earlier", errors)) return;
  const app = { number: v.number_1, period_to: v.period_to_1, lines, retainage: v.retainage_1 ? (centsFrom(v.retainage_1).cents / 100).toFixed(2) : "",
    paid: v.paid_1 ? (centsFrom(v.paid_1).cents / 100).toFixed(2) : "", paid_on: v.paid_on_1 || "" };
  const r = await post(`/api/jobs/${jobId}/earlier-payapps`, earlierMode === "summary" ? { summary: app } : { payapps: [app] }, { formKey: "earlier" });
  if (r && r.ok) {
    clearForm("earlier");
    if (earlierMode === "summary") formOpen.earlier = false;
    render();
  }
}

// ---------- retainage by line, and for this pay app ----------

export function lineRetainageHtml(j) {
  const v = j.sov_view || {};
  if (!v.present || j.closed || !may("line-retainage")) return "";
  const form = "lineret";
  const open = !!formOpen.lineret;
  const set = (v.lines || []).filter(l => Number.isInteger(l.retainage_pct));
  return `<details class="card line-retainage" data-more="line-retainage"${open ? " open" : ""}>
    <summary><h2>Retainage by line</h2><span class="sub">${set.length ? `${set.length} ${set.length === 1 ? "line with its" : "lines with their"} own percent` : `Every line at the job's ${Number(j.retainage_pct) || 0}%`}</span></summary>
    <div data-form-key="${form}">${formErrorLine(form)}
    <p class="sub">A line can hold its own retainage, like materials at 0% or 5% and labor at 10%. Blank is the job's percent. It applies from the next pay app.</p>
    <div class="table-wrap sov-wrap"><table class="sov-table"><tr><th>Item</th><th>Description</th><th>Retainage %</th></tr>
      ${(v.lines || []).map(l => `<tr><td>${l.item}</td><td class="sov-desc-cell">${l.description}</td>
        <td><span class="with-suffix inline"><input id="lr-${l.id}" class="sov-in sov-num" data-field="pct_${l.id}" value="${esc(draftValue(form, `pct_${l.id}`, Number.isInteger(l.retainage_pct) ? String(l.retainage_pct) : ""))}" placeholder="${Number(j.retainage_pct) || 0}" inputmode="numeric" aria-label="${l.description}: retainage percent"><span class="suffix" aria-hidden="true">%</span></span></td></tr>`).join("")}
    </table></div>
    <button class="btn small" data-action="line-retainage" data-id="${j.id}">Save retainage by line</button></div>
  </details>`;
}

async function lineRetainageSave(jobId) {
  const j = state.jobs.find(x => x.id === jobId);
  const v = formValues("lineret");
  const pcts = {};
  const viewLines = (j && j.sov_view && j.sov_view.lines) || [];
  for (const l of viewLines) {
    const raw = (v[`pct_${unesc(l.id)}`] || "").replace(/%$/, "");
    const was = Number.isInteger(l.retainage_pct) ? String(l.retainage_pct) : "";
    if (raw !== was) pcts[unesc(l.id)] = raw;
  }
  if (!Object.keys(pcts).length) { refuseForm("lineret", { _form: "Change a percent first." }); return; }
  formOpen.lineret = true;
  await post(`/api/jobs/${jobId}/line-retainage`, { pcts }, { formKey: "lineret" });
}

// On a draft's page: one percent on everything this period, in place of the lines' and the job's.
export function payappRetainageHtml(j, inv) {
  if (!["draft", "awaiting_approval"].includes(inv.status) || inv.retainage_release || inv.pencil_status === "sent") return "";
  const key = `payret-${inv.id}`;
  const over = inv.retainage_pct_override;
  return `<div class="payapp-retainage" data-form-key="${key}">${formErrorLine(key)}
    <p class="sub">Retainage on this pay app: ${money(inv.retainage_cents || 0)}${over != null ? `, ${over}% for this period only` : ""}.</p>
    <div class="form-inline">${field({ form: key, name: "pct", id: `${key}-pct`, label: "Retainage this period", suffix: "%", inputmode: "numeric", value: over != null ? String(over) : "" })}
      <button class="btn small secondary" data-action="payapp-retainage" data-id="${j.id}" data-inv="${inv.id}">${over != null ? "Change" : "Set for this period"}</button>
      ${over != null ? `<button class="linkish" data-action="payapp-retainage" data-id="${j.id}" data-inv="${inv.id}" data-clear="1">Back to the usual percents</button>` : ""}</div></div>`;
}

// ---------- the retainage-only pay app ----------

export function releaseCardHtml(j) {
  const r = j.retainage_release;
  if (!r || r.held_cents <= 0 || !may("retainage-release")) return "";
  const key = "retrelease";
  if (!formOpen.retrelease) {
    return `<div class="card retainage-release"><h2>Retainage held: ${money(r.held_cents)}</h2>
      <p class="sub">${r.drafted_cents ? `${money(r.drafted_cents)} is on a release pay app that is still a draft.` : "Bill it back as its own pay app: all of it or part, at substantial completion or once the job is finished."}</p>
      ${r.open_draft ? `<p class="sub">Send or delete the open draft first.</p>` : `<button class="btn small secondary" data-action="toggle-form" data-form="retrelease">Bill retainage release</button>`}</div>`;
  }
  const stage = draftValue(key, "stage", r.final_ready ? "final" : "substantial");
  return `<div class="card form-card retainage-release" data-form-key="${key}"><h2>Bill retainage release</h2>${formErrorLine(key)}
    <p class="sub">A pay app with no new work: it bills ${money(r.held_cents)} of retainage held, or the part you enter. Its lien waiver follows its payment, as for any pay app.</p>
    <div class="form-row">
      ${moneyField({ form: key, name: "amount_cents", id: "rr-amt", label: "Amount ($), blank for all", placeholder: money(r.held_cents) })}
      ${field({ form: key, name: "stage", id: "rr-stage", label: "When", buttons: true, value: stage, options: [["substantial", "Substantial completion"], ["final", "Final"]] })}
      ${field({ form: key, name: "period_to", id: "rr-period", label: "Period to", type: "date", value: todayIso() })}
    </div>
    ${stage === "final" && !r.final_ready ? `<p class="sub">${r.work_done ? "The final release waits for the closeout documents and the last of the work to be billed." : "The final release is billed once the work is finished (Final pay app)."}</p>` : ""}
    <div class="form-inline"><button class="btn small" data-action="retainage-release" data-id="${j.id}">Draft the release</button>
      <button class="btn small secondary" data-action="toggle-form" data-form="retrelease">Cancel</button></div></div>`;
}

// ---------- stored materials backup ----------

export function storedBackupHtml(j, inv) {
  const rows = inv.stored_backup || [];
  if (!rows.length) return "";
  const draft = ["draft", "awaiting_approval"].includes(inv.status);
  return `<div class="card stored-backup"><h2>Stored materials backup</h2>
    <p class="sub">Each line billing materials stored and not yet installed needs its supplier invoice, receipt or photo.</p>
    ${rows.map(r => `<div class="list-item"><strong>${r.name}</strong> · ${money(r.new_cents)} new this period, ${money(r.stored_cents)} stored
      <div class="sub row-sub">${r.ok ? "Backup attached" : r.records.length ? `On the job records: ${r.records.map(x => x.label).join(", ")}` : "No backup yet"}</div>
      ${draft && !r.ok ? `<div class="form-inline">
        <button class="btn small" data-action="pick-files" data-job="${j.id}" data-kind="stored" data-attach="${r.item_id}" data-sov-line="${r.line_id}" data-capture="1">Take a photo</button>
        <button class="btn small secondary" data-action="pick-files" data-job="${j.id}" data-kind="stored" data-attach="${r.item_id}" data-sov-line="${r.line_id}">Choose file</button></div>` : ""}
    </div>`).join("")}
  </div>`;
}

// The earlier-pay-app note on its own page: billed before Duracash, so nothing here sends it.
export function importedNoteHtml(inv) {
  if (!inv.imported) return "";
  return `<p class="notice earlier-note">Billed before Duracash${inv.imported_as === "summary" ? ": the totals billed to date when the job came in" : ""}. It counts in the job's figures and the next pay app's previous column; it isn't emailed or put in QuickBooks.</p>`;
}

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  "earlier-open": (el, id) => {
    formOpen.earlier = true;
    render();
    focusAfterDraw(() => document.getElementById("ep-period"));
  },
  "earlier-mode": (el) => { earlierMode = el.dataset.mode; render(); },
  "earlier-lines-as": (el) => { earlierLinesAs = el.dataset.mode; render(); },
  "earlier-save": (el, id) => earlierSave(id),
  "earlier-remove": async (el, id) => {
    if (!(await askConfirm("Take off the earlier pay apps and what was paid on them? You can enter them again.", "Take them off", true))) return;
    await post(`/api/jobs/${id}/earlier-payapps-remove`);
  },
  "line-retainage": (el, id) => lineRetainageSave(id),
  "payapp-retainage": async (el, id) => {
    const key = `payret-${el.dataset.inv}`;
    const pct = el.dataset.clear ? "" : (formValues(key).pct || "").replace(/%$/, "");
    if (!el.dataset.clear && !/^\d{1,2}$/.test(pct)) { refuseForm(key, { pct: "Enter a whole percent from 0 to 20." }); return; }
    const r = await post(`/api/jobs/${id}/payapp-retainage`, { pct }, { formKey: key });
    if (r && r.ok) {
      const draft = (state.jobs.find(x => x.id === id)?.invoices || []).find(i => ["draft", "awaiting_approval"].includes(i.status));
      if (draft) nav("payapp-detail", id, draft.id, { replace: true });
    }
  },
  "retainage-release": async (el, id) => {
    const v = formValues("retrelease");
    const amount = v.amount_cents ? centsFrom(v.amount_cents) : { cents: null, error: "" };
    if (refuseForm("retrelease", amount.error || (amount.cents !== null && amount.cents <= 0) ? { amount_cents: "Enter the retainage to bill in dollars, or leave it blank for all." } : {})) return;
    const r = await post(`/api/jobs/${id}/retainage-release`, { amount_cents: amount.cents ?? "", stage: v.stage || "final", period_to: v.period_to || "" },
      { formKey: "retrelease", close: "retrelease" });
    if (r && r.ok) {
      const draft = (state.jobs.find(x => x.id === id)?.invoices || []).find(i => ["draft", "awaiting_approval"].includes(i.status));
      if (draft) nav("payapp-detail", id, draft.id);
    }
  },
  // "Yes" to "Any stored materials received this period to bill?": the Progress pay app page,
  // at the first stored materials box.
  "stored-enter": (el, id) => {
    nav("payapp-new", id);
    focusAfterDraw(() => document.querySelector("[data-sovs]:not([disabled])"));
  },
};
