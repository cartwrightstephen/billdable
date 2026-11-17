// What a pay app's confirms say: Approve & send (round 1, item 5) and Delete draft (item 20).
// No page here, so node can test it, and both the pay app page and the job page can use it.
import { unesc, money, fmtDate } from "./format.js";
import { termText } from "./terms.js";

// Who it goes to, how much and the period, as the waiver send names who it goes to.
export function sendConfirmText(j, inv) {
  const p = j.payer || {};
  const to = unesc(`${p.approver || p.name || "the client"}${p.ap_email ? " · " + p.ap_email : ""}`);
  const copies = p.copy && unesc(p.copy) !== "None" ? ` A copy goes to ${unesc(p.copy)}.` : "";
  return `Email ${unesc(inv.number)} for ${money(inv.amount_cents)}${inv.period_to ? `, period to ${fmtDate(inv.period_to)}` : ""}, to ${to}?${copies}`;
}

// A change order billed on the draft comes off the bill with it.
export function deleteDraftText(j) {
  const d = (j.invoices || []).find(i => ["draft", "awaiting_approval"].includes(i.status));
  const carried = new Set(((d && d.lines) || []).map(l => l.co_id).filter(Boolean));
  const cos = (j.change_orders || []).filter(c => carried.has(c.id) && c.included_in_billing).map(c => `CO ${unesc(String(c.number))}`);
  const names = cos.length === 1 ? cos[0] : cos.slice(0, -1).join(", ") + " and " + cos[cos.length - 1];
  const off = cos.length ? ` ${names} come${cos.length === 1 ? "s" : ""} off the bill.` : "";
  return termText(`Delete the open draft pay app${d ? " " + unesc(d.number) : ""}?${off} You can draft it again from the job page.`);
}
