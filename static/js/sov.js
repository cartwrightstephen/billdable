// Schedule of values: quantities in thousandths, money in cents, and the continuation sheet (G703 columns).

import { esc, money, plainNumber, fmtDate } from "./format.js";
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

// The schedule card's note when the only thing keeping the schedule fixed is a draft that is with
// the client for review (sov_view.draft_lock from the server). The rule is the server's; this only
// says why and offers Delete draft (the void-draft action, with its own confirm) to whoever may use
// it. Someone who may not delete drafts is told an owner can.
// j comes escaped from the state, so its fields go in as they are.
export function sovDraftLockHtml(j, canDelete) {
  const v = j.sov_view || {};
  const lock = v.draft_lock;
  if (!lock) return "";
  const text = `${v.present ? "The schedule is fixed" : "A schedule can't be set"} while ${lock.number} is with the client for review. ` +
    (canDelete ? "Record their answer or delete the draft to change it." : "Record their answer, or ask an owner to delete the draft, to change it.");
  const button = canDelete ? ` <button class="btn small secondary" data-action="void-draft" data-id="${j.id}">Delete draft</button>` : "";
  return `<p class="sub sov-lock">${text}${button}</p>`;
}

// What the schedule card says when the schedule can't be changed, or doesn't exist and can't now:
// the rule is in domain._sov_view. Blank when it can be changed.
export function sovFixedNote(j, canDelete) {
  const v = j.sov_view || {};
  if (v.editable) return "";
  const lock = sovDraftLockHtml(j, canDelete);
  if (lock) return lock;
  if (v.present) {
    return v.sent
      ? `<p class="sub sov-lock">Fixed since ${v.sent} was sent. Scope changes go through change orders, which add their own lines when approved.</p>`
      : `<p class="sub sov-lock">Fixed because the work is finished.</p>`;
  }
  return v.sent
    ? `<p class="sub sov-lock">This job bills in dollars: ${v.sent} was sent without a schedule of values, and a schedule can only be set before the first pay app is sent.</p>`
    : `<p class="sub sov-lock">This job bills in dollars. It has no schedule of values, and a schedule is set before the work is done.</p>`;
}

// How much of a line a quantity is: a lump sum in %, anything else in its unit.
function periodQty(l, milli) {
  return l.lump_sum ? `${+(milli / 10).toFixed(1)}%` : `${fmtQty(milli)} ${l.unit}`;
}

// The current billing period (sov_view.period, domain._sov_period): each line's quantity done this
// period, how much came from job records, what it adds up to, the suggested amount, and the amount
// on the draft. canChange puts a box on each line, for changing what the draft bills.
export function sovPeriodHtml(j, canChange = false) {
  const v = j.sov_view || {};
  const p = v.period;
  if (!p) return "";
  const draft = p.draft;
  const editing = canChange && draft && !draft.with_client;
  const rows = p.lines.filter(l => editing || draft ? true : l.qty_this_milli || l.suggested_cents);
  const when = `${p.from ? `Since ${fmtDate(p.from)}` : "Since the job started"}${p.to ? `, to ${fmtDate(p.to)}` : ""}`;
  if (!rows.length && !p.unsigned_cents) {
    return `<div class="sov-period"><h3>This billing period</h3><p class="sub">${when}. No quantities recorded yet.</p></div>`;
  }
  const finalCell = (l) => {
    if (!draft) return "";
    if (editing && !l.credit) {
      const typed = esc(draftValue("sovamounts", `amt_${l.line_id}`, (l.final_cents / 100).toFixed(2)));
      return `<td data-label="On ${draft.number}"><input id="sova-${l.line_id}" class="sov-in sov-num" data-sova="${l.line_id}" data-field="amt_${l.line_id}" value="${typed}" inputmode="decimal" aria-label="${l.description}: amount on ${draft.number}, dollars"></td>`;
    }
    return `<td class="num" data-label="On ${draft.number}">${money(l.final_cents)}</td>`;
  };
  const moneyRow = (label, cents, cls = "") => `<div class="money-row${cls}"><span class="label">${label}</span><span>${money(cents)}</span></div>`;
  const suggested = `${moneyRow("Suggested pay app value", p.suggested_gross_cents, " sov-suggested")}
      ${p.suggested_retainage_cents ? moneyRow(`Less retainage ${p.retainage_pct}%`, -p.suggested_retainage_cents) + moneyRow("Suggested amount due", p.suggested_net_cents) : ""}`;
  const final = !draft ? "" : `${moneyRow(`On ${draft.number}`, p.final_gross_cents, " sov-suggested")}
      ${p.final_retainage_cents ? moneyRow(`Less retainage ${p.retainage_pct}%`, -p.final_retainage_cents) + moneyRow("Amount due", p.final_net_cents) : ""}`;
  return `<div class="sov-period" data-form-key="sovamounts">
    <h3>This billing period</h3>
    <p class="sub">${when}.${editing ? ` Change any amount to bill what you decide; ${draft.number} uses your amounts. Clear a box to go back to the suggested amount.` : ""}</p>
    ${rows.length ? `<div class="table-wrap sov-wrap"><table class="sov-table">
      <tr class="head-row"><th>Item</th><th>Description</th><th>Done this period</th><th>Adds up to</th><th>Suggested</th>${draft ? `<th>On ${draft.number}</th>` : ""}</tr>
      ${rows.map(l => `<tr><td>${l.item}</td><td class="sov-desc-cell">${l.description}</td>
        <td class="num" data-label="Done this period">${periodQty(l, l.qty_this_milli)}${l.record_count ? `<div class="sub">${periodQty(l, l.from_records_milli)} from ${l.record_count} job record${l.record_count === 1 ? "" : "s"}</div>` : ""}</td>
        <td class="num" data-label="Adds up to">${money(l.adds_up_cents)}</td>
        <td class="num" data-label="Suggested">${money(l.suggested_cents)}</td>
        ${finalCell(l)}</tr>`).join("")}
    </table></div>` : ""}
    <div class="sov-sums">
      ${p.op_suggested_cents || p.op_final_cents ? moneyRow(`Overhead and profit ${v.op_bp / 100}%`, p.op_suggested_cents) : ""}
      ${p.unsigned_cents ? moneyRow("Unsigned change orders on the bill", p.unsigned_cents) : ""}
      ${suggested}
      ${final}
    </div>
    ${editing ? `<div class="form-inline"><button class="btn small" data-action="sov-amounts" data-id="${j.id}">Save amounts</button>${p.changed ? `<button class="btn small secondary" data-action="sov-amounts-reset" data-id="${j.id}">Back to the suggested amounts</button>` : ""}</div>` : ""}
  </div>`;
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
  // Filled in from the field total (or what is billed, when that is more); a line billed in full is closed.
  const rows = v.lines.map(l => { const start = Math.max(l.done_milli, l.billed_qty_milli || 0), closed = (l.billed_qty_milli || 0) >= l.qty_milli; return `
    <tr>
      <td>${l.item}</td><td class="sov-desc-cell">${l.description}</td>
      <td class="num">${l.lump_sum ? "lump sum" : fmtQty(l.qty_milli) + " " + l.unit}</td>
      <td data-label="${doneLabel(l)}"><input id="sovp-done-${l.id}" class="sov-in sov-num" data-sovp="${l.id}" data-lump="${l.lump_sum ? 1 : 0}" data-field="done_${l.id}" value="${typed(`done_${l.id}`, String(l.lump_sum ? +(start / 10).toFixed(1) : start / 1000))}" inputmode="decimal" aria-label="${l.description}: done to date" ${closed ? "disabled" : ""}> ${l.lump_sum ? "%" : l.unit}</td>
      <td><input id="sovp-stored-${l.id}" class="sov-in sov-num" data-sovs="${l.id}" data-field="stored_${l.id}" value="${typed(`stored_${l.id}`, l.stored_cents ? (l.stored_cents / 100).toFixed(2) : "")}" placeholder="0.00" inputmode="decimal" aria-label="${l.description}: materials stored, dollars" ${closed ? "disabled" : ""}></td>
      <td class="sub">${l.billed_qty_milli ? `billed ${l.lump_sum ? (l.billed_qty_milli / 10).toFixed(1) + "%" : fmtQty(l.billed_qty_milli)}` : ""}</td>
    </tr>`; }).join("");
  return `<div class="card sov-progress">${sovPeriodHtml(j)}<div data-form-key="sovprogress">${errorLine}
    <p class="sub">Each line is filled in with the total recorded to date (not this period). Change a figure to bill a different quantity; what the field recorded stays as it is. Add the dollars of materials delivered and stored on site but not yet installed, and lower them as materials are installed. Duracash bills the difference since the last pay app, plus overhead and profit, less retainage.</p>
    <div class="table-wrap sov-wrap"><table class="sov-table">
      <tr><th>Item</th><th>Description</th><th>Scheduled</th><th>Done to date</th><th>Stored materials $</th><th></th></tr>
      ${rows}
    </table></div>
    ${periodHtml ? `<div class="form-row">${periodHtml}</div>` : ""}
    ${replaces ? `<p class="sub">This replaces ${replaces.number} (${money(replaces.amount_cents)}). Its number and supporting documents stay.</p>` : ""}
    <div class="form-inline"><input id="progress-notes" data-field="notes" value="${typed("notes", "")}" placeholder="What was completed (printed on the pay app)" aria-label="What was completed, printed on the pay app" class="grow"><button class="btn small" data-action="sov-progress" data-id="${j.id}">Draft progress pay app</button></div>
  </div></div>`;
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

// The quantity completed to date with its unit (sov.qty_to_date, Stephen's Oct 29 review, M2):
// blank for a lump sum and for entries from before quantities were kept.
export function qtyToDate(e) {
  if (String(e.unit || "").toUpperCase() === "LS" || e.qty_milli_to_date == null) return "";
  return `${fmtQty(e.qty_milli_to_date)} ${e.unit || ""}`.trim();
}

export function continuationHtml(inv, job) {
  const rows = (inv.sov_sheet && inv.sov_sheet.length ? inv.sov_sheet : inv.sov_lines) || [];
  if (!rows.length) return "";
  const retPct = (inv.retainage_pct ?? (job && job.retainage_pct)) || 0;   // as held on this pay app (R4-07)
  return `<div class="card">
    <h2>Continuation sheet</h2>
    <div class="table-wrap sov-wrap"><table class="sov-table">
      <tr><th>Item</th><th>Description</th><th>Scheduled value</th><th>Previous</th><th>This period</th><th>Stored</th><th>Qty to date</th><th>Total to date</th><th>%</th><th>Balance to finish</th>${retPct ? `<th>Retainage ${retPct}%</th>` : ""}</tr>
      ${rows.map(e => { const c = sheetColumns(e, retPct); return `<tr><td>${e.item}</td><td class="sov-desc-cell">${e.description}</td><td class="num">${money(e.scheduled_cents)}</td><td class="num">${money(c.previous)}</td><td class="num">${money(c.thisPeriod)}${e.qty_milli_this && e.unit.toUpperCase() !== "LS" ? `<div class="sub">${fmtQty(e.qty_milli_this)} ${e.unit}</div>` : ""}</td><td class="num">${money(c.stored)}</td><td class="num">${qtyToDate(e)}</td><td class="num">${money(e.to_date_cents)}</td><td class="num">${c.pct}%</td><td class="num">${money(c.balance)}</td>${retPct ? `<td class="num">${money(c.retainage)}</td>` : ""}</tr>`; }).join("")}
      ${inv.op_cents ? `<tr><td></td><td class="sov-desc-cell">Overhead and profit</td><td></td><td></td><td class="num">${money(inv.op_cents)}</td><td></td><td></td><td></td><td></td><td></td>${retPct ? "<td></td>" : ""}</tr>` : ""}
    </table></div>
    ${retPct ? `<p class="sub">The retainage column is ${retPct}% of each line's total to date, for reference.</p>` : ""}
  </div>`;
}
