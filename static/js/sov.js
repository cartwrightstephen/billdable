// Schedule of values: quantities in thousandths, money in cents, and the continuation sheet (G703 columns).

import { money } from "./format.js";

// ---------- schedule of values ----------
// Same integer arithmetic as sov.py, so the totals shown here match what the server saves.
export function sovNum(v) {
  const t = String(v ?? "").replace(/[$,\s%]/g, "");
  if (!t || !/^-?\d*\.?\d+$/.test(t)) return NaN;
  return parseFloat(t);
}

export function sovMilli(v) { const n = sovNum(v); return isNaN(n) ? NaN : Math.round(n * 1000); }

export function sovCents(v) { const n = sovNum(v); return isNaN(n) ? NaN : Math.round(n * 100); }

export function sovExtend(q, r) { return Math.floor((q * r + 500) / 1000); }

export function sovMarkup(base, bp) { return Math.floor((base * bp + 5000) / 10000); }

export function fmtQty(milli) {
  const n = milli / 1000;
  return n.toLocaleString("en-US", { maximumFractionDigits: 3 });
}

export function blankSovLine() { return { id: "", item: "", description: "", quantity: "", unit: "", rate: "" }; }

export function sovProgressHtml(j) {
  const v = j.sov_view;
  const rows = v.lines.map(l => `
    <tr>
      <td>${l.item}</td><td>${l.description}</td>
      <td class="num">${l.lump_sum ? "lump sum" : fmtQty(l.qty_milli) + " " + l.unit}</td>
      <td><input class="sov-in sov-num" data-sovp="${l.id}" data-lump="${l.lump_sum ? 1 : 0}" value="${l.lump_sum ? +(l.done_milli / 10).toFixed(1) : l.done_milli / 1000}" inputmode="decimal" aria-label="${l.description}: done to date" ${l.done_milli >= l.qty_milli ? "disabled" : ""}> ${l.lump_sum ? "%" : l.unit}</td>
      <td><input class="sov-in sov-num" data-sovs="${l.id}" value="${l.stored_cents ? (l.stored_cents / 100).toFixed(2) : ""}" placeholder="0.00" inputmode="decimal" aria-label="${l.description}: materials stored, dollars" ${l.done_milli >= l.qty_milli ? "disabled" : ""}></td>
      <td class="sub">${l.billed_qty_milli ? `billed ${l.lump_sum ? (l.billed_qty_milli / 10).toFixed(1) + "%" : fmtQty(l.billed_qty_milli)}` : ""}</td>
    </tr>`).join("");
  return `<div class="card sov-progress">
    <p class="sub">Enter how much of each line is done to date (not this period), and the dollars of materials delivered and stored on site but not yet installed. Billdable bills the difference since the last pay app, plus overhead and profit, less retainage. Lower the stored amount as materials are installed.</p>
    <div class="table-wrap"><table class="sov-table">
      <tr><th>Item</th><th>Description</th><th>Scheduled</th><th>Done to date</th><th>Stored materials $</th><th></th></tr>
      ${rows}
    </table></div>
    <div class="form-inline"><input id="progress-notes" placeholder="What was completed" class="grow"><button class="btn small" data-action="sov-progress" data-id="${j.id}">Draft progress pay app</button></div>
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
  const retPct = (job && job.retainage_pct) || 0;
  return `<div class="card">
    <h2>Continuation sheet</h2>
    <div class="table-wrap"><table class="sov-table">
      <tr><th>Item</th><th>Description</th><th>Scheduled value</th><th>Previous</th><th>This period</th><th>Stored</th><th>Total to date</th><th>%</th><th>Balance to finish</th>${retPct ? `<th>Retainage ${retPct}%</th>` : ""}</tr>
      ${rows.map(e => { const c = sheetColumns(e, retPct); return `<tr><td>${e.item}</td><td>${e.description}</td><td class="num">${money(e.scheduled_cents)}</td><td class="num">${money(c.previous)}</td><td class="num">${money(c.thisPeriod)}${e.qty_milli_this && e.unit.toUpperCase() !== "LS" ? `<div class="sub">${fmtQty(e.qty_milli_this)} ${e.unit}</div>` : ""}</td><td class="num">${money(c.stored)}</td><td class="num">${money(e.to_date_cents)}</td><td class="num">${c.pct}%</td><td class="num">${money(c.balance)}</td>${retPct ? `<td class="num">${money(c.retainage)}</td>` : ""}</tr>`; }).join("")}
      ${inv.op_cents ? `<tr><td></td><td>Overhead and profit</td><td></td><td></td><td class="num">${money(inv.op_cents)}</td><td></td><td></td><td></td><td></td>${retPct ? "<td></td>" : ""}</tr>` : ""}
    </table></div>
    ${retPct ? `<p class="sub">The retainage column is ${retPct}% of each line's total to date, for reference.</p>` : ""}
  </div>`;
}
