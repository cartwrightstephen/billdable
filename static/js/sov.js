// Schedule of values: quantities in thousandths, money in cents, and the continuation sheet (G703 columns).

import { esc, money, plainNumber } from "./format.js";
import { draftValue } from "./forms.js";

// ---------- schedule of values ----------
// Same integer arithmetic as sov.py, so the totals shown here match what the server saves.
export function sovNum(v) {
  const t = plainNumber(v);
  if (!t || !/^-?\d*\.?\d+$/.test(t)) return NaN;
  return parseFloat(t);
}

// A number scaled to whole units of 10^-places, or NaN when it has more decimals than that: the
// server refuses those (sov.to_milli, to_cents, to_bp) rather than rounding them.
function scaled(v, places) {
  const n = sovNum(v);
  if (isNaN(n)) return NaN;
  const frac = (plainNumber(v).split(".")[1] || "").replace(/0+$/, "");
  return frac.length > places ? NaN : Math.round(n * 10 ** places);
}

export function sovMilli(v) { return scaled(v, 3); }

export function sovCents(v) { return scaled(v, 2); }

// Overhead and profit in basis points ("15.5" -> 1550), at most two decimals.
export function sovBp(v) { return scaled(v, 2); }

// A lump-sum line's "% done" as the quantity to send (the line's quantity is 1, held in thousandths,
// so a tenth of a percent is the finest step). null when the percent has more than one decimal:
// rounding it would bill a different amount than was typed. Not a number: sent as typed for the
// server to explain.
export function lumpDone(raw) {
  if (isNaN(sovNum(raw))) return raw;
  const tenths = scaled(raw, 1);
  return isNaN(tenths) ? null : (tenths / 1000).toFixed(3);
}

export function sovExtend(q, r) { return Math.floor((q * r + 500) / 1000); }

export function sovMarkup(base, bp) { return Math.floor((base * bp + 5000) / 10000); }

export function fmtQty(milli) {
  const n = milli / 1000;
  return n.toLocaleString("en-US", { maximumFractionDigits: 3 });
}

export function blankSovLine() { return { id: "", item: "", description: "", quantity: "", unit: "", rate: "" }; }

// What is wrong with an editor line, as clean_lines in sov.py would say it: "" when it is fine or
// blank, "price" when it has everything but a unit rate (a pasted "3 loads"), "fix" otherwise.
export function sovLineProblem(l) {
  const has = (x) => String(x || "").trim() !== "";
  if (![l.description, l.quantity, l.rate].some(has)) return "";
  const q = has(l.quantity) ? sovMilli(l.quantity) : 1000;
  if (!has(l.description) || !(q > 0)) return "fix";
  if (!has(l.rate)) return "price";
  return sovCents(l.rate) > 0 ? "" : "fix";
}

// The schedule card's note when the only thing keeping the schedule fixed is an unsent draft
// (sov_view.draft_lock from the server; Oct 23 note 8). The rule is the server's; this only says
// why and offers Delete draft (the void-draft action, with its own confirm) to whoever may use it.
// Someone who may not delete drafts is told an owner can. A job with no schedule yet gets one
// quiet line saying how to set one up, since its card would otherwise be empty.
// j comes escaped from the state, so its fields go in as they are.
export function sovDraftLockHtml(j, canDelete) {
  const v = j.sov_view || {};
  const lock = v.draft_lock;
  if (!lock) return "";
  let text;
  if (v.present) {
    text = `The schedule is fixed while ${lock.number} is a draft. ` +
      (canDelete ? "Delete the draft to change it." : "An owner can delete the draft to change it.");
  } else {
    text = canDelete
      ? `To bill line by line, delete draft ${lock.number} first, then set up a schedule of values.`
      : `To bill line by line, an owner has to delete draft ${lock.number} first. Then you can set up a schedule of values.`;
  }
  const button = canDelete ? ` <button class="btn small secondary" data-action="void-draft" data-id="${j.id}">Delete draft</button>` : "";
  return `<p class="sub sov-lock">${text}${button}</p>`;
}

// What a progress line's box is called on a phone, where the table is a card per line and the
// column heading is gone (R6-02): a lump sum is a percent, anything else a quantity in its unit.
export function doneLabel(l) { return l.lump_sum ? "% done" : `Qty done (${l.unit})`; }

// periodHtml: the "Period to" box (R4-04); errorLine: the form's error line.
// What was typed in a box is kept (forms.js, data-field) across a redraw; else the saved value.
export function sovProgressHtml(j, periodHtml = "", errorLine = "") {
  const v = j.sov_view;
  const typed = (name, saved) => esc(draftValue("sovprogress", name, saved));
  // M1: an open draft that is only here is replaced, keeping its number (domain record_sov_progress).
  const replaces = (j.invoices || []).find(i => ["draft", "awaiting_approval"].includes(i.status) && i.pencil_status !== "sent");
  const rows = v.lines.map(l => `
    <tr>
      <td>${l.item}</td><td class="sov-desc-cell">${l.description}</td>
      <td class="num">${l.lump_sum ? "lump sum" : fmtQty(l.qty_milli) + " " + l.unit}</td>
      <td data-label="${doneLabel(l)}"><input class="sov-in sov-num" data-sovp="${l.id}" data-lump="${l.lump_sum ? 1 : 0}" data-field="done_${l.id}" value="${typed(`done_${l.id}`, String(l.lump_sum ? +(l.done_milli / 10).toFixed(1) : l.done_milli / 1000))}" inputmode="decimal" aria-label="${l.description}: done to date" ${l.done_milli >= l.qty_milli ? "disabled" : ""}> ${l.lump_sum ? "%" : l.unit}</td>
      <td><input class="sov-in sov-num" data-sovs="${l.id}" data-field="stored_${l.id}" value="${typed(`stored_${l.id}`, l.stored_cents ? (l.stored_cents / 100).toFixed(2) : "")}" placeholder="0.00" inputmode="decimal" aria-label="${l.description}: materials stored, dollars" ${l.done_milli >= l.qty_milli ? "disabled" : ""}></td>
      <td class="sub">${l.billed_qty_milli ? `billed ${l.lump_sum ? (l.billed_qty_milli / 10).toFixed(1) + "%" : fmtQty(l.billed_qty_milli)}` : ""}</td>
    </tr>`).join("");
  return `<div class="card sov-progress" data-form-key="sovprogress">${errorLine}
    <p class="sub">Enter how much of each line is done to date (not this period), and the dollars of materials delivered and stored on site but not yet installed. Duracash bills the difference since the last pay app, plus overhead and profit, less retainage. Lower the stored amount as materials are installed.</p>
    <div class="table-wrap sov-wrap"><table class="sov-table">
      <tr><th>Item</th><th>Description</th><th>Scheduled</th><th>Done to date</th><th>Stored materials $</th><th></th></tr>
      ${rows}
    </table></div>
    ${periodHtml ? `<div class="form-row">${periodHtml}</div>` : ""}
    ${replaces ? `<p class="sub">This replaces ${replaces.number} (${money(replaces.amount_cents)}). Its number and paperwork stay.</p>` : ""}
    <div class="form-inline"><input id="progress-notes" data-field="notes" value="${typed("notes", "")}" placeholder="What was completed (printed on the pay app)" aria-label="What was completed, printed on the pay app" class="grow"><button class="btn small" data-action="sov-progress" data-id="${j.id}">Draft progress pay app</button></div>
  </div>`;
}

// The same G703 columns as sov.sheet_columns(); entries from before stored materials
// have no work/stored split, so all of their billing counts as work.
export function sheetColumns(e, retPct) {
  const stored = e.stored_cents || 0;
  const thisPeriod = e.work_this_cents ?? e.amount_cents;
  const previous = e.prev_work_cents ?? (e.to_date_cents - e.amount_cents - stored);
  return { previous, thisPeriod, stored, pct: e.scheduled_cents ? Math.floor(100 * e.to_date_cents / e.scheduled_cents) : 0,
           balance: e.scheduled_cents - e.to_date_cents, retainage: Math.floor((e.to_date_cents * retPct + 50) / 100) };
}

export function continuationHtml(inv, job) {
  const rows = (inv.sov_sheet && inv.sov_sheet.length ? inv.sov_sheet : inv.sov_lines) || [];
  if (!rows.length) return "";
  const retPct = (inv.retainage_pct ?? (job && job.retainage_pct)) || 0;   // as held on this pay app (R4-07)
  return `<div class="card">
    <h2>Continuation sheet</h2>
    <div class="table-wrap sov-wrap"><table class="sov-table">
      <tr><th>Item</th><th>Description</th><th>Scheduled value</th><th>Previous</th><th>This period</th><th>Stored</th><th>Total to date</th><th>%</th><th>Balance to finish</th>${retPct ? `<th>Retainage ${retPct}%</th>` : ""}</tr>
      ${rows.map(e => { const c = sheetColumns(e, retPct); return `<tr><td>${e.item}</td><td class="sov-desc-cell">${e.description}</td><td class="num">${money(e.scheduled_cents)}</td><td class="num">${money(c.previous)}</td><td class="num">${money(c.thisPeriod)}${e.qty_milli_this && e.unit.toUpperCase() !== "LS" ? `<div class="sub">${fmtQty(e.qty_milli_this)} ${e.unit}</div>` : ""}</td><td class="num">${money(c.stored)}</td><td class="num">${money(e.to_date_cents)}</td><td class="num">${c.pct}%</td><td class="num">${money(c.balance)}</td>${retPct ? `<td class="num">${money(c.retainage)}</td>` : ""}</tr>`; }).join("")}
      ${inv.op_cents ? `<tr><td></td><td class="sov-desc-cell">Overhead and profit</td><td></td><td></td><td class="num">${money(inv.op_cents)}</td><td></td><td></td><td></td><td></td>${retPct ? "<td></td>" : ""}</tr>` : ""}
    </table></div>
    ${retPct ? `<p class="sub">The retainage column is ${retPct}% of each line's total to date, for reference.</p>` : ""}
  </div>`;
}
