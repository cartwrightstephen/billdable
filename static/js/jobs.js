// Where a job stands: blocked or not, the one next step, status pills, change-order signatures and waiver kinds.

import { unesc, fmtDate, fmtDateTime, toDate, centsFrom, percentFrom, money, words } from "./format.js";
import { termText } from "./terms.js";

export const RECORD_KINDS = {
  note: "Note", daily: "Daily record", photo: "Photo", contract: "Contract", change_order: "Change order",
  waiver: "Lien waiver", pay_app: "Backup for the pay app", ticket: "Delivery or time ticket", other: "Other",
};

// domain.SOURCE_LABELS has the same words, for Today's rows.
export const SOURCE_LABELS = { upload: "Upload", paste: "Pasted", email: "Email", sms: "Text message", notes: "Notes", payer_page: "Customer page" };

// One short word for where a job stands; the full sentence (status_label) goes on the line below it.
export function jobStatus(j) {
  const inv = j.invoices || [];
  if (j.closed) return ["Closed", "green"];
  if (isBlocked(j)) return needsPaperwork(j) ? ["Needs paperwork", "amber"] : ["Blocked", "red"];
  if (inv.some(i => ["draft", "awaiting_approval"].includes(i.status))) return ["Ready to send", "copper"];
  if (j.waiver_due && !j.waiver_sent) return ["Waiver due", "amber"];
  if (inv.some(i => i.status === "partial")) return ["Partly paid", "amber"];
  if (inv.some(i => i.status === "sent")) return ["Sent", "amber"];
  if (inv.some(i => i.status === "approved")) return ["Approved", "green"];
  if (j.work_status !== "complete") return ["In progress", ""];
  if (j.money.unbilled_completed_cents > 0) return ["Ready to bill", "copper"];
  if (j.money.retainage_open_cents > 0) return ["Held back", "amber"];
  if (inv.length && j.money.outstanding_cents === 0) return ["Paid", "green"];
  return ["Done", ""];
}

export function statusPill(j) { const [w, c] = jobStatus(j); return `<span class="pill ${c}">${w}</span>`; }

// Blocked (blockers count once there is a draft to block or the work is done), and blocked only by
// paperwork to attach (amber, not red; red stays for an unsigned change order on the bill): the
// server decides both and sends them on each job (domain.is_blocked / needs_paperwork as blocked /
// needs_paperwork, R4-10), the same rules Today's rows and the daily email use.
export function isBlocked(j) { return !!j.blocked; }

export function needsPaperwork(j) { return !!j.needs_paperwork; }

// The unsigned change orders on the bill that block send: {kind: "co", text, co_id, number} (R3-05).
export function coBlockers(j) {
  return (j.blocker_items || []).filter(b => b.kind === "co");
}

// Blocked only by unsigned change orders: the way out is a signature, not paperwork.
export function onlyCoBlockers(j) {
  const cos = coBlockers(j);
  return isBlocked(j) && cos.length > 0 && cos.length === j.blockers.length;
}

// The button that opens the paperwork page, or the change order when that is all that blocks:
// its words come from the server (domain.blocker_button), the same as on Today's row.
export function blockerButton(j) { return j.blocker_button || ""; }

// Kept here rather than on the server: jobStatus (the pill's short word; the server's status_label
// is the sentence under it), nextStep, followUpDue and jobActions. They build on the server's
// blocked / needs_paperwork above, and followUpDue and jobActions run on the viewer's clock (a
// follow-up before its date is "Follow up now", the waiting line counts to the date). Today's
// queue no longer uses any of them.

// In the company's word for a pay app (terms.js): "Draft a draw for..." for draws (R6-01).
export function nextStep(j) { return termText(nextStepWords(j)); }

function nextStepWords(j) {
  if (j.closed) return "Finished.";
  if (needsPaperwork(j)) return "Attach the paperwork, then send: " + j.missing_paperwork.join(", ");
  if (isBlocked(j)) return "Before it can go: " + j.blockers[0];
  const draft = j.invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
  if (draft) return "Pay app is ready. Review it, then Approve & send.";
  if (j.waiver_due && !j.waiver_sent) return "Payment received. Approve and send the waiver.";
  if (j.invoices.some(i => ["sent", "approved", "partial"].includes(i.status))) return "Waiting on the customer. Follow up or record a payment.";
  if (j.work_status !== "complete") return "Record progress, or mark the job done." + (j.blockers && j.blockers.length ? " The next pay app needs: " + j.blockers.join("; ") : "");
  if (j.money.retainage_open_cents > 0) return "Ask for the money held back once the closeout paperwork is in.";
  if (j.money.outstanding_cents === 0 && j.money.unbilled_completed_cents === 0) return (j.close_blockers || []).length ? "Before you finish: " + j.close_blockers[0] : "Ready to finish the job.";
  return "Draft a pay app for the work done and not billed.";
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

// A change order's pill (the glossary): Signed or Unsigned; "Approved" is kept for the customer's approval of a pay app.
export const CO_WORDS = { pending: "Unsigned", approved: "Signed", withdrawn: "Withdrawn" };

export function coStatusWords(status) { return CO_WORDS[unesc(status || "")] || words(status); }

// One line on when each type fits (R6-10), under the type picker and on Closeout.
export const WAIVER_HINTS = {
  conditional_progress: "Before the money arrives: it counts only once their payment clears.",
  unconditional_progress: "You already have the money for this pay app.",
  conditional_final: "For the last payment, before it arrives: it counts once the payment clears.",
  unconditional_final: "You have the last payment, and nothing more is owed on the job.",
};

export function waiverHint(code) { return termText(WAIVER_HINTS[unesc(code)] || ""); }

// The Type column on Closeout: the type of the waiver sent or chosen, else "Not chosen yet".
export function waiverTypeText(j, paid) {
  const kind = (paid && paid.waiver_kind) || j.waiver_kind;
  return kind ? waiverKindLabel(kind) : "Not chosen yet";
}

// disabled: sending is off (email not confirmed, or no email for the customer); the page says why.
export function waiverSendForm(j, inv, disabled = false) {
  const key = inv ? inv.id : "job";
  const final = (inv && inv.final) || (j.work_status === "complete" && j.money.unbilled_completed_cents === 0 && j.money.outstanding_cents === 0);
  const pick = final ? "unconditional_final" : "unconditional_progress";
  return `<div class="form-inline">
    <select id="waiver-kind-${key}" aria-label="Waiver type">${Object.entries(WAIVER_KINDS).map(([k, v]) => `<option value="${k}" ${k === pick ? "selected" : ""}>${v}</option>`).join("")}</select>
    <button class="btn small" data-action="waiver" data-id="${j.id}"${inv ? ` data-inv="${inv.id}"` : ""}${disabled ? " disabled" : ""}>Approve & send</button>
  </div>
  <p class="sub waiver-hint" data-waiver-hint="${key}">${waiverHint(pick)}</p>`;
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

// A date the server keeps as a day ("2026-11-05") counts from midnight UTC, as domain.follow_up_due
// reads it; the day people see is that same calendar day.
const utcMidnight = (iso) => { const d = new Date(unesc(iso) + "T00:00:00Z"); return isNaN(d) ? null : d; };
const sameDayLocal = (d) => new Date(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());

// When the next follow-up may go (domain.follow_up_due): the customer's days between follow-ups
// after the last one on the oldest unpaid pay app, or after it was sent; or the date noted after a
// call ("Spoke to them", R4-06) until a follow-up goes out. invoices: every unpaid pay app, which
// one follow-up names. due is the moment it falls due (the server's clock); day is the date to
// show for it. Null with nothing to chase.
export function followUpDue(j) {
  const unpaid = (j.invoices || []).filter(i => OPEN_FOR_PAYMENT.includes(i.status));
  const sent = unpaid[0];
  if (!sent) return null;
  const lastIso = unesc(sent.last_follow_up_at || sent.sent_at || "");
  const last = toDate(lastIso);
  if (!last) return null;
  const noted = j.expected_payment_on && unesc(j.contact_noted_at || "") >= lastIso ? utcMidnight(j.expected_payment_on) : null;
  if (noted) return { invoice: sent, invoices: unpaid, due: noted, day: sameDayLocal(noted), noted: true };
  const days = Number(unesc(String((j.payer || {}).follow_up_days || ""))) || 7;
  let due = new Date(last.getTime() + days * 86400000);
  let day = due;
  // Approved on the customer's page with a date to expect payment (R4-08): not before the day after.
  const promised = expectedPayment(sent);
  if (promised && promised.getTime() + 86400000 > due.getTime()) {
    due = new Date(promised.getTime() + 86400000);
    day = sameDayLocal(due);
  }
  return { invoice: sent, invoices: unpaid, due, day, promised };
}

// The date the customer said payment would come when they approved the pay app (R4-08), or null.
export function expectedPayment(inv) {
  if (!inv || inv.status !== "approved" || !inv.expected_payment_on) return null;
  return utcMidnight(inv.expected_payment_on);
}

// "PA-01" or "PA-01 and PA-02" or "PA-01, PA-02 and PA-03": the pay apps one follow-up names.
export function payAppList(invoices) {
  const n = invoices.map(i => i.number);
  return n.length > 1 ? `${n.slice(0, -1).join(", ")} and ${n[n.length - 1]}` : (n[0] || "the pay app");
}

// Required closeout items not in yet, which gate asking for retainage: the server sends them
// (domain.closeout_missing, the rule chase_retainage uses), so older jobs without a closeout
// packet count their job paperwork the same way here.
export function closeoutMissing(j) {
  return (j.closeout_missing || []).length;
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
  const when = f.due <= now ? `a follow-up has been due since ${fmtDate(f.day)}` : `follow-up due ${fmtDate(f.day)}`;
  const inv = f.invoice;
  if (f.promised && f.due > now) return `Approved by ${inv.approved_by_payer || p.approver || p.name || "the customer"} ${fmtDate(inv.approved_at)}; expect payment ${fmtDate(inv.expected_payment_on)}.`;
  if (f.noted && f.due > now) return `Waiting on ${p.approver || p.name || "the customer"}. Spoke to them ${fmtDate(j.contact_noted_at)}; check again ${fmtDate(f.day)}.`;
  return `Waiting on ${p.approver || p.name || "the customer"}. Sent ${fmtDate(f.invoice.sent_at)}; ${when}.`;
}

// The job page's buttons (R3-04): one main button (or none while the customer has the pay app and
// no follow-up is due), then the rest in the order they matter. Each has a group for the "More"
// list: bill, paid (get paid) or close (close out). demo adds the QuickBooks sample import.
// Job actions only an owner may take (server.OWNER_ACTIONS, R4-09): "You approve every send".
// The company setting members_can_send lets members send pay apps, waivers and retainage requests.
export const OWNER_ONLY = ["send", "waiver", "retainage", "void-draft", "void-sent", "close", "delete", "accept-short-pay", "reopen"];
const MEMBER_SEND = ["send", "waiver", "retainage"];

// perms: {owner, membersCanSend}. Without perms (tests, older callers) everything is allowed.
export function mayTake(action, perms) {
  if (!perms || perms.owner || !OWNER_ONLY.includes(action)) return true;
  return MEMBER_SEND.includes(action) && !!perms.membersCanSend;
}

// The job-page button keys that are owner-only actions.
const KEY_ACTION = { send: "send", waiver: "waiver", retainage: "retainage", void: "void-draft", close: "close" };

const SECONDARY_ORDER = ["clear", "send", "waiver", "match", "follow", "contact", "pencil", "pencil-ok", "pencil-no", "packet",
  "draft", "progress", "done", "retainage", "retpay", "co", "close", "undo", "void", "qbo"];

export function jobActions(j, { now = new Date(), demo = false, perms = null } = {}) {
  if (j.closed) return { primary: null, more: [] };
  const m = j.money;
  const draft = (j.invoices || []).find(i => DRAFTS.includes(i.status));
  const pencilOut = draft && draft.pencil_status === "sent";
  const waitingPay = (j.invoices || []).some(i => ["sent", "approved", "partial"].includes(i.status));
  const follow = waitingPay ? followUpDue(j) : null;
  const followDue = !!follow && follow.due <= now;
  const canClose = !(j.close_blockers || []).length;
  const all = [];
  // Labels and reasons in the company's word for a pay app (terms.js, R6-01).
  const add = (key, label, attrs, why, group) => { if (mayTake(KEY_ACTION[key], perms)) all.push({ key, label: termText(label), attrs, why: termText(why), group }); };
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
    followDue ? "Email the customer a reminder." : `Before the follow-up date, ${fmtDate(follow.day)}.`, "paid");
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
  if (demo) add("qbo", "Import QuickBooks sample", `data-action="import-qbo" data-id="${j.id}"`, "Sample company only: a stand-in for a QuickBooks payment read.", "paid");
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
// perms as for mayTake: a member doesn't see what only an owner may do.
export function closeBlockerActions(j, b, perms = null) {
  return closeBlockerButtons(j, b).filter(a => mayTake(a.needs, perms)).map(a => ({ ...a, label: termText(a.label) }));
}

function closeBlockerButtons(j, b) {
  const id = j.id;
  switch (b.kind) {
    case "not_done": return [{ label: "Mark done", attrs: `data-action="toggle-form" data-form="done"` }];
    case "draft": return [
      { label: "Review & send", attrs: `data-action="open-payapp" data-job="${id}" data-inv="${b.invoice_id}" data-focus=".send-check"`, needs: "send" },
      { label: "Delete draft", attrs: `data-action="void-draft" data-id="${id}"`, needs: "void-draft" }];
    case "co": return [
      { label: `Get CO ${b.number} signed`, attrs: `data-action="open-co" data-job="${id}" data-co="${b.co_id}"` },
      { label: "Withdraw", attrs: `data-action="withdraw-co" data-id="${id}" data-co="${b.co_id}"` }];
    case "unbilled": return [{ label: "Draft pay app", attrs: `data-action="draft" data-id="${id}"` }];
    case "outstanding": return [{ label: "Record a payment", attrs: `data-action="toggle-form" data-form="match"` },
      ...shortPayable(j).map(i => ({ label: shortPayLabel(i), attrs: shortPayAttrs(j, i), needs: "accept-short-pay" }))];
    case "retainage": return [
      { label: "Record retainage paid", attrs: `data-action="toggle-form" data-form="retpay"` },
      closeoutMissing(j)
        // An older job without a closeout packet keeps those items in the job paperwork (as on Closeout).
        ? { label: "Open closeout paperwork", attrs: `data-action="open-packet" data-job="${id}"${(j.closeout_packet || []).length ? ` data-inv="closeout"` : ""}` }
        : { label: "Ask for retainage", attrs: `data-action="retainage" data-id="${id}"`, needs: "retainage" }];
    case "waiver": return [{ label: "Send waiver", attrs: `data-action="open-waiver" data-id="${id}"`, needs: "waiver" }];
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
