// The Payments page's QuickBooks import (R5-03): rows the server proposed from a CSV, a pay app
// picked by hand for a row that needs one, a payment larger than its pay app split in two, and
// what is left on the page after Record. Pure functions over the rows (already HTML-escaped on
// arrival, like the rest of state), so node can test them (tests/js/payimport.test.mjs).

import { money } from "./format.js";

// Rows a person can tick: a match, or a row they picked a pay app for.
export const canRecord = (row) => Boolean(row.job_id && row.invoice_id);

// Rows that get the pay-app select instead of a pill.
export const needsPick = (row) => ["pick one", "no match", "not recorded"].includes(row.status) && (row.candidates || []).length > 0;

// A pay app picked from the row's select (its index in row.candidates); "" clears the pick.
export function pickPayApp(row, index) {
  const c = index === "" || index == null ? null : (row.candidates || [])[+index];
  if (!c) return { ...row, job_id: null, invoice_id: null, number: null, job: undefined, picked: "", ticked: false };
  return { ...row, job_id: c.job_id, invoice_id: c.invoice_id, number: c.number, job: c.job, picked: String(index), ticked: true };
}

// "Record $owed to PA-X": this row becomes what that pay app still owes, and the rest of the payment
// moves to a new row under it that needs a pay app picked. The rest keeps the reference with
// "(rest)" after it, since a reference is recorded once per job, and keeps a QuickBooks payment's
// id and whole amount (qbo_total_cents), so the server records the two lines as parts of it.
export function splitTooMuch(rows, n) {
  const row = rows[n];
  if (!row || row.status !== "too much" || !row.split || !(row.split.owed_cents > 0)) return rows;
  const s = row.split;
  const restCents = row.amount_cents - s.owed_cents;
  const first = { ...row, status: "match", amount_cents: s.owed_cents, job_id: s.job_id, invoice_id: s.invoice_id, number: s.number, job: s.job,
    ticked: true, split: null, reason: `${money(s.owed_cents)} of ${money(row.amount_cents)}; the rest, ${money(restCents)}, is on the next line.` };
  const rest = { ...row, status: "no match", amount_cents: restCents, ref: row.ref ? `${row.ref} (rest)`.slice(0, 60) : "",
    job_id: null, invoice_id: null, number: null, job: undefined, split: null, ticked: false, picked: "",
    candidates: (row.candidates || []).filter(c => c.invoice_id !== s.invoice_id),
    reason: `The rest of this payment. Pick the pay app it goes to, or leave it unticked.` };
  return [...rows.slice(0, n), first, rest, ...rows.slice(n + 1)];
}

// The ticked rows as the server's payments-apply body expects them; `key` comes back with each result.
export function picksFrom(rows, raw = (v) => v) {
  return rows.map((row, n) => ({ row, n })).filter(({ row }) => canRecord(row) && row.ticked !== false)
    .map(({ row, n }) => ({ key: n, job_id: row.job_id, invoice_id: row.invoice_id, amount_cents: row.amount_cents,
      reference: raw(row.ref), date: row.date, qbo_id: row.qbo_id ? raw(row.qbo_id) : undefined,
      // The whole QuickBooks payment: both lines of a split keep the id, and the server records up to this.
      qbo_total_cents: row.qbo_id ? row.qbo_total_cents : undefined }));
}

// After Record: recorded rows go, a row the server refused stays marked "not recorded" with the
// reason, the rest stay as they were. The import closes when nothing is left to do.
export function afterRecord(rows, results) {
  const byKey = new Map(results.map(r => [String(r.key), r]));
  const left = [];
  rows.forEach((row, n) => {
    const r = byKey.get(String(n));
    if (r && r.ok) return;
    if (r) left.push({ ...row, status: "not recorded", reason: r.error || "Not recorded.", ticked: false, picked: "",
      job_id: null, invoice_id: null, number: null, job: undefined });
    else left.push(row);
  });
  const done = results.filter(r => r.ok).length;
  const failed = results.length - done;
  const toPick = left.filter(r => (needsPick(r) && r.status !== "not recorded") || r.status === "too much").length;
  let text = `${done} recorded.`;
  if (failed) text += ` ${failed} not recorded; the reason is on ${failed === 1 ? "its line" : "their lines"} below.`;
  if (toPick) text += ` ${toPick} need${toPick === 1 ? "s" : ""} a pay app picked below.`;
  const open = failed > 0 || toPick > 0;
  return { rows: open ? left : null, toast: text };
}

// After Record the pay apps' balances have moved: each row's list shows what is owed now, and a pay
// app paid in full drops out. A "too much" row whose pay app is now paid becomes a row to pick for.
const OPEN_FOR_PAYMENT = ["sent", "approved", "partial"];   // domain.OPEN_FOR_PAYMENT
export function refreshOwed(rows, jobs) {
  const owed = new Map();
  (jobs || []).forEach(j => { if (j.closed) return; (j.invoices || []).forEach(inv => {
    if (!OPEN_FOR_PAYMENT.includes(inv.status)) return;
    const paid = (j.payments || []).filter(p => p.invoice_id === inv.id).reduce((a, p) => a + p.amount_cents, 0);
    if (inv.amount_cents - paid > 0) owed.set(inv.id, inv.amount_cents - paid);
  }); });
  const now = (c) => owed.has(c.invoice_id) ? { ...c, owed_cents: owed.get(c.invoice_id) } : null;
  return rows.map(row => {
    let next = { ...row, candidates: (row.candidates || []).map(now).filter(Boolean) };
    // A pick keeps pointing at its pay app, or is cleared when that one dropped out.
    if (row.picked) {
      const at = next.candidates.findIndex(c => c.invoice_id === row.invoice_id);
      next = at < 0 ? pickPayApp(next, "") : { ...next, picked: String(at) };
    }
    if (row.status === "too much" && row.split) {
      next.split = now(row.split);
      if (!next.split) Object.assign(next, { status: "no match", reason: "That pay app is paid now. Pick another, or leave it unticked." });
    }
    return next;
  });
}
