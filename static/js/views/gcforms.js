// The client's own forms (gcforms.py): on a client's card, their blank pay app, continuation sheet
// and lien waiver forms, each field matched to a Duracash value; on a pay app's page, its preview
// on their form and what stops it going. The forms' data is asked for when a card opens or a pay
// app page draws; it isn't in the page load.
import { esc } from "../format.js";
import { askConfirm } from "../dom.js";
import { readJson, sendFile } from "../net.js";
import { state, onSessionEnd } from "../state.js";
import { post, render, toast, setState, load } from "../app-core.js";

// payer id -> { forState, data, loading }; data as the server sent it (not escaped: esc() on output).
const byClient = {};
// pay app id -> { forState, data, loading }
const byPayapp = {};
// template id -> what is being matched and not saved yet.
const drafts = {};
// template ids whose matching is open.
const editing = new Set();
onSessionEnd(() => {
  for (const o of [byClient, byPayapp, drafts]) for (const k of Object.keys(o)) delete o[k];
  editing.clear();
});

const inches = (pt) => String(Math.round((Number(pt) / 72) * 100) / 100);
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function loadClient(id, force = false) {
  const seen = state;
  const entry = byClient[id];
  if (!force && entry && (entry.forState === seen || entry.loading)) return;
  byClient[id] = { ...(entry || {}), forState: seen, loading: true };
  if (typeof fetch !== "function") return;
  fetch(`/api/payers/${encodeURIComponent(id)}/client-forms`).then(r => (r.ok ? readJson(r) : null)).then(data => {
    byClient[id] = { ...(byClient[id] || {}), forState: state, data: data || (entry && entry.data) || null, loading: false };
    render();
  }).catch(() => { byClient[id] = { ...(byClient[id] || {}), forState: null, data: entry ? entry.data : null, loading: false }; });
}

// A reply that carries the client's forms (every change here answers with them).
function took(payerId, data) {
  if (data && data.client_forms) byClient[payerId] = { ...(byClient[payerId] || {}), forState: state, data: data.client_forms, loading: false };
}

function draftOf(t) {
  if (!drafts[t.id]) {
    const mapping = {};
    for (const m of t.mapping || []) mapping[m.field] = { ...m };
    drafts[t.id] = { mapping, placed: (t.placed || []).map(p => ({ ...p })), money_format: t.money_format, date_format: t.date_format };
  }
  return drafts[t.id];
}

function sourceSelect(data, attrs, picked, forPlaced) {
  const groups = [];
  for (const s of data.sources) {
    if (forPlaced && s.key === "checked") continue;
    let g = groups.find(x => x.name === s.group);
    if (!g) groups.push(g = { name: s.group, items: [] });
    g.items.push(s);
  }
  return `<select ${attrs}><option value="">${forPlaced ? "Pick a value…" : "Leave empty"}</option>${groups.map(g => `<optgroup label="${esc(g.name)}">${g.items.map(s =>
    `<option value="${esc(s.key)}"${s.key === picked ? " selected" : ""}>${esc(s.label)}</option>`).join("")}</optgroup>`).join("")}</select>`;
}

// The extra boxes a value needs: the row for a continuation line, the text for fixed text.
function extras(m, attrs) {
  const src = m.source || "";
  return `${src.startsWith("line.") ? `<label class="inline-label">Line <input type="number" min="1" max="500" class="cf-row" ${attrs} data-cf-key="row" value="${esc(m.row || "")}" aria-label="Which line"></label>` : ""}
    ${src === "text" ? `<input type="text" maxlength="300" ${attrs} data-cf-key="text" value="${esc(m.text || "")}" aria-label="The fixed text" placeholder="The text to write">` : ""}
    ${src ? `<label class="check-line"><input type="checkbox" ${attrs} data-cf-key="optional"${m.optional ? " checked" : ""}> May be blank</label>` : ""}
    ${m.guessed && src ? `<span class="sub">Guessed from its name</span>` : ""}`;
}

function matchHtml(data, t) {
  const d = draftOf(t);
  const pages = (t.pages || []).length;
  const fieldRows = (t.fields || []).map((f, n) => {
    const m = d.mapping[f.name] || {};
    const attrs = `data-cf-tpl="${t.id}" data-cf-field="${esc(f.name)}"`;
    const fixed = ["signature", "button"].includes(f.kind);
    return `<tr>
      <td><span class="cf-name">${esc(f.name)}</span><br><span class="sub">${esc(f.kind)}${f.page ? ` · page ${f.page}` : ""}${f.max_len ? ` · at most ${f.max_len} characters` : ""}</span></td>
      <td>${fixed ? `<span class="sub">Left for the person signing</span>` : `${sourceSelect(data, `${attrs} data-cf-key="source" aria-label="Value for ${esc(f.name)}" id="cf-${t.id}-f${n}"`, m.source || "", false)} ${extras(m, attrs)}`}</td>
    </tr>`;
  }).join("");
  const placedRows = d.placed.map((p, n) => {
    const attrs = `data-cf-tpl="${t.id}" data-cf-placed="${n}"`;
    const num = (key, value, label, step = "0.01") => `<label class="inline-label">${label} <input type="number" step="${step}" min="0" ${attrs} data-cf-key="${key}" value="${esc(value)}"></label>`;
    return `<div class="cf-placed list-item">
      ${sourceSelect(data, `${attrs} data-cf-key="source" aria-label="Value ${n + 1}"`, p.source || "", true)} ${extras(p, attrs)}
      <label class="inline-label">Page <select ${attrs} data-cf-key="page">${Array.from({ length: pages }, (_, i) => `<option value="${i + 1}"${Number(p.page) === i + 1 ? " selected" : ""}>${i + 1}</option>`).join("")}</select></label>
      ${num("x_in", inches(p.x || 0), "Inches from the left")} ${num("y_in", inches(p.y || 0), "Inches from the top")} ${num("size", p.size || 10, "Size", "0.5")}
      <label class="inline-label">Line up <select ${attrs} data-cf-key="align">${[["left", "From the left"], ["right", "Ending at the position"], ["center", "Centred"]].map(([v, l]) =>
        `<option value="${v}"${(p.align || "left") === v ? " selected" : ""}>${l}</option>`).join("")}</select></label>
      <button type="button" class="btn small secondary" data-action="cf-placed-remove" data-tpl="${t.id}" data-n="${n}">Remove</button>
    </div>`;
  }).join("");
  const fmt = (key, list, label) => `<label class="inline-label">${label} <select data-cf-tpl="${t.id}" data-cf-key="${key}">${Object.entries(list).map(([v, l]) =>
    `<option value="${esc(v)}"${d[key] === v ? " selected" : ""}>${esc(l)}</option>`).join("")}</select></label>`;
  return `<div class="cf-match" data-cf-editor="${t.id}">
    ${t.fields && t.fields.length ? `<h4>Their form's fields</h4>
      <p class="sub">Pick the Duracash value for each field to fill. Fields left empty stay blank on the form.</p>
      <div class="table-wrap"><table class="cf-fields"><tr><th>Field</th><th>Duracash value</th></tr>${fieldRows}</table></div>` : ""}
    <h4>Values placed by position</h4>
    <p class="sub">${t.fields && t.fields.length ? "For a value their form has no field for." : "This form has no fields to fill, so each value is placed on the page."}
      Measure in inches from the page's top left corner to where the text sits; open <a href="/api/payers/${esc(data.payer_id)}/client-forms/${t.id}/sample.pdf?grid=1" target="_blank" rel="noopener">the preview with a grid</a> to read positions off it. ${t.pages && t.pages[0] ? `Page 1 is ${inches(t.pages[0].width)} by ${inches(t.pages[0].height)} inches.` : ""}</p>
    ${placedRows}
    <button type="button" class="btn small secondary" data-action="cf-placed-add" data-tpl="${t.id}">Add a value by position</button>
    <div class="form-inline">${fmt("money_format", data.money_formats, "Money as")} ${fmt("date_format", data.date_formats, "Dates as")}</div>
    <div class="form-inline">
      <button type="button" class="btn small" data-action="cf-save" data-id="${esc(data.payer_id)}" data-tpl="${t.id}">Save matches</button>
      <button type="button" class="btn small secondary" data-action="cf-edit" data-tpl="${t.id}">Close</button>
    </div>
  </div>`;
}

function formHtml(data, t, edits) {
  const matched = (t.mapping || []).length, placed = (t.placed || []).length, fields = (t.fields || []).length;
  const what = t.fillable ? `fillable, ${plural(fields, "field")}, ${matched} matched${placed ? `, ${placed} placed` : ""}` : `not fillable, ${plural(placed, "value")} placed`;
  const open = editing.has(t.id);
  return `<div class="cf-form list-item" data-cf-form="${t.id}">
    <div><strong>${esc(t.label)}</strong> <span class="sub">version ${t.version} · ${esc(what)} · ${esc(t.file_name)}, added by ${esc(t.added_by || "")}</span></div>
    ${(t.notes || []).map(n => `<p class="sub">${esc(n)}</p>`).join("")}
    ${t.duracash_note ? `<p class="override-note">Set up by Duracash: ${esc(t.duracash_note)}</p>` : ""}
    ${!matched && !placed ? `<p class="blocker">Nothing is matched yet: pay apps can't go on this form until it is.</p>` : ""}
    <p class="cf-links"><a href="/api/payers/${esc(data.payer_id)}/client-forms/${t.id}/blank.pdf" target="_blank" rel="noopener">Blank form</a>
      · <a href="/api/payers/${esc(data.payer_id)}/client-forms/${t.id}/sample.pdf" target="_blank" rel="noopener">Preview with sample values</a>
      · <a href="/api/payers/${esc(data.payer_id)}/client-forms/${t.id}/sample.pdf?grid=1" target="_blank" rel="noopener">with a grid</a></p>
    ${edits ? `<div class="form-inline">
      <button type="button" class="btn small secondary" data-action="cf-edit" data-tpl="${t.id}" aria-expanded="${open}">${open ? "Close matching" : "Match values"}</button>
      <button type="button" class="btn small secondary" data-action="cf-upload" data-id="${esc(data.payer_id)}" data-type="${esc(t.form_type)}">Upload a new version</button>
      <button type="button" class="btn small secondary" data-action="cf-retire" data-id="${esc(data.payer_id)}" data-tpl="${t.id}">Stop using it</button>
    </div>` : ""}
    ${data.admin ? `<div class="cf-note"><label for="cf-note-${t.id}">Note from Duracash (set up by Duracash)</label>
      <textarea id="cf-note-${t.id}" rows="2" maxlength="2000">${esc(t.duracash_note || "")}</textarea>
      <button type="button" class="btn small secondary" data-action="cf-note" data-id="${esc(data.payer_id)}" data-tpl="${t.id}">Save note</button></div>` : ""}
    ${open && edits ? matchHtml(data, t) : ""}
  </div>`;
}

// The client card's section. p: the client (escaped, from the page load); open: its card is open.
export function clientFormsHtml(p, open, edits) {
  const id = p.id;
  if (open) loadClient(id);
  const entry = byClient[id];
  const data = entry && entry.data;
  const summary = !data ? "" : data.templates.length ? `${plural(data.templates.length, "form")} · emails carry ${data.send_modes[data.send].toLowerCase()}` : "None uploaded";
  const types = data ? Object.entries(data.form_types) : [];
  const body = !data ? `<p class="sub">${entry && entry.loading ? "Loading…" : "Open to load."}</p>` : `
    <p class="sub">A client who sends back pay apps that aren't on their own form: upload their blank form once, match its fields to Duracash's values, and each month's pay app goes out on it.</p>
    ${data.templates.length ? `<div class="form-inline"><label class="inline-label" for="cf-send-${id}">Pay app emails carry</label>
      <select id="cf-send-${id}" data-cf-send="${id}"${edits ? "" : " disabled"}>${Object.entries(data.send_modes).map(([v, l]) => `<option value="${esc(v)}"${v === data.send ? " selected" : ""}>${esc(l)}</option>`).join("")}</select>
      ${edits ? `<button type="button" class="btn small secondary" data-action="cf-send" data-id="${id}">Save</button>` : ""}</div>` : ""}
    ${data.templates.map(t => formHtml(data, t, edits)).join("")}
    ${edits ? `<div class="form-inline cf-add"><label class="inline-label" for="cf-type-${id}">Upload their blank</label>
      <select id="cf-type-${id}">${types.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("")}</select>
      <button type="button" class="btn small" data-action="cf-upload" data-id="${id}">Choose the PDF</button></div>
      <p class="sub">A fillable PDF's fields are read and matched by their names first; check each one. A form with no fields gets values placed by position.</p>` : ""}
    ${data.history.length ? `<details><summary>Older versions (${data.history.length})</summary>${data.history.map(t => `<div class="list-item"><span>${esc(t.label)} version ${t.version} · ${esc(t.file_name)}, replaced or stopped by ${esc(t.retired_by || "")}</span>
      <a href="/api/payers/${esc(data.payer_id)}/client-forms/${t.id}/blank.pdf" target="_blank" rel="noopener">Blank form</a></div>`).join("")}</details>` : ""}`;
  return `<details class="playbook-section" data-more="pb-forms-${id}" data-cf-section="${id}"${entry && entry.open ? " open" : ""}>
    <summary><h3>Their own forms</h3><span class="sub">${esc(summary)}</span></summary>
    <div class="client-forms">${body}</div>
  </details>`;
}

function loadPayapp(j, inv) {
  const seen = state;
  const entry = byPayapp[inv.id];
  if (entry && (entry.forState === seen || entry.loading)) return;
  byPayapp[inv.id] = { ...(entry || {}), forState: seen, loading: true };
  if (typeof fetch !== "function") return;
  fetch(`/api/jobs/${encodeURIComponent(j.id)}/payapp/${encodeURIComponent(inv.id)}/client-forms`).then(r => (r.ok ? readJson(r) : null)).then(data => {
    byPayapp[inv.id] = { forState: state, data, loading: false };
    if (data && (data.forms.length || data.waivers.length)) render();
  }).catch(() => { byPayapp[inv.id] = { forState: null, data: null, loading: false }; });
}

// The pay app page's card: the pay app on the client's form (a preview before sending), what
// stops it, and their lien waivers filled in to print and sign.
export function payappFormsHtml(j, inv) {
  loadPayapp(j, inv);
  const data = (byPayapp[inv.id] || {}).data;
  if (!data || (!data.forms.length && !data.waivers.length)) return "";
  const draft = ["draft", "awaiting_approval"].includes(inv.status);
  return `<div class="card client-forms-payapp">
    <h2>On ${j.payer ? j.payer.name : "the client"}'s own forms</h2>
    ${data.forms.length ? `<p class="sub">The email carries ${esc(data.send_label.toLowerCase())}.</p>` : ""}
    ${data.docusign_note && draft ? `<p class="sub cf-docusign">${esc(data.docusign_note)}</p>` : ""}
    ${data.forms.map(f => `<div class="list-item"><div><strong>${esc(f.label)}</strong> <span class="sub">version ${f.version}</span>
      <a href="${esc(f.url)}" target="_blank" rel="noopener">${draft ? "Preview it filled in" : "Open it filled in"}</a></div>
      ${f.problems.length ? `<ul class="blocker cf-problems">${f.problems.map(p => `<li>${esc(p)}</li>`).join("")}</ul>` : `<p class="sub">Every matched field has its value.</p>`}</div>`).join("")}
    ${data.waivers.length ? `<p class="sub">Lien waivers on their form, filled in for you to print and sign:</p>
      <ul>${data.waivers.map(w => `<li><a href="${esc(w.url)}" target="_blank" rel="noopener">${esc(w.label)}</a> <span class="sub">version ${w.version}</span></li>`).join("")}</ul>` : ""}
  </div>`;
}

// What is typed or picked in the matching boxes, kept through a redraw.
function remember(el) {
  const tpl = el.dataset.cfTpl;
  const d = drafts[tpl];
  if (!d) return false;
  const key = el.dataset.cfKey;
  const value = el.type === "checkbox" ? el.checked : el.value;
  if (key === "money_format" || key === "date_format") { d[key] = value; return false; }
  let target;
  if (el.dataset.cfField !== undefined) target = (d.mapping[el.dataset.cfField] ||= {});
  else if (el.dataset.cfPlaced !== undefined) target = d.placed[Number(el.dataset.cfPlaced)];
  if (!target) return false;
  if (key === "x_in" || key === "y_in") target[key === "x_in" ? "x" : "y"] = Math.round(Number(value) * 72 * 100) / 100;
  else if (key === "source") { target.source = value; delete target.guessed; return true; }
  else target[key] = value;
  return false;
}

if (typeof document !== "undefined" && document.addEventListener) {
  document.addEventListener("change", (e) => {
    const el = e.target;
    if (el && el.dataset && el.dataset.cfTpl && remember(el)) render();
  });
  document.addEventListener("input", (e) => {
    const el = e.target;
    if (el && el.dataset && el.dataset.cfTpl && el.tagName !== "SELECT") remember(el);
  });
  document.addEventListener("toggle", (e) => {
    const box = e.target;
    if (!box || !box.dataset || !box.dataset.cfSection) return;
    const id = box.dataset.cfSection;
    byClient[id] = { ...(byClient[id] || {}), open: box.open };
    if (box.open) loadClient(id);
  }, true);
}

// The body the server takes: the matches and placed values as typed.
export function matchBody(d) {
  return {
    mapping: Object.entries(d.mapping).filter(([, m]) => m.source).map(([field, m]) => ({
      field, source: m.source, ...(m.row !== undefined && m.row !== "" ? { row: m.row } : {}), ...(m.text ? { text: m.text } : {}), ...(m.optional ? { optional: true } : {}) })),
    placed: d.placed.map(p => ({ source: p.source, row: p.row, text: p.text, optional: !!p.optional, page: Number(p.page) || 1, x: Number(p.x) || 0, y: Number(p.y) || 0,
      size: Number(p.size) || 10, align: p.align || "left" })),
    money_format: d.money_format, date_format: d.date_format,
  };
}

export const actions = {
  "cf-upload": (el, id) => {
    const type = el.dataset.type || (document.getElementById(`cf-type-${id}`) || {}).value || "payapp";
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".pdf,application/pdf";
    input.addEventListener("change", async () => {
      const file = input.files && input.files[0];
      if (!file) return;
      toast("Reading the form…");
      const r = await sendFile(`/api/payers/${encodeURIComponent(id)}/client-forms/upload/${encodeURIComponent(type)}`, file, file.name, "application/pdf");
      if (r.status === 401) { load(); return; }
      if (!r.ok) { toast((r.data && r.data.error) || "That form couldn't be read."); return; }
      took(id, r.data);
      const t = (r.data.client_forms.templates || []).find(x => x.form_type === type);
      if (t) editing.add(t.id);
      if (Array.isArray(r.data.jobs)) setState(r.data);
      toast(t && t.fillable ? `Read ${plural((t.fields || []).length, "field")}. Check each match, then save.` : "This form has no fields: place each value by position.");
      render();
    });
    input.click();
  },
  "cf-edit": (el) => {
    const id = el.dataset.tpl;
    if (editing.has(id)) { editing.delete(id); delete drafts[id]; } else editing.add(id);
    render();
  },
  "cf-placed-add": (el) => {
    const d = drafts[el.dataset.tpl];
    if (!d) return;
    d.placed.push({ source: "", page: 1, x: 72, y: 72, size: 10, align: "left" });
    render();
  },
  "cf-placed-remove": (el) => {
    const d = drafts[el.dataset.tpl];
    if (!d) return;
    d.placed.splice(Number(el.dataset.n), 1);
    render();
  },
  "cf-save": async (el, id) => {
    const tpl = el.dataset.tpl, d = drafts[tpl];
    if (!d) return;
    const r = await post(`/api/payers/${encodeURIComponent(id)}/client-forms/${tpl}/match`, matchBody(d));
    if (r && r.ok) { took(id, r.data); editing.delete(tpl); delete drafts[tpl]; for (const k of Object.keys(byPayapp)) delete byPayapp[k]; render(); }
  },
  "cf-send": async (el, id) => {
    const value = (document.getElementById(`cf-send-${id}`) || {}).value;
    const r = await post(`/api/payers/${encodeURIComponent(id)}/client-forms/send`, { send: value });
    if (r && r.ok) { took(id, r.data); for (const k of Object.keys(byPayapp)) delete byPayapp[k]; render(); }
  },
  "cf-retire": async (el, id) => {
    if (!(await askConfirm("Stop using this form? Pay apps go out on Duracash's PDF again. It stays under Older versions.", "Stop using it", true))) return;
    const r = await post(`/api/payers/${encodeURIComponent(id)}/client-forms/${el.dataset.tpl}/retire`, {});
    if (r && r.ok) { took(id, r.data); for (const k of Object.keys(byPayapp)) delete byPayapp[k]; render(); }
  },
  "cf-note": async (el, id) => {
    const note = (document.getElementById(`cf-note-${el.dataset.tpl}`) || {}).value || "";
    const r = await post(`/api/payers/${encodeURIComponent(id)}/client-forms/${el.dataset.tpl}/note`, { note });
    if (r && r.ok) { took(id, r.data); render(); }
  },
};
