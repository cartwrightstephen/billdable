// Where a job stands: blocked or not, the one next step, status pills, change-order signatures and waiver kinds.

import { unesc, fmtDate, fmtDateTime, toDate, centsFrom, percentFrom, money } from "./format.js";

export const RECORD_KINDS = {
  note: "Note", daily: "Daily record", photo: "Photo", contract: "Contract", change_order: "Change order",
  waiver: "Lien waiver", pay_app: "Pay app / invoice backup", ticket: "Delivery or time ticket", other: "Other",
};

export const SOURCE_LABELS = { upload: "Upload", paste: "Pasted", email: "Email", sms: "Text message", notes: "Notes" };

// One short word for where a job stands; the full sentence (status_label) goes on the line below it.
export function jobStatus(j) {
  const inv = j.invoices || [];
  if (j.closed) return ["Closed", "green"];
  if (isBlocked(j)) return needsPaperwork(j) ? ["Needs paperwork", "amber"] : ["Blocked", "red"];
  if (inv.some(i => ["draft", "awaiting_approval"].includes(i.status))) return ["Ready to send", "copper"];
  if (j.waiver_due && !j.waiver_sent) return ["Waiver due", "amber"];
  if (inv.some(i => i.status === "partial")) return ["Partly paid", "amber"];
  if (inv.some(i => ["sent", "approved"].includes(i.status))) return ["Sent", "amber"];
  if (j.work_status !== "complete") return ["In progress", ""];
  if (j.money.unbilled_completed_cents > 0) return ["Ready to bill", "copper"];
  if (j.money.retainage_open_cents > 0) return ["Retainage open", "amber"];
  if (inv.length && j.money.outstanding_cents === 0) return ["Paid", "green"];
  return ["Done", ""];
}

export function statusPill(j) { const [w, c] = jobStatus(j); return `<span class="pill ${c}">${w}</span>`; }

// Blockers count once there is a pay app to block (a draft) or the work is done; before that
// they are what the next pay app will need.
export function isBlocked(j) {
  return !!(j.blockers && j.blockers.length && (j.work_status === "complete" || j.invoices.some(i => ["draft", "awaiting_approval"].includes(i.status))));
}

// Blocked only by paperwork to attach: amber, not red. Red stays for an unsigned change order on the bill.
export function needsPaperwork(j) {
  return isBlocked(j) && (j.missing_paperwork || []).length === j.blockers.length;
}

// The unsigned change orders on the bill that block send: {kind: "co", text, co_id, number} (R3-05).
export function coBlockers(j) {
  return (j.blocker_items || []).filter(b => b.kind === "co");
}

// Blocked only by unsigned change orders: the way out is a signature, not paperwork.
export function onlyCoBlockers(j) {
  const cos = coBlockers(j);
  return isBlocked(j) && cos.length > 0 && cos.length === j.blockers.length;
}

// The button that opens the paperwork page, or the change order when that is all that blocks.
export function blockerButton(j) {
  const n = j.blockers.length;
  if (needsPaperwork(j)) return `Add ${n} missing document${n === 1 ? "" : "s"}`;
  if (onlyCoBlockers(j)) return n === 1 ? `Get CO ${coBlockers(j)[0].number} signed` : `Get ${n} change orders signed`;
  return `Clear ${n} blocker${n === 1 ? "" : "s"}`;
}

export function nextStep(j) {
  if (j.closed) return "Finished.";
  if (needsPaperwork(j)) return "Attach the paperwork, then send: " + j.missing_paperwork.join(", ");
  if (isBlocked(j)) return "Clear blockers: " + j.blockers[0];
  const draft = j.invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
  if (draft) return "Pay app is ready. Review it, then Approve & send.";
  if (j.waiver_due && !j.waiver_sent) return "Payment received. Approve and send the waiver.";
  if (j.invoices.some(i => ["sent", "approved", "partial"].includes(i.status))) return "Waiting on the payer. Follow up or record a payment.";
  if (j.work_status !== "complete") return "Record progress, or mark the job done." + (j.blockers && j.blockers.length ? " The next pay app needs: " + j.blockers.join("; ") : "");
  if (j.money.retainage_open_cents > 0) return "Chase retainage once the closeout packet is complete.";
  if (j.money.outstanding_cents === 0 && j.money.unbilled_completed_cents === 0) return (j.close_blockers || []).length ? "Before you finish: " + j.close_blockers[0] : "Ready to finish the job.";
  return "Draft a pay app for unbilled completed work.";
}

export function coSignature(co) {
  if (co.signature) return `<div class="money-row"><span class="label">Signed</span><span>${co.signature.name}, ${fmtDateTime(co.signature.at)} (online)</span></div>
    <p class="sub">Kept with it: the consent they agreed to, their network address and browser, and a fingerprint of what they saw (${String(co.signature.snapshot_sha256).slice(0, 12)}…).</p>`;
  if (co.signed_evidence) return `<div class="money-row"><span class="label">Signed</span><span>Paper copy on the job record: ${co.signed_evidence.label} (recorded by ${co.signed_evidence.by}, ${fmtDate(co.signed_evidence.at)})</span></div>`;
  return "";
}

export const WAIVER_KINDS = {
  conditional_progress: "Conditional, progress payment",
  unconditional_progress: "Unconditional, progress payment",
  conditional_final: "Conditional, final payment",
  unconditional_final: "Unconditional, final payment",
};

export function waiverKindLabel(code) { return WAIVER_KINDS[unesc(code)] || code; }

// disabled: sending is off (email not confirmed, or no email for the customer); the page says why.
export function waiverSendForm(j, inv, disabled = false) {
  const key = inv ? inv.id : "job";
  const final = (inv && inv.final) || (j.work_status === "complete" && j.money.unbilled_completed_cents === 0 && j.money.outstanding_cents === 0);
  const pick = final ? "unconditional_final" : "unconditional_progress";
  return `<div class="form-inline">
    <select id="waiver-kind-${key}" aria-label="Waiver type">${Object.entries(WAIVER_KINDS).map(([k, v]) => `<option value="${k}" ${k === pick ? "selected" : ""}>${v}</option>`).join("")}</select>
    <button class="btn small" data-action="waiver" data-id="${j.id}"${inv ? ` data-inv="${inv.id}"` : ""}${disabled ? " disabled" : ""}>Approve & send</button>
  </div>`;
}

// The job a form starts on (R2-07): the one last used when it is still open, else the only open
// job, else none ("Pick a job…").
export function defaultJobId(openJobs, lastId = "") {
  if (lastId && openJobs.some(j => j.id === lastId)) return lastId;
  return openJobs.length === 1 ? openJobs[0].id : "";
}

const DRAFTS = ["draft", "awaiting_approval"];
const OPEN_FOR_PAYMENT = ["sent", "approved", "partial"];   // domain.OPEN_FOR_PAYMENT

// Work already billed, before retainage, on the pay apps that count, leaving out an open draft
// (a progress entry replaces it). The server's _billed_gross, which a draw is added to.
export function billedWork(j) {
  return (j.invoices || []).filter(i => i.status !== "void" && !DRAFTS.includes(i.status))
    .reduce((sum, i) => sum + Number(i.gross_cents ?? (i.amount_cents + (i.retainage_cents || 0))), 0);
}

// The progress form (R3-01): what was typed, in one of three ways, as the pay app it drafts.
// mode: "period" (this draw $, the default), "total" (total done so far $) or "percent" (% complete).
// Returns {field, completed, period, retained, net, error}; field is the request key the number goes in.
export function progressFigures(j, mode, text) {
  const base = billedWork(j);
  const total = j.money.contract_total_cents;
  const out = { field: mode === "period" ? "period_cents" : "completed_cents", completed: null, period: null, retained: 0, net: null, error: "" };
  if (mode === "percent") {
    const pct = percentFrom(text);
    if (pct.error) return { ...out, error: pct.error };
    if (pct.value <= 0 || pct.value > 100) return { ...out, error: "Enter a percent from 1 to 100." };
    out.completed = Math.round(total * pct.value / 100);
  } else {
    const amount = centsFrom(text);
    if (amount.error || amount.cents <= 0) return { ...out, error: mode === "period" ? "Enter this draw in dollars, like 4,850." : "Enter the total done so far in dollars, like 19,400." };
    out.completed = mode === "period" ? base + amount.cents : amount.cents;
  }
  out.period = out.completed - base;
  if (out.completed > total) return { ...out, error: mode === "period" ? `Enter no more than the ${money(total - base)} left on the contract.` : `Enter no more than the contract total, ${money(total)}.` };
  if (out.period <= 0) return { ...out, error: `Enter more than the ${money(base)} already billed. This is the total done so far, not this draw.` };
  // Unsigned change orders put on the bill ride on the same pay app (domain.draft_invoice).
  const cos = (j.change_orders || []).filter(co => co.status === "pending" && co.included_in_billing).reduce((s, co) => s + co.amount_cents, 0);
  const gross = out.period + cos;
  const pct = Number(j.retainage_pct) || 0;
  out.gross = gross;
  out.retained = pct ? Math.floor((gross * pct + 50) / 100) : 0;
  out.net = gross - out.retained;
  return out;
}

// When the next follow-up may go (domain.follow_up_due): the customer's days between follow-ups
// after the last one on the oldest unpaid pay app, or after it was sent; or the date noted after a
// call ("Spoke to them", R4-06) until a follow-up goes out. invoices: every unpaid pay app, which
// one follow-up names. Null with nothing to chase.
export function followUpDue(j) {
  const unpaid = (j.invoices || []).filter(i => OPEN_FOR_PAYMENT.includes(i.status));
  const sent = unpaid[0];
  if (!sent) return null;
  const lastIso = unesc(sent.last_follow_up_at || sent.sent_at || "");
  const last = toDate(lastIso);
  if (!last) return null;
  const noted = j.expected_payment_on && unesc(j.contact_noted_at || "") >= lastIso ? toDate(j.expected_payment_on) : null;
  if (noted) return { invoice: sent, invoices: unpaid, due: noted, noted: true };
  const days = Number(unesc(String((j.payer || {}).follow_up_days || ""))) || 7;
  return { invoice: sent, invoices: unpaid, due: new Date(last.getTime() + days * 86400000) };
}

// "PA-01" or "PA-01 and PA-02" or "PA-01, PA-02 and PA-03": the pay apps one follow-up names.
export function payAppList(invoices) {
  const n = invoices.map(i => i.number);
  return n.length > 1 ? `${n.slice(0, -1).join(", ")} and ${n[n.length - 1]}` : (n[0] || "the pay app");
}

// Required closeout items not in yet (the closeout packet gates asking for retainage).
export function closeoutMissing(j) {
  return (j.closeout_packet || []).filter(p => p.required && !p.present).length;
}

// Why the job page has no "Ask for retainage" while retainage is held (R3-04). Empty when it has one.
export function retainageNote(j) {
  if (!(j.money.retainage_open_cents > 0) || j.closed) return "";
  if (j.work_status !== "complete") return "Released at closeout.";
  const n = closeoutMissing(j);
  return n ? `Closeout paperwork: ${n} item${n === 1 ? "" : "s"} missing.` : "";
}

// "Waiting on Sam. Sent Oct 3; follow-up due Oct 10." while a sent pay app is unpaid. Empty otherwise.
// Once the date has passed: "...; a follow-up has been due since Oct 10."
export function waitingText(j, now = new Date()) {
  const f = followUpDue(j);
  if (!f) return "";
  const p = j.payer || {};
  const when = f.due <= now ? `a follow-up has been due since ${fmtDate(f.due)}` : `follow-up due ${fmtDate(f.due)}`;
  if (f.noted && f.due > now) return `Waiting on ${p.approver || p.name || "the customer"}. Spoke to them ${fmtDate(j.contact_noted_at)}; check again ${fmtDate(f.due)}.`;
  return `Waiting on ${p.approver || p.name || "the customer"}. Sent ${fmtDate(f.invoice.sent_at)}; ${when}.`;
}

// The job page's buttons (R3-04): one main button (or none while the customer has the pay app and
// no follow-up is due), then the rest in the order they matter. Each has a group for the "More"
// list: bill, paid (get paid) or close (close out). demo adds the QuickBooks sample import.
const SECONDARY_ORDER = ["clear", "send", "waiver", "match", "follow", "contact", "pencil", "pencil-ok", "pencil-no", "packet",
  "draft", "progress", "done", "retainage", "retpay", "co", "close", "undo", "void", "qbo"];

export function jobActions(j, { now = new Date(), demo = false } = {}) {
  if (j.closed) return { primary: null, more: [] };
  const m = j.money;
  const draft = (j.invoices || []).find(i => DRAFTS.includes(i.status));
  const pencilOut = draft && draft.pencil_status === "sent";
  const waitingPay = (j.invoices || []).some(i => ["sent", "approved", "partial"].includes(i.status));
  const follow = waitingPay ? followUpDue(j) : null;
  const followDue = !!follow && follow.due <= now;
  const canClose = !(j.close_blockers || []).length;
  const all = [];
  const add = (key, label, attrs, why, group) => all.push({ key, label, attrs, why, group });
  if (isBlocked(j)) {
    const cos = coBlockers(j);
    if (onlyCoBlockers(j)) add("clear", blockerButton(j), `data-action="open-co" data-job="${j.id}" data-co="${cos[0].co_id}"`, "Send waits until the customer signs it.", "bill");
    else add("clear", blockerButton(j), `data-action="open-packet" data-job="${j.id}"${draft ? ` data-inv="${draft.id}"` : ""}`, "Open the paperwork and add what is missing.", "bill");
  }
  // Every send passes "Before you send" (R1-06): the job page opens it instead of sending.
  if (draft) add("send", "Review & send", `data-action="open-payapp" data-job="${j.id}" data-inv="${draft.id}" data-focus=".send-check"`, "See what goes out and to whom, then send.", "bill");
  if (j.waiver_due && !j.waiver_sent) add("waiver", "Choose waiver and send", `data-action="open-waiver" data-id="${j.id}"`, "Pick the waiver type and send the newest signed waiver on the job record.", "paid");
  if (waitingPay) add("match", "Record a payment", `data-action="toggle-form" data-form="match"`, "Money came in. Paid in full opens the waiver step.", "paid");
  if (follow) add("follow", followDue ? "Send follow-up" : "Follow up now", `data-action="follow-up" data-id="${j.id}"${followDue ? "" : ` data-early="1"`}`,
    followDue ? "Email the customer a reminder." : `Before the follow-up date, ${fmtDate(follow.due)}.`, "paid");
  // A phone call counts as a follow-up (R4-06): what they said, and when to check again.
  if (follow) add("contact", "Spoke to them…", `data-action="toggle-form" data-form="contact"`, "Note what they said and when to check again.", "paid");
  if (j.work_status === "complete" && !draft && m.unbilled_completed_cents > 0) add("draft", "Draft pay app", `data-action="draft" data-id="${j.id}"`, "A pay app for the work done and not billed.", "bill");
  if (j.work_status !== "complete") add("progress", "Progress pay app", `data-action="toggle-form" data-form="progress"`, "Bill the work done so far without marking the job done.", "bill");
  if (j.work_status !== "complete") add("done", "Mark done", `data-action="toggle-form" data-form="done"`, "Record that the work is finished and draft the final pay app.", "close");
  if (canClose) add("close", "Finish job", `data-action="close" data-id="${j.id}"`, "Moves the settled job to Job history.", "close");
  // Mark done by mistake (R4-02): while the final pay app has not gone out.
  if (j.can_undo_done) add("undo", "Undo done", `data-action="undo-done" data-id="${j.id}"`, "Back to in progress. The final draft is deleted.", "close");
  // Asking for retainage only once the work is done and the closeout paperwork is in; until then
  // the Money card says why it isn't here.
  if (m.retainage_open_cents > 0 && j.work_status === "complete" && closeoutMissing(j) === 0) add("retainage", "Ask for retainage", `data-action="retainage" data-id="${j.id}"`, "Email the customer for the retainage held back.", "paid");
  if (draft && !pencilOut) add("pencil", "Send draft for their review", `data-action="pencil" data-id="${j.id}"`, "The customer checks the numbers before the real one goes.", "bill");
  if (pencilOut) add("pencil-ok", "Customer agreed to the draft", `data-action="pencil-agreed" data-id="${j.id}"`, "Record that the customer agreed to the draft.", "bill");
  if (pencilOut) add("pencil-no", "Customer wants changes", `data-action="toggle-form" data-form="pencilchanges"`, "Record what the customer wants changed.", "bill");
  if (m.retainage_open_cents > 0) add("retpay", "Record retainage paid", `data-action="toggle-form" data-form="retpay"`, "Record retainage the customer released.", "paid");
  add("co", "New change order", `data-action="new-co"`, "Extra or changed work, or a credit.", "bill");
  if (draft) add("packet", "Open paperwork", `data-action="open-packet" data-job="${j.id}" data-inv="${draft.id}"`, "Add the documents that go with the pay app.", "bill");
  if (draft) add("void", "Delete draft", `data-action="void-draft" data-id="${j.id}"`, "Throw away the open draft. You can draft it again.", "bill");
  if (demo) add("qbo", "Import QBO sample", `data-action="import-qbo" data-id="${j.id}"`, "Sample company only: a stand-in for a QuickBooks payment read.", "paid");
  const order = ["clear", "send", "waiver"];
  // While the customer has the pay app, nothing is the main step until a follow-up is due, unless
  // finished work is still to bill.
  if (waitingPay) order.push(...(followDue ? ["follow"] : []), "draft");
  else order.push("draft", "progress", "close", "retainage");
  const primary = order.map(k => all.find(a => a.key === k)).find(Boolean) || null;
  const more = all.filter(a => a !== primary).sort((a, b) => SECONDARY_ORDER.indexOf(a.key) - SECONDARY_ORDER.indexOf(b.key));
  return { primary, more };
}

export const ACTION_GROUPS = [["bill", "Bill"], ["paid", "Get paid"], ["close", "Close out"]];

// The buttons beside one thing that stands between the job and Finish job (R3-08), from the
// server's close_blocker_items ({kind, text, invoice_id, co_id, number}): [{label, attrs}].
export function closeBlockerActions(j, b) {
  const id = j.id;
  switch (b.kind) {
    case "not_done": return [{ label: "Mark done", attrs: `data-action="toggle-form" data-form="done"` }];
    case "draft": return [
      { label: "Review & send", attrs: `data-action="open-payapp" data-job="${id}" data-inv="${b.invoice_id}" data-focus=".send-check"` },
      { label: "Delete draft", attrs: `data-action="void-draft" data-id="${id}"` }];
    case "co": return [
      { label: `Get CO ${b.number} signed`, attrs: `data-action="open-co" data-job="${id}" data-co="${b.co_id}"` },
      { label: "Withdraw", attrs: `data-action="withdraw-co" data-id="${id}" data-co="${b.co_id}"` }];
    case "unbilled": return [{ label: "Draft pay app", attrs: `data-action="draft" data-id="${id}"` }];
    case "outstanding": return [{ label: "Record a payment", attrs: `data-action="toggle-form" data-form="match"` },
      ...shortPayable(j).map(i => ({ label: shortPayLabel(i), attrs: shortPayAttrs(j, i) }))];
    case "retainage": return [
      { label: "Record retainage paid", attrs: `data-action="toggle-form" data-form="retpay"` },
      closeoutMissing(j)
        ? { label: "Open closeout paperwork", attrs: `data-action="open-packet" data-job="${id}" data-inv="closeout"` }
        : { label: "Ask for retainage", attrs: `data-action="retainage" data-id="${id}"` }];
    case "waiver": return [{ label: "Send waiver", attrs: `data-action="open-waiver" data-id="${id}"` }];
    default: return [];
  }
}

// Part-paid pay apps the customer may not pay in full (R4-01): accepting the short pay settles them.
export function shortPayable(j) {
  return (j.invoices || []).filter(i => i.status === "partial" && i.remaining_cents > 0);
}

export function shortPayLabel(inv) {
  return `Accept ${money(inv.remaining_cents)} short pay…`;
}

export function shortPayAttrs(j, inv) {
  return `data-action="short-pay-open" data-job="${j.id}" data-inv="${inv.id}"`;
}

// A sent pay app can be pulled back (R4-03) while nothing is paid on it and nothing came after it.
export function canPullBack(j, inv) {
  if (j.closed || !["sent", "approved"].includes(inv.status)) return false;
  if ((j.payments || []).some(p => p.invoice_id === inv.id)) return false;
  const live = (j.invoices || []).filter(i => i.status !== "void");
  return live[live.length - 1] === inv;
}
