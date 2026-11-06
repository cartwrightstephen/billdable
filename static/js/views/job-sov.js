// The job page's schedule of values: the card, the editor, pasted rows, and line-by-line progress.
import { esc, unesc, money, fmtDate } from "../format.js";
import { mayTake } from "../jobs.js";
import { sovMilli, lumpDone, sovCents, sovBp, sovExtend, sovMarkup, fmtQty, blankSovLine, sovLineProblem, sovFixedNote, sovPeriodHtml, sovLostLines } from "../sov.js";
import { readJson, setNetBanner } from "../net.js";
import { askConfirm } from "../dom.js";
import { field, formValues, formErrorLine, refuseForm, clearForm } from "../forms.js";
import { termText } from "../terms.js";
import { state, formOpen, onSessionEnd } from "../state.js";
import { perms, post, toast, render, nav } from "../app-core.js";
import { qtyImportForJob } from "./data.js";


export let sovDraft = null; // schedule-of-values editor: { jobId, lines: [{id,item,description,quantity,unit,rate}], op_pct, warnings } (raw text, not escaped)
// Signed out: a schedule being typed is that company's.
let sovCorrecting = null; // the line whose field total is being corrected: { jobId, lineId }
onSessionEnd(() => { sovDraft = null; sovCorrecting = null; });

function sovStartEdit(j) {
  const v = j.sov_view || {};
  // The one automatic lump-sum line isn't something to edit: real lines replace it.
  const base = v.auto ? [] : (v.lines || []).filter(l => !l.co_id);
  sovDraft = {
    jobId: j.id,
    lines: base.length
      ? base.map(l => ({ id: l.id, item: unesc(l.item), description: unesc(l.description), quantity: String(l.qty_milli / 1000), unit: unesc(l.unit), rate: (l.rate_cents / 100).toFixed(2) }))
      : [blankSovLine(), blankSovLine(), blankSovLine()],
    op_pct: v.op_bp ? String(v.op_bp / 100) : "",
    warnings: [],
  };
}

function sovFilledLines() {
  return sovDraft.lines.filter(l => [l.description, l.quantity, l.rate].some(x => String(x || "").trim()));
}

function sovDraftTotals() {
  let base = 0, bad = 0, noPrice = 0;
  const amounts = sovDraft.lines.map(l => {
    const filled = [l.description, l.quantity, l.rate].some(x => String(x || "").trim());
    if (!filled) return null;
    const problem = sovLineProblem(l);
    if (problem === "price") { bad++; noPrice++; return "price"; }
    if (problem) { bad++; return NaN; }
    const q = String(l.quantity || "").trim() ? sovMilli(l.quantity) : 1000;
    const r = sovCents(l.rate);
    const a = sovExtend(q, r);
    base += a;
    return a;
  });
  const bpRaw = String(sovDraft.op_pct || "").trim();
  const bp = bpRaw ? sovBp(bpRaw) : 0;
  const op = isNaN(bp) ? 0 : sovMarkup(base, bp);
  return { amounts, base, bp, op, total: base + op, bad, noPrice, badBp: isNaN(bp) || bp < 0 || bp > 5000 };
}

export function updateSovTotals() {
  if (!sovDraft) return;
  const j = state.jobs.find(x => x.id === sovDraft.jobId);
  if (!j) return;
  const t = sovDraftTotals();
  t.amounts.forEach((a, i) => {
    const el = document.getElementById(`sov-amt-${i}`);
    if (el) el.textContent = a === null ? "" : a === "price" ? "needs a price" : isNaN(a) ? "check" : money(a);
  });
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set("sov-sum-lines", money(t.base));
  set("sov-sum-op", t.badBp ? "check the percent" : money(t.op));
  set("sov-sum-total", money(t.total));
  const diff = t.total - j.contract_cents;
  set("sov-sum-diff", diff === 0 ? "matches the contract" : `${diff > 0 ? "+" : "−"}${money(Math.abs(diff))} vs the contract`);
  const d = document.getElementById("sov-sum-diff");
  if (d) d.className = diff === 0 ? "ok" : "blocker";
  const alt = document.querySelector('[data-action="sov-save-contract"]');
  if (alt) {
    alt.hidden = diff === 0 || t.base === 0;
    alt.textContent = `Save and change the contract to ${money(t.total)}`;
  }
}

function sovEditorHtml(j) {
  const rows = sovDraft.lines.map((l, i) => `
    <tr>
      <td class="sov-item-cell"><input class="sov-in sov-item" data-sov-row="${i}" data-sov-field="item" value="${esc(l.item)}" placeholder="${i + 1}" aria-label="Line ${i + 1}: item"></td>
      <td class="sov-desc-cell"><input class="sov-in sov-desc" data-sov-row="${i}" data-sov-field="description" value="${esc(l.description)}" placeholder="Rough-in wiring" aria-label="Line ${i + 1}: description"></td>
      <td><input class="sov-in sov-num" data-sov-row="${i}" data-sov-field="quantity" value="${esc(l.quantity)}" placeholder="1" inputmode="decimal" aria-label="Line ${i + 1}: quantity"></td>
      <td><input class="sov-in sov-unit" data-sov-row="${i}" data-sov-field="unit" value="${esc(l.unit)}" placeholder="LS" aria-label="Line ${i + 1}: unit"></td>
      <td><input class="sov-in sov-num${l.needsPrice ? " sov-need-price" : ""}" data-sov-row="${i}" data-sov-field="rate" value="${esc(l.rate)}" placeholder="${l.needsPrice ? "Price?" : "0.00"}" inputmode="decimal" aria-label="Line ${i + 1}: unit rate, dollars"></td>
      <td class="num sov-amt" id="sov-amt-${i}"></td>
      <td class="sov-remove-cell"><button class="btn small secondary" data-action="sov-remove" data-row="${i}" aria-label="Remove line ${i + 1}">×</button></td>
    </tr>`).join("");
  return `
    <p class="sub">Each line is a quantity at a unit rate; a lump sum is quantity 1 (unit LS) at its amount. Overhead and profit goes on top of the base lines. Approved change orders add their own lines.</p>
    ${(j.sov_view || {}).auto ? `<p class="sub">Leave this blank to keep one lump-sum line for the contract amount.</p>` : ""}
    ${(j.sov_view || {}).open_draft ? `<p class="sub">Saving updates ${j.sov_view.open_draft.number}. Quantities done on lines you replace start again at zero.</p>` : ""}
    <div class="sov-paste">
      <textarea id="sov-paste" rows="3" placeholder="Paste rows from Excel, Google Sheets, a CSV or a PDF: description, quantity, unit, unit rate, or a lump-sum amount. A header row is optional; total rows are skipped."></textarea>
      <button class="btn small" data-action="sov-read">Read pasted rows</button>
    </div>
    ${sovDraft.warnings.length ? `<div class="sov-warn">${sovDraft.warnings.map(w => `<div>${esc(w)}</div>`).join("")}</div>` : ""}
    <div class="table-wrap sov-wrap"><table class="sov-table sov-edit">
      <tr><th>Item</th><th>Description</th><th>Qty</th><th>Unit</th><th>Unit rate $</th><th>Scheduled value</th><th></th></tr>
      ${rows}
    </table></div>
    <div class="form-inline">
      <button class="btn small secondary" data-action="sov-add">Add line</button>
      <label for="sov-op" class="sov-op-label">Overhead and profit</label>
      <input id="sov-op" class="sov-in sov-num" data-sov-field="op_pct" value="${esc(sovDraft.op_pct)}" placeholder="0" inputmode="decimal"> %
    </div>
    <div class="sov-sums">
      <div class="money-row"><span class="label">Schedule lines</span><span id="sov-sum-lines"></span></div>
      <div class="money-row"><span class="label">Overhead and profit</span><span id="sov-sum-op"></span></div>
      <div class="money-row"><span class="label">Schedule total</span><span><strong id="sov-sum-total"></strong> · <span id="sov-sum-diff"></span></span></div>
      <div class="money-row"><span class="label">Contract</span><span>${money(j.contract_cents)}</span></div>
    </div>
    <div class="form-inline">
      <button class="btn" data-action="sov-save" data-id="${j.id}">Save schedule</button>
      <button class="btn secondary" data-action="sov-save-contract" data-id="${j.id}" hidden>Save and change the contract</button>
      <button class="btn secondary" data-action="sov-cancel">Cancel</button>
    </div>`;
}

// How a line's quantity reads: a lump sum in %, anything else in its unit.
function lineQty(l, milli) { return l.lump_sum ? `${+(milli / 10).toFixed(1)}%` : `${fmtQty(milli)} ${l.unit}`; }

// Correct field total (a wrong quantity on a job record or an imported file): the total now, the new
// one and why. The change, with the old and new totals, goes in the activity log.
function sovCorrectHtml(j) {
  if (!sovCorrecting || sovCorrecting.jobId !== j.id) return "";
  const l = (j.sov_view.lines || []).find(x => x.id === sovCorrecting.lineId);
  if (!l) return "";
  return `<div class="sov-correct" data-form-key="sovcorrect">
    <h3>Correct field total: ${l.description}</h3>
    ${formErrorLine("sovcorrect")}
    <p class="sub">The field total is ${lineQty(l, l.done_milli)}.${l.sent_qty_milli ? ` ${lineQty(l, l.sent_qty_milli)} is on pay apps sent, and they stay as they are.` : ""}</p>
    <div class="form-grid">
      ${field({ form: "sovcorrect", name: "qty", id: "sov-correct-qty", label: l.lump_sum ? "New total, % done" : `New total (${l.unit})`, inputmode: "decimal" })}
      ${field({ form: "sovcorrect", name: "reason", id: "sov-correct-reason", label: "Why it was wrong" })}
    </div>
    <div class="form-inline"><button class="btn small" data-action="sov-correct-save" data-id="${j.id}">Correct field total</button>
      <button class="btn small secondary" data-action="sov-correct-cancel">Cancel</button></div>
  </div>`;
}

async function sovCorrectSave(jobId) {
  const line = (state.jobs.find(j => j.id === jobId)?.sov_view?.lines || []).find(l => l.id === sovCorrecting?.lineId);
  if (!line) return;
  const v = formValues("sovcorrect");
  const qty = line.lump_sum ? lumpDone(v.qty) : (v.qty || "").trim();
  const milli = qty === null ? NaN : sovMilli(qty);
  if (refuseForm("sovcorrect", { ...(isNaN(milli) || milli < 0 ? { qty: "Enter the correct total, zero or more." } : {}), ...((v.reason || "").trim() ? {} : { reason: "Enter why the total is wrong." }) })) return;
  const below = milli < (line.sent_qty_milli || 0);
  const said = `${unesc(line.description)}: ${unesc(lineQty(line, line.done_milli))} to ${unesc(lineQty(line, milli))}.`
    + (below ? ` ${unesc(lineQty(line, line.sent_qty_milli))} is already billed. Pay apps sent stay as they are, and nothing more is suggested on this line until the field total passes that.` : "");
  if (!(await askConfirm(said, "Correct field total", below))) return;
  const r = await post(`/api/jobs/${jobId}/sov-correct`, { line_id: line.id, qty, reason: v.reason, below_billed: below }, { formKey: "sovcorrect" });
  if (r && r.ok) { sovCorrecting = null; render(); }
}

export function sovCardHtml(j) {
  const v = j.sov_view || {};
  if (sovDraft && sovDraft.jobId === j.id) {
    return `<div class="card" id="sov-card"><h2>Schedule of values</h2>${sovEditorHtml(j)}</div>`;
  }
  // The card is on every job (Nov 3 Decision 2): what it can't do, it says.
  const fixedNote = sovFixedNote(j, mayTake("void-draft", perms()));
  if (!v.present) {
    if (!v.editable) return `<div class="card" id="sov-card"><h2>Schedule of values</h2>${fixedNote}</div>`;
    return `<div class="card" id="sov-card">
      <h2>Schedule of values</h2>
      <p class="sub">Bill line by line, the way subs bill: quantity × unit rate plus overhead and profit. ${termText("Each pay app then shows what was done on each line this period. Without it, progress is entered in dollars. It can be set until the first pay app is sent.")}${v.open_draft ? ` Setting it updates ${v.open_draft.number}.` : ""}</p>
      <button class="btn secondary" data-action="sov-start" data-id="${j.id}">Set up schedule of values</button>
    </div>`;
  }
  const t = v.totals;
  const rows = v.lines.map(l => `
    <tr>
      <td>${l.item}</td>
      <td class="sov-desc-cell">${l.description}${l.co_id ? ' <span class="pill green">CO</span>' : ""}</td>
      <td class="num">${l.lump_sum ? "" : fmtQty(l.qty_milli) + " " + l.unit}</td>
      <td class="num">${l.lump_sum ? "LS" : money(l.rate_cents)}</td>
      <td class="num">${money(l.scheduled_cents)}</td>
      <td class="num">${l.lump_sum ? l.pct + "%" : fmtQty(l.done_milli)}${l.stored_cents ? `<div class="sub">+ ${money(l.stored_cents)} stored</div>` : ""}${l.from_daily_on ? `<div class="sov-daily">Updated from job records on ${fmtDate(l.from_daily_on)}</div>` : ""}${!v.done && l.scheduled_cents >= 0 ? `<div><button class="linkish tall" data-action="sov-correct-open" data-id="${j.id}" data-line="${l.id}" aria-label="Correct field total: ${l.description}">Correct field total</button></div>` : ""}</td>
      <td class="num">${money(l.to_date_cents)}</td>
      <td class="num">${money(l.billed_cents)}</td>
      <td class="num">${money(l.scheduled_cents - l.to_date_cents)}</td>
    </tr>`).join("");
  return `<div class="card" id="sov-card">
    <h2>Schedule of values</h2>
    <div class="table-wrap sov-wrap"><table class="sov-table">
      <tr><th>Item</th><th>Description</th><th>Qty</th><th>Unit rate</th><th>Scheduled value</th><th>Done to date</th><th>Earned to date</th><th>Billed</th><th>Balance to finish</th></tr>
      ${rows}
      ${v.op_bp ? `<tr><td></td><td class="sov-desc-cell">Overhead and profit ${v.op_bp / 100}%</td><td></td><td></td><td class="num">${money(t.op_scheduled_cents)}</td><td></td><td class="num">${money(t.op_to_date_cents)}</td><td class="num">${money(v.op_billed_cents)}</td><td class="num">${money(t.op_scheduled_cents - t.op_to_date_cents)}</td></tr>` : ""}
      <tr class="sov-total"><td></td><td class="sov-desc-cell">Total</td><td></td><td></td><td class="num">${money(t.scheduled_cents)}</td><td></td><td class="num">${money(t.to_date_cents)}</td><td class="num">${money(v.lines.reduce((a, l) => a + l.billed_cents, 0) + v.op_billed_cents)}</td><td class="num">${money(t.scheduled_cents - t.to_date_cents)}</td></tr>
    </table></div>
    ${v.auto && v.editable ? `<p class="sub">One lump-sum line for the contract amount. Replace it with your own lines any time before the first pay app is sent.</p>` : ""}
    ${v.editable ? `<button class="btn small secondary" data-action="sov-start" data-id="${j.id}">${v.auto ? "Enter schedule lines" : "Edit schedule"}</button>` : fixedNote}
    ${qtyImportForJob(j)}
    ${sovCorrectHtml(j)}
    ${sovPeriodHtml(j, true)}
  </div>`;
}

async function sovReadPaste() {
  const box = document.getElementById("sov-paste");
  if (!box || !sovDraft) return;
  const text = box.value;
  if (!text.trim()) { toast("Paste rows into the box first."); return; }
  let res;
  try {
    res = await fetch("/api/sov/parse", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
  } catch (_) { setNetBanner(true); toast("Can't reach Duracash."); return; }
  const data = await readJson(res);
  if (!res.ok) { toast(data.error || "Couldn't read the rows."); return; }
  const kept = sovFilledLines();
  sovDraft.lines = kept.concat((data.lines || []).map(l => ({ id: "", item: l.item, description: l.description, quantity: l.quantity, unit: l.unit, rate: l.rate, needsPrice: !!l.needs_price })));
  if (!sovDraft.lines.length) sovDraft.lines = [blankSovLine()];
  if (data.op_pct && !String(sovDraft.op_pct || "").trim()) sovDraft.op_pct = data.op_pct;
  sovDraft.warnings = [`Read ${(data.lines || []).length} line${(data.lines || []).length === 1 ? "" : "s"}. Check them below, then save.`].concat(data.warnings || []);
  render();
}

async function sovSave(jobId, updateContract) {
  const t = sovDraftTotals();
  if (t.noPrice && t.noPrice === t.bad) { toast(t.noPrice === 1 ? "One line needs a price. Enter its unit rate, then save." : `${t.noPrice} lines need a price. Enter their unit rates, then save.`); return; }
  if (t.bad) { toast("Some lines need a description, a quantity above zero and a rate above zero."); return; }
  const j = state.jobs.find(x => x.id === jobId);
  if (updateContract && !(await askConfirm(`Change the contract amount from ${money(j.contract_cents)} to ${money(t.total)} to match the schedule?`, "Change the contract"))) return;
  const lines = sovFilledLines().map(l => ({ id: l.id || undefined, item: l.item, description: l.description, quantity: l.quantity, unit: l.unit, rate: l.rate }));
  const lost = sovLostLines((j.sov_view || {}).lines, lines.map(l => l.id).filter(Boolean));
  if (lost.length && !(await askConfirm(`These lines are being replaced, and what is recorded on them is discarded: ${lost.map(l => `${unesc(l.description)} ${unesc(lineQty(l, l.done_milli))} (${money(l.to_date_cents)})`).join("; ")}.`, "Replace the lines", true))) return;
  const draftBefore = (j.sov_view || {}).open_draft;
  const r = await post(`/api/jobs/${jobId}/sov`, { lines, op_pct: sovDraft.op_pct, update_contract: !!updateContract });
  if (r && r.ok) {
    sovDraft = null;
    render();
    const saved = state.jobs.find(x => x.id === jobId);
    if (draftBefore && saved && !(saved.sov_view || {}).open_draft) toast(`${draftBefore.number} comes back when you enter quantities on the new lines.`);
  }
}

// The amounts changed on the draft: only the boxes that differ from what the draft bills, a cleared
// box going back to the suggested amount. On the pay app's page, the redrafted one opens.
async function sovSaveAmounts(jobId, amounts) {
  const onDetail = !!document.querySelector(".send-check");
  const r = await post(`/api/jobs/${jobId}/sov-amounts`, { amounts }, { formKey: "sovamounts" });
  if (!r || !r.ok || !onDetail) return;
  const draft = (state.jobs.find(x => x.id === jobId)?.sov_view?.period || {}).draft;
  if (draft) nav("payapp-detail", jobId, draft.id);
}

function sovAmounts(jobId) {
  const period = state.jobs.find(j => j.id === jobId)?.sov_view?.period;
  if (!period) return;
  const amounts = {};
  document.querySelectorAll("[data-sova]").forEach(el => {
    const line = period.lines.find(l => l.line_id === el.dataset.sova);
    if (!line) return;
    const raw = el.value.trim();
    if (!raw) { if (line.changed) amounts[el.dataset.sova] = ""; return; }
    if (sovCents(raw) !== line.final_cents) amounts[el.dataset.sova] = raw;
  });
  if (!Object.keys(amounts).length) { toast("Change an amount first."); return; }
  return sovSaveAmounts(jobId, amounts);
}

function sovAmountsReset(jobId) {
  const period = state.jobs.find(j => j.id === jobId)?.sov_view?.period;
  if (!period) return;
  const amounts = Object.fromEntries(period.lines.filter(l => l.changed).map(l => [l.line_id, ""]));
  return sovSaveAmounts(jobId, amounts);
}

async function sovProgress(jobId) {
  const done = {};
  let tooFine = false;
  document.querySelectorAll("[data-sovp]").forEach(el => {
    if (el.disabled) return;
    const raw = el.value.trim();
    if (!raw) return;
    if (el.dataset.lump === "1") {
      const qty = lumpDone(raw);
      if (qty === null) tooFine = true;
      else done[el.dataset.sovp] = qty;
    } else done[el.dataset.sovp] = raw;
  });
  if (tooFine) { toast("A lump-sum % done can have at most one decimal, like 99.9."); return; }
  const stored = {};
  document.querySelectorAll("[data-sovs]").forEach(el => {
    if (el.disabled) return;
    const raw = el.value.trim();
    const line = (state.jobs.find(j => j.id === jobId)?.sov_view?.lines || []).find(l => l.id === el.dataset.sovs);
    const before = line && line.stored_cents ? (line.stored_cents / 100).toFixed(2) : "";
    if (raw !== before) stored[el.dataset.sovs] = raw || "0";
  });
  const notes = document.querySelector("#progress-notes")?.value || "";
  const r = await post(`/api/jobs/${jobId}/sov-progress`, { done, stored, notes, period_to: formValues("sovprogress").period_to || "" }, { formKey: "sovprogress" });
  if (r && r.ok) { formOpen.progress = false; render(); }
}

document.addEventListener("input", (e) => {
  const el = e.target;
  if (!sovDraft || !el || !el.dataset || !el.dataset.sovField) return;
  if (el.dataset.sovField === "op_pct") sovDraft.op_pct = el.value;
  else if (sovDraft.lines[+el.dataset.sovRow]) sovDraft.lines[+el.dataset.sovRow][el.dataset.sovField] = el.value;
  updateSovTotals();
});
document.addEventListener("paste", (e) => {
  if (e.target && e.target.id === "sov-paste") setTimeout(sovReadPaste, 0);
});

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  "sov-start": (el, id) => { sovStartEdit(state.jobs.find(x => x.id === id)); render(); },
  "sov-add": () => { sovDraft.lines.push(blankSovLine()); render(); document.querySelectorAll(".sov-desc")[sovDraft.lines.length - 1]?.focus(); },
  "sov-remove": (el) => { sovDraft.lines.splice(+el.dataset.row, 1); if (!sovDraft.lines.length) sovDraft.lines.push(blankSovLine()); render(); },
  "sov-read": () => sovReadPaste(),
  "sov-cancel": () => { sovDraft = null; render(); },
  "sov-save": (el, id) => sovSave(id, false),
  "sov-save-contract": (el, id) => sovSave(id, true),
  "sov-progress": (el, id) => sovProgress(id),
  "sov-amounts": (el, id) => sovAmounts(id),
  "sov-amounts-reset": (el, id) => sovAmountsReset(id),
  "sov-correct-open": (el, id) => { clearForm("sovcorrect"); sovCorrecting = { jobId: id, lineId: el.dataset.line }; render(); document.getElementById("sov-correct-qty")?.focus(); },
  "sov-correct-cancel": () => { sovCorrecting = null; render(); },
  "sov-correct-save": (el, id) => sovCorrectSave(id),
};
