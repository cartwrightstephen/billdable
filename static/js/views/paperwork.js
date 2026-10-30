// Money, Paperwork: what goes with each pay app and the closeout paperwork, and the paperwork
// part of a pay app's Before you send (beforeYouSendHtml).
import { unesc, money, words, fmtDate, INVOICE_WORDS } from "../format.js";
import { RECORD_KINDS, isBlocked, coBlockers, WAIVER_KINDS, waiverKindLabel, waiverStageText } from "../jobs.js";
import { US_STATES } from "../validate.js";
import { pathFor } from "../routes.js";
import { askConfirm } from "../dom.js";
import { termText } from "../terms.js";
import { state, currentJobId, currentDetailId, formOpen } from "../state.js";
import { may, perms, render, toast, post, backButton, emptyState, nav } from "../app-core.js";

const DRAFT = ["draft", "awaiting_approval"];
const IMAGE = /\.(jpe?g|png|gif|webp)$/i;

// The job-record kind for a file taken for a paperwork item: the first kind the item takes
// (the server's accepts_kinds), else Other.
function recordKindFor(item) {
  const kinds = (item.accepts_kinds || []).map(unesc);
  if (kinds.length) return kinds[0];
  return RECORD_KINDS[unesc(item.kind || "")] && !["note", "daily"].includes(item.kind) ? item.kind : "other";
}

function filesById() {
  return Object.fromEntries(((state && state.files) || []).map(f => [f.id, f]));
}

// A record's files as links, photos as small pictures (M3).
export function recordFileLinks(rec, files = filesById()) {
  return (rec.file_ids || []).map(fid => {
    const f = files[fid];
    if (!f) return "";
    const pic = IMAGE.test(unesc(f.name || "")) ? `<img class="thumb" src="/api/files/${f.id}" alt="" loading="lazy">` : "";
    return `<a class="file-link" href="/api/files/${f.id}" target="_blank" rel="noopener">${pic}${f.name}</a>`;
  }).filter(Boolean).join(" ");
}

// The type a waiver record is (domain.record_waiver_kind): the one kept on it, else its label.
export function recordWaiverKind(rec) {
  const said = unesc(rec.waiver_kind || "");
  if (WAIVER_KINDS[said]) return said;
  const label = unesc(rec.label || "");
  const m = label.match(/\b(un)?conditional\b/i);
  if (!m) return "";
  return `${m[1] ? "unconditional" : "conditional"}_${/final/i.test(label) ? "final" : "progress"}`;
}

// The waiver records going out with a draft that are unconditional (domain.unconditional_waivers_attached).
export function unconditionalAttached(j, inv) {
  const records = Object.fromEntries((j.records || []).map(r => [r.id, r]));
  const excluded = new Set(inv.excluded_file_ids || []);
  const ids = [...(inv.packet || []).flatMap(p => (p.attachments || []).map(a => a.record_id)), ...(inv.extra_record_ids || [])];
  return [...new Set(ids)].map(id => records[id]).filter(r => r && r.kind === "waiver" && recordWaiverKind(r).startsWith("unconditional")
    && !((r.file_ids || []).length && r.file_ids.every(f => excluded.has(f))));
}

// "What <client> needs to pay" (M5): the client playbook's rules, read only, with a way to edit them.
export function clientNeedsHtml(j, heading = "h2") {
  const p = j.payer || {};
  const row = (label, value) => value ? `<div class="money-row"><span class="label">${label}</span><span>${value}</span></div>` : "";
  return `<div class="client-needs">
    <${heading}>What ${p.name || "the client"} needs to pay</${heading}>
    ${row("Who approves", p.approver)}
    ${row("Paperwork they ask for", p.no_paperwork ? "Nothing" : (p.packet || "Nothing listed"))}
    ${row("They send it back when", p.rejects_when)}
    ${row("Pay apps due by", p.payapp_due_day ? `day ${p.payapp_due_day} of the month` : "")}
    ${row("Payment terms", p.payment_terms_days ? `${p.payment_terms_days} days` : "")}
    ${row("Also email (CC)", p.copy)}
    <p class="sub"><button class="linkish" data-action="nav" data-view="playbook">Change in the Client playbook</button></p>
  </div>`;
}

// The lien waiver forms by state (M22), from the server's helper/waiver_forms.json. Fetched once.
let forms = null, formsAsked = false;
const pickedState = {};
function waiverForms() {
  if (!forms && !formsAsked && typeof fetch === "function") {
    formsAsked = true;
    fetch("/api/waiver-forms").then(r => r.ok ? r.json() : null).then(d => { if (d) { forms = d; render(); } }).catch(() => { formsAsked = false; });
  }
  return forms;
}
export function setWaiverForms(data) { forms = data; formsAsked = true; }

// The state form for a job (M22): the job's state from its address, or one picked here.
export function waiverFormHtml(j, kind) {
  const all = waiverForms();
  const st = pickedState[j.id] || unesc(j.waiver_state || "");
  const entry = all && st ? (all.states || {})[st] : null;
  const picker = `<div class="form-inline"><label for="waiver-state-${j.id}" class="inline-label">Job's state</label>
    <select id="waiver-state-${j.id}"><option value="">Pick…</option>${US_STATES.map(s => `<option value="${s}"${s === st ? " selected" : ""}>${s}</option>`).join("")}</select>
    <button class="btn small secondary" data-action="waiver-state" data-id="${j.id}">Show</button></div>`;
  let body;
  if (!all) body = "";
  else if (!st) body = `<p class="sub">Pick the job's state to see its form.</p>`;
  else if (!entry) body = (all.other && all.other.notes || []).map(n => `<p class="sub">${n}</p>`).join("");
  else body = `<p><strong>${entry.name}</strong>: ${entry.citation}</p>
    ${kind && entry.forms && entry.forms[kind] ? `<p>Form for this waiver: <strong>${entry.forms[kind]}</strong></p>` : ""}
    <ul class="plain-list">${(entry.notes || []).map(n => `<li>${n}</li>`).join("")}</ul>`;
  return `<div class="waiver-form-state"><h3>State form</h3>${picker}${body}</div>`;
}

// "Check the signed form shows" (M15): what Duracash knows that the signed form has to carry.
export function waiverChecklistHtml(j, kind, inv = null, amount = null) {
  const company = state && state.company || {};
  const legal = company.legal_name || company.name || "";
  const cents = amount != null ? amount : inv ? inv.amount_cents - (inv.short_pay_cents || 0) : null;
  const held = inv && kind && kind.endsWith("progress") ? inv.retainage_cents || 0 : 0;
  const items = [
    `Job: ${j.name}${j.address ? ", " + j.address : ""}`,
    `Client: ${(j.payer || {}).name || ""}`,
    `Your legal name: ${legal}${company.legal_name ? "" : ` <button class="linkish" data-action="nav" data-view="settings">Add your legal name</button>`}`,
    cents != null ? `Amount covered: ${money(cents)}` : "",
    inv && inv.period_to ? `Work through: ${fmtDate(inv.period_to)}` : "",
    kind && kind.startsWith("conditional") ? "Conditional wording: it counts only once the payment clears" : "",
    held ? `Not released: the ${money(held)} retainage held on ${inv.number}` : "",
    "Signature and date",
    kind ? `Type: ${waiverKindLabel(kind)}` : "",
  ].filter(Boolean);
  return `<div class="waiver-check"><h3>Check the signed form shows</h3><ul class="plain-list">${items.map(t => `<li>${t}</li>`).join("")}</ul></div>`;
}

// Who put an attachment on an item, and when.
function attachedLine(a) {
  return `${a.auto ? "attached automatically" : "added by " + a.added_by}${a.added_at ? ", " + fmtDate(a.added_at) : ""}`;
}

function attachmentRow(a, records, files) {
  const rec = records[a.record_id];
  if (!rec) return `<div class="sub">${a.auto ? "Attached from the job record" : "Attached"}: ${a.name}</div>`;
  const links = recordFileLinks(rec, files);
  return `<div class="attached-record"><span class="pill">${RECORD_KINDS[unesc(rec.kind)] || words(rec.kind)}</span> ${links || rec.label}${links && !links.includes(`>${rec.label}</a>`) ? ` <span class="sub">${rec.label}</span>` : ""} <span class="sub">· ${attachedLine(a)}</span></div>`;
}

// The job records of the period (M3): after the previous pay app's period, up to this one's
// (to now for a draft); the whole job for closeout.
function periodRecords(j, inv) {
  const records = j.records || [];
  if (!inv) return records;
  const live = (j.invoices || []).filter(i => i.status !== "void" && !i.replaced);
  const at = live.findIndex(i => i.id === inv.id);
  const prev = at > 0 ? live[at - 1] : null;
  const from = prev && prev.period_to ? unesc(prev.period_to) : "";
  const to = DRAFT.includes(inv.status) ? "9999" : unesc(inv.period_to || "9999");
  return records.filter(r => { const d = unesc(r.added_at || "").slice(0, 10); return d > from && d <= to; });
}

const GROUPS = [["Photos", ["photo"]], ["Daily records", ["daily"]], ["Tickets and time sheets", ["ticket"]],
  ["Change orders", ["change_order"]], ["Waivers", ["waiver"]]];

function groupOf(kind) {
  const g = GROUPS.find(([, kinds]) => kinds.includes(kind));
  return g ? g[0] : "Other";
}

// What a work-tied ticket can cover (M6): the job's change orders and unit-price schedule lines.
function workChoices(j) {
  const cos = (j.change_orders || []).filter(c => c.status !== "withdrawn").map(c => [`co:${c.id}`, `CO ${c.number} · ${c.title}`]);
  const lines = ((j.sov_view || {}).lines || []).filter(l => !l.lump_sum).map(l => [`line:${l.id}`, l.description]);
  return [...cos, ...lines];
}

function periodRowHtml(j, rec, packet, inv, editable, files) {
  const kind = unesc(rec.kind);
  const holder = packet.find(p => (p.attachments || []).some(a => a.record_id === rec.id));
  const extra = inv && (inv.extra_record_ids || []).includes(rec.id);
  const fits = packet.filter(p => p.required && !p.present && (p.accepts_kinds || []).map(unesc).includes(kind));
  const options = [...fits.map(p => [p.id, p.label]), ...(inv && (rec.file_ids || []).length && !extra ? [["extra", termText("Send with the pay app")]] : [])];
  const links = recordFileLinks(rec, files);
  let tools = "";
  if (editable && !holder && !extra && options.length) {
    tools += `<span class="form-inline"><select id="period-attach-${rec.id}" aria-label="Attach to"><option value="">Attach to…</option>${options.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select>
      <button class="btn small secondary" data-action="attach-from-period" data-id="${j.id}" data-rec="${rec.id}"${inv ? ` data-inv="${inv.id}"` : ""}>Attach</button></span>`;
  }
  if (editable && kind === "ticket") {
    const choices = workChoices(j);
    const now = rec.co_id ? `co:${rec.co_id}` : rec.sov_line_id ? `line:${rec.sov_line_id}` : "";
    if (choices.length) tools += `<span class="form-inline"><select id="record-work-${rec.id}" aria-label="Work it backs up"><option value="">Covers no set work</option>${choices.map(([v, l]) => `<option value="${v}"${v === now ? " selected" : ""}>${l}</option>`).join("")}</select>
      <button class="btn small secondary" data-action="record-work" data-id="${j.id}" data-rec="${rec.id}">Save</button></span>`;
  }
  if (editable && kind === "waiver") {
    const has = recordWaiverKind(rec);
    tools += `<span class="form-inline"><select id="waiver-type-${rec.id}" aria-label="Waiver type"><option value="">Type not said</option>${Object.entries(WAIVER_KINDS).map(([k, v]) => `<option value="${k}"${k === has ? " selected" : ""}>${v}</option>`).join("")}</select>
      <button class="btn small secondary" data-action="waiver-type" data-id="${j.id}" data-rec="${rec.id}">Save</button></span>`;
  }
  return `<div class="list-item period-record">
    <div><span class="pill">${RECORD_KINDS[kind] || words(kind)}</span> ${rec.label} <span class="sub">${fmtDate(rec.added_at)}</span>
      ${links ? `<div class="sub row-sub">${links}</div>` : ""}
      ${holder ? `<div class="sub row-sub">On ${holder.label}</div>` : extra ? `<div class="sub row-sub">${termText("Goes with the pay app")}</div>` : ""}</div>
    ${tools}
  </div>`;
}

// "From the job record this period" (M3), grouped by kind.
function periodHtml(j, inv, packet, editable, isCloseout) {
  const recs = periodRecords(j, inv);
  const files = filesById();
  const groups = {};
  for (const r of [...recs].reverse()) (groups[groupOf(unesc(r.kind))] ||= []).push(r);
  const order = [...GROUPS.map(g => g[0]), "Other"].filter(g => groups[g]);
  return `<div class="card period-records">
    <h2>${isCloseout ? "From the job record" : "From the job record this period"}</h2>
    ${order.length ? order.map(g => `<h3>${g}</h3>${groups[g].map(r => periodRowHtml(j, r, packet, inv, editable, files)).join("")}`).join("")
      : `<p class="sub">Nothing added this period.</p>`}
  </div>`;
}

// The paperwork part of a pay app's Before you send (M4, M5, M14): the client's rules, every file
// the email carries with Leave out and Put back, records to add, and the waiver check.
export function beforeYouSendHtml(j, inv) {
  const records = Object.fromEntries((j.records || []).map(r => [r.id, r]));
  const files = filesById();
  const excluded = new Set(inv.excluded_file_ids || []);
  const extra = inv.extra_record_ids || [];
  const going = [];
  for (const item of inv.packet || j.packet || []) for (const a of item.attachments || []) {
    const rec = records[a.record_id];
    const fids = rec ? rec.file_ids || [] : [];
    for (const fid of fids) if (!going.some(g => g.fid === fid)) going.push({ fid, rec, item: item.label });
  }
  for (const rid of extra) {
    const rec = records[rid];
    const fids = rec ? rec.file_ids || [] : [];
    for (const fid of fids) if (!going.some(g => g.fid === fid)) going.push({ fid, rec, extra: true });
  }
  const editable = DRAFT.includes(inv.status) && !j.closed;
  const addable = (j.records || []).filter(r => (r.file_ids || []).length && !going.some(g => g.rec && g.rec.id === r.id));
  const fileRow = (g) => {
    const f = files[g.fid];
    const name = f ? `<a href="/api/files/${g.fid}" target="_blank" rel="noopener">${f.name}</a>` : g.rec.label;
    const out = excluded.has(g.fid);
    const why = g.extra ? termText("added to this pay app") : `for ${g.item}`;
    const btn = !editable ? "" : g.extra && !out
      ? `<button class="btn small secondary" data-action="payapp-attach" data-id="${j.id}" data-inv="${inv.id}" data-op="remove_record" data-target="${g.rec.id}">Take off</button>`
      : `<button class="btn small secondary" data-action="payapp-attach" data-id="${j.id}" data-inv="${inv.id}" data-op="${out ? "include_file" : "exclude_file"}" data-target="${g.fid}">${out ? "Put back" : "Leave out"}</button>`;
    return `<li class="${out ? "left-out" : ""}"><span>${name} <span class="sub">${why}${out ? " · left out" : ""}</span></span> ${btn}</li>`;
  };
  const stages = inv.waiver_stages || {};
  const unconditional = unconditionalAttached(j, inv);
  return `<div class="send-attachments">
      <h3>The email carries</h3>
      <ul class="plain-list attach-list">
        <li>${termText("Pay app PDF")}${inv.sov_sheet && inv.sov_sheet.length ? " with continuation sheet" : ""} and the paperwork checklist</li>
        ${going.map(fileRow).join("")}
      </ul>
      ${editable && addable.length ? `<div class="form-inline"><select id="payapp-add-${inv.id}" aria-label="Add from the job record"><option value="">Add from the job record…</option>${addable.map(r => `<option value="${r.id}">${r.label}</option>`).join("")}</select>
        <button class="btn small secondary" data-action="payapp-add" data-id="${j.id}" data-inv="${inv.id}">Add</button></div>` : ""}
      ${stages.conditional && stages.conditional.status ? `<div class="money-row"><span class="label">Conditional waiver</span><span>${waiverStageText(stages.conditional)}</span></div>` : ""}
    </div>
    ${unconditional.length ? `<div class="unconditional-warning" role="alert">
      <p class="blocker">An unconditional waiver is attached: ${unconditional.map(r => r.label).join(", ")}. It gives up your lien rights before you are paid. Take it off, or send anyway with the reason.</p>
      ${may("send") && perms().owner ? `<div class="form-inline"><input id="override-reason-${inv.id}" class="grow" aria-label="Why it goes with this pay app" placeholder="Why it goes with this pay app">
        <button class="btn small danger" data-action="send-override" data-id="${j.id}" data-inv="${inv.id}">Send anyway</button></div>` : `<p class="sub">Only an owner can send it this way.</p>`}
    </div>` : ""}
    <div class="client-needs-box">${clientNeedsHtml(j, "h3")}</div>`;
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  packetDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j) return backButton("payapps", termText("Pay apps")) + emptyState("Paperwork not found", "The job may have been deleted.", `<button class="btn" data-action="nav" data-view="payapps">${termText("Go to Pay apps")}</button>`);
    const isCloseout = currentDetailId === "closeout";
    const inv = isCloseout ? null : (j.invoices.find(i => i.id === currentDetailId) || [...j.invoices].reverse().find(i => i.packet) || j.invoices[j.invoices.length - 1]);
    const packet = isCloseout ? (j.closeout_packet || []) : ((inv && inv.packet) || j.packet || []);
    if (!inv && !packet.length) return backButton("job", j.name, j.id) + emptyState("No paperwork yet", "Draft a pay app. Its paperwork list comes with it, from this client's playbook. Until then, add notes and files to the job record.", `<button class="btn" data-action="open-job" data-id="${j.id}">Back to ${j.name}</button>`);
    const missing = packet.filter(p => p.required && !p.present).length;
    // Unsigned change orders on the bill block the same send; say so here with a way to them (R3-05).
    const cosBlocking = !isCloseout && isBlocked(j) ? coBlockers(j) : [];
    // What went with a pay app stays as it was sent: Remove is for a draft's paperwork, the job's and closeout's.
    const canRemove = !j.closed && !(inv && !DRAFT.includes(inv.status));
    const records = Object.fromEntries((j.records || []).map(r => [r.id, r]));
    const files = filesById();
    return `
      ${backButton("job", j.name, j.id)}
      <h1>${isCloseout ? "Closeout paperwork" : (inv ? "Paperwork for " + inv.number : "Paperwork")}</h1>
      <p class="sub">${j.name}${inv ? " · " + money(inv.amount_cents) + " · " + (INVOICE_WORDS[inv.status] || words(inv.status)) : ""}${isCloseout && j.money.retainage_open_cents > 0 ? " · needed before you ask for the money held back" : ""} · ${missing ? `<span class="blocker">${missing} missing</span>` : "complete"}</p>
      <div class="card">${clientNeedsHtml(j)}</div>
      <div class="card">
        <h2>Required items</h2>
        ${packet.length ? packet.map(p => {
          const skipped = !p.required && p.skipped_reason;
          const waiver = unesc(p.waiver_kind || "");
          return `
          <div class="list-item packet-item ${p.present || skipped ? "present" : "absent"}">
            <div><span class="mark" aria-hidden="true">${p.present ? "✓" : skipped ? "–" : "✗"}</span> <strong>${p.label}</strong> <span class="visually-hidden">${p.present ? "in" : skipped ? "not needed" : "missing"}</span>
              ${!p.present && !skipped ? `<span class="field-error">${p.blocker}</span>` : ""}
              ${(p.covers || []).length ? `<div class="sub">For ${p.covers.join(", ")}</div>` : ""}
              ${skipped ? `<div class="sub">Not needed ${isCloseout ? "on this job" : "for this client"}${/^Not needed for this \w+$/.test(unesc(p.skipped_reason)) ? "" : ": " + p.skipped_reason}</div>` : ""}
              ${p.present && p.present_reason === "Produced by Duracash" ? `<div class="sub">Produced by Duracash with the pay app</div>` : ""}
              ${p.present && p.present_reason && p.present_reason !== "Produced by Duracash" ? `<div class="sub">Marked as in hand: ${p.present_reason}</div>` : ""}
              ${p.present && !p.present_reason && !(p.attachments || []).length ? `<div class="sub">Marked as in hand.</div>` : ""}
              ${p.files_left_out && !p.present ? `<div class="sub">Its files are left out of the email.</div>` : ""}
              ${(p.attachments || []).map(a => attachmentRow(a, records, files)).join("")}
              ${p.present && p.present_reason !== "Produced by Duracash" && canRemove ? `<button class="btn small secondary" data-action="packet-detach" data-id="${j.id}" data-item="${p.id}">Remove</button>` : ""}
            </div>
            ${!p.present && !skipped ? `<div class="form-inline packet-add">
              <button class="btn small" data-action="pick-files" data-job="${j.id}" data-attach="${p.id}" data-kind="${recordKindFor(p)}"${waiver ? ` data-waiver-kind="${waiver}"` : ""} data-capture="1">Take a photo</button>
              <button class="btn small secondary" data-action="pick-files" data-job="${j.id}" data-attach="${p.id}" data-kind="${recordKindFor(p)}"${waiver ? ` data-waiver-kind="${waiver}"` : ""}>Choose file</button>
            </div>
            <div class="form-inline">
              <button class="btn small secondary" data-action="toggle-form" data-form="resolve-${p.id}" aria-expanded="${!!formOpen["resolve-" + p.id]}">I have this…</button>
              <button class="btn small secondary" data-action="packet-skip" data-id="${j.id}" data-item="${p.id}">${isCloseout ? "Not needed on this job" : "Not needed for this client"}</button>
            </div>
            ${formOpen["resolve-" + p.id] ? `<div class="form-inline"><input id="resolve-reason-${p.id}" placeholder="Where is it? (optional)" aria-label="Where is it? (optional)" class="grow"><button class="btn small" data-action="resolve" data-id="${j.id}" data-item="${p.id}">Save</button></div>` : ""}` : ""}
            ${waiver && !skipped ? `<details class="waiver-details"><summary>What the signed waiver must show</summary>${waiverChecklistHtml(j, waiver, inv)}${waiverFormHtml(j, waiver)}</details>` : ""}
          </div>`;
        }).join("") : `<p class="sub">${termText("This pay app needs no paperwork.")}</p>`}
      </div>
      ${periodHtml(j, inv, packet, canRemove, isCloseout)}
      ${!isCloseout && cosBlocking.length ? `<div class="card also-blocking" role="note">
        <h2>Also blocking send</h2>
        ${cosBlocking.map(b => `<div class="list-item">${b.text} <a href="${pathFor("co-detail", j.id, b.co_id)}" data-action="open-co" data-job="${j.id}" data-co="${b.co_id}">Open CO ${b.number}</a></div>`).join("")}
      </div>` : ""}
    `;
  },
  "packet-detail": function() { return views.packetDetail(); },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  "resolve": async (el, id) => {
    const box = document.getElementById(`resolve-reason-${el.dataset.item}`);
    const reason = (box?.value || "").trim();   // optional (R3-05)
    const r = await post(`/api/jobs/${id}/resolve/${el.dataset.item}`, { reason });
    if (r && r.ok) { formOpen[`resolve-${el.dataset.item}`] = false; render(); }
  },
  "goto-packet": (el, id) => nav("packet-detail", id),
  "open-packet": (el) => nav("packet-detail", el.dataset.job, el.dataset.inv),
  "attach": async (el, id) => {
    const name = document.querySelector("#attach-name")?.value || "attachment.pdf";
    await post(`/api/jobs/${id}/attach/${el.dataset.item}`, { name });
  },
  "packet-detach": (el, id) => post(`/api/jobs/${id}/detach/${el.dataset.item}`),
  "packet-skip": (el, id) => post(`/api/jobs/${id}/packet-skip/${el.dataset.item}`, {}),
  // A record of the period put on a missing item, or sent with the pay app as it is (M3, M4).
  "attach-from-period": async (el, id) => {
    const pick = document.getElementById(`period-attach-${el.dataset.rec}`);
    const to = pick ? pick.value : "";
    if (!to) { toast("Choose where it goes first."); pick?.focus(); return; }
    if (to === "extra") await post(`/api/jobs/${id}/payapp/${el.dataset.inv}/attachments`, { action: "add_record", record_id: el.dataset.rec });
    else await post(`/api/jobs/${id}/attach-record/${to}`, { record_id: el.dataset.rec });
  },
  "payapp-attach": (el, id) => {
    const op = el.dataset.op;
    const key = op.endsWith("_record") ? "record_id" : "file_id";
    return post(`/api/jobs/${id}/payapp/${el.dataset.inv}/attachments`, { action: op, [key]: el.dataset.target });
  },
  "payapp-add": async (el, id) => {
    const pick = document.getElementById(`payapp-add-${el.dataset.inv}`);
    if (!pick || !pick.value) { toast("Choose a record first."); pick?.focus(); return; }
    await post(`/api/jobs/${id}/payapp/${el.dataset.inv}/attachments`, { action: "add_record", record_id: pick.value });
  },
  "record-work": async (el, id) => {
    const v = document.getElementById(`record-work-${el.dataset.rec}`)?.value || "";
    const [what, ref] = v.split(":");
    await post(`/api/jobs/${id}/record-work/${el.dataset.rec}`, { co_id: what === "co" ? ref : "", sov_line_id: what === "line" ? ref : "" });
  },
  "waiver-type": async (el, id) => {
    const kind = document.getElementById(`waiver-type-${el.dataset.rec}`)?.value || "";
    if (!kind) { toast("Pick the waiver type first."); return; }
    await post(`/api/jobs/${id}/waiver-type/${el.dataset.rec}`, { kind });
  },
  "waiver-state": (el, id) => {
    pickedState[id] = document.getElementById(`waiver-state-${id}`)?.value || "";
    render();
  },
  // Approve & send with an unconditional waiver attached (M14): an owner, with the reason.
  "send-override": async (el, id) => {
    const box = document.getElementById(`override-reason-${el.dataset.inv}`);
    const reason = (box?.value || "").trim();
    if (!reason) { toast("Enter why it goes with this pay app."); box?.focus(); return; }
    if (!(await askConfirm("Send it with the unconditional waiver? It gives up your lien rights before you are paid.", "Send anyway", true))) return;
    await post(`/api/jobs/${id}/send`, { unconditional_override: reason });
  },
};
