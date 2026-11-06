// Where a job stands: blocked or not, the one next step, status pills, change-order signatures and waiver kinds.

import { unesc, fmtDate, fmtDateTime, toDate, centsFrom, percentFrom, money, words } from "./format.js";
import { termText } from "./terms.js";

// The job record's kinds, as domain.RECORD_KINDS and importer.KINDS have them.
export const RECORD_KINDS = {
  note: "Note", conversation: "Conversation", daily: "Diary entry", photo: "Photo", contract: "Contract", change_order: "Change order",
  waiver: "Lien waiver", pay_app: "Backup for the pay app", ticket: "Delivery ticket or time sheet",
  supplier_invoice: "Supplier invoice or receipt", insurance: "Insurance certificate", inspection: "Inspection",
  stored: "Stored materials document", payroll: "Certified payroll", notarized: "Notarized pay application", punch: "Punch list sign-off", completion: "Completion certificate",
  asbuilt: "As-builts", warranty: "Warranty", other: "Other",
};

// What an Inbox item can be filed as: the record kinds, with a ticket offered as the two it can
// be (importer.file_item splits "ticket:delivery" into its kind and ticket_type).
export const FILE_KINDS = Object.entries(RECORD_KINDS).flatMap(([k, v]) => k === "ticket"
  ? [["ticket:delivery", "Delivery ticket"], ["ticket:time_sheet", "Time sheet"]] : [[k, v]]);

// The filing choice an Inbox guess starts on: a ticket is a time sheet if it says so.
export function fileKindPick(kind, text = "") {
  if (kind !== "ticket") return RECORD_KINDS[kind] ? kind : "note";
  return /\btime ?sheet\b/i.test(unesc(text || "")) ? "ticket:time_sheet" : "ticket:delivery";
}

// A ticket says which it is, when the New job record form made it (record.ticket_type); one from
// before, or filed from the Inbox, is just a ticket. The paperwork rules read the kind alone.
export const TICKET_TYPES = { delivery: "Delivery ticket", time_sheet: "Time sheet" };
export function recordKindWord(r) {
  const kind = unesc(r.kind || "");
  return (kind === "ticket" && TICKET_TYPES[unesc(r.ticket_type || "")]) || RECORD_KINDS[kind] || r.kind;
}

// domain.SOURCE_LABELS has the same words, for Today's rows.
export const SOURCE_LABELS = { upload: "Upload", paste: "Pasted", email: "Email", sms: "Text message", notes: "Notes", payer_page: "Client page" };

// One short word for where a job stands; the full sentence (status_label) goes on the line below it.
export function jobStatus(j) {
  const inv = j.invoices || [];
  if (j.closed) return ["Closed", "green"];
  if (isBlocked(j)) return needsPaperwork(j) ? ["Needs supporting documents", "amber"] : ["Blocked", "red"];
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
// follow-up before its date is "Follow Up Now", the waiting line counts to the date). Today's
// queue no longer uses any of them.

// In the company's word for a pay app (terms.js): "Draft a draw for..." for draws (R6-01).
// listed: the job page shows every blocker under the line (J6), so the line doesn't name one.
export function nextStep(j, now = new Date(), { listed = false } = {}) { return termText(nextStepWords(j, now, listed)); }

// The line names the main button jobActions puts under it, in the same words (Nov 3 review).
function nextStepWords(j, now, listed = false) {
  if (j.closed) return "Finished.";
  const m = j.money;
  const draft = j.invoices.find(i => DRAFTS.includes(i.status));
  const number = draft && draft.number || "The draft";
  const client = (j.payer || {}).approver || (j.payer || {}).name || "the client";
  if (draft && draft.pencil_status === "sent") return `${number} is with ${client} for their review${draft.pencil_sent_at ? ` since ${fmtDate(draft.pencil_sent_at)}` : ""}. When they answer, tap Client agreed to the draft or Client wants changes.`;
  if (isBlocked(j)) {
    const button = blockerButton(j) || "Fix this";
    const lead = `${button}${button.endsWith("before sending") ? ":" : " before it can go:"}`;
    // A file that only needs a look says so; the line above the buttons names the file.
    const items = j.blocker_items || [];
    if (!listed && items.length && items.every(b => b.waiting)) return items.map(b => b.text).join(" ");
    const first = needsPaperwork(j) ? (j.missing_paperwork || []).join(", ") : j.blockers[0];
    return `${lead}${listed ? "" : ` ${first}`}`;
  }
  if (draft) {
    // domain.today_rows has the same sentence for the Dashboard's row.
    const changes = draft.pencil_status === "corrections";
    const said = draft.pencil_status === "agreed" ? "The client agreed to it. " : changes ? `The client wants changes${draft.pencil_note ? `: ${draft.pencil_note}` : ""}. Fix it, then ` : "";
    const email = unesc((j.payer || {}).ap_email || "").trim() ? "" : `${changes ? "add" : "Add"} the pay app email for ${(j.payer || {}).name || "the client"}, then `;
    return `${number}${draft.amount_cents != null ? ` for ${money(draft.amount_cents)}` : ""} to ${client} is ready. ${said}${email}Review & send.`;
  }
  if (j.waiver_due && !j.waiver_sent) return "Payment received. Choose waiver and send.";
  const notBilled = j.work_status === "complete" && m.unbilled_completed_cents > 0 ? `${money(m.unbilled_completed_cents)} of finished work isn't billed. Draft pay app.` : "";
  if (j.invoices.some(i => OPEN_FOR_PAYMENT.includes(i.status))) {
    const follow = followUpDue(j);
    const waiting = waitingText(j, now) || "Waiting on the client.";
    if (follow && follow.due <= now) return `${waiting} Send follow-up.`;
    return notBilled || `${waiting} Record a payment when it comes in.`;
  }
  if (j.work_status !== "complete") return "Bill the work so far with Progress pay app, or use Final pay app once it is finished." + (j.blockers && j.blockers.length ? " The next pay app needs: " + j.blockers.join("; ") : "");
  if (notBilled) return notBilled;
  // The closeout documents come first: domain.chase_retainage refuses before it looks at the wait.
  if (closeoutMissing(j)) return `Closeout: missing ${j.closeout_missing.join(", ")}. Open closeout documents.`;
  if (m.retainage_open_cents > 0) {
    const asked = retainageAskWaits(j, now);
    if (asked) return `Asked for the ${money(m.retainage_open_cents)} held back ${fmtDate(asked.last)}. Ask again from ${fmtDate(asked.due)}, or Record retainage paid.`;
    return `${money(m.retainage_open_cents)} held back. Closeout documents are in. Ask for retainage.`;
  }
  if ((j.close_blockers || []).length) return "Before you finish: " + j.close_blockers[0];
  return "Ready to finish the job. Finish job.";
}

// What Final Pay App is about to do, for its confirm box, from the server's
// domain.final_pay_app_preview. A pay app that wouldn't be the final one says so, and its button
// doesn't use the label.
export function finalPayAppConfirm(j, p) {
  const name = unesc(j.name || "the job");
  const finishes = j.sov_view && j.sov_view.present
    ? "It marks the work finished and sets every schedule line to 100%."
    : "It marks the work finished and bills the rest of the contract.";
  if (!p.drafted) return { text: `There is nothing left to bill on ${name}. ${finishes}`, button: "Mark work finished" };
  const amount = `${money(p.amount_cents)}${p.retainage_cents ? ` (${money(p.gross_cents)} less ${money(p.retainage_cents)} retainage)` : ""}`;
  const replaces = p.replaces ? ` It replaces draft ${unesc(p.replaces)}.` : "";
  if (p.final) return { text: `${termText("Final pay app")} for ${name}: ${amount}. ${finishes}${replaces}`, button: termText("Final pay app") };
  const cos = (p.pending_cos || []).map(c => `CO ${c.number} (${money(c.amount_cents)})`).join(", ");
  return {
    text: `${termText("This won't be the final pay app")} for ${name}${cos ? `: ${cos} isn't signed or on the bill` : ""}. ${termText("Pay app")} ${unesc(p.number)}: ${amount}. ${finishes}${replaces}`,
    button: termText("Draft pay app"),
  };
}

export function coSignature(co) {
  if (co.signature) return `<div class="money-row"><span class="label">Signed</span><span>${co.signature.name}, ${fmtDateTime(co.signature.at)} (online)</span></div>
    <p class="sub">Kept with it: the consent they agreed to, their network address and browser, and a fingerprint of what they saw (${String(co.signature.snapshot_sha256).slice(0, 12)}…).</p>`;
  // On paper (M9): who signed it and the date they signed, when recorded; then the copy on the job record.
  const ev = co.signed_evidence;
  if (ev) return `<div class="money-row"><span class="label">Signed</span><span>${ev.signer_name ? `${ev.signer_name}, ${fmtDate(ev.signed_on || ev.at)} (on paper)` : "On paper"}</span></div>
    <p class="sub">Paper copy in the job records: ${ev.label} (recorded by ${ev.by}, ${fmtDate(ev.at)}).</p>`;
  return "";
}

export const WAIVER_KINDS = {
  conditional_progress: "Conditional, progress payment",
  unconditional_progress: "Unconditional, progress payment",
  conditional_final: "Conditional, final payment",
  unconditional_final: "Unconditional, final payment",
};

export function waiverKindLabel(code) { return WAIVER_KINDS[unesc(code)] || code; }

// A change order's pill (the glossary): Signed or Unsigned; "Approved" is kept for the client's approval of a pay app.
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

// The type a waiver is sent as (M17): the server's suggestion for the pay app or the retainage
// release (domain.suggested_waiver_kind, never final while retainage is held), else worked out
// the same way for an older job-level waiver.
export function waiverPick(j, inv = null, release = null) {
  if (release) return unesc(release.kind || "") || "unconditional_progress";
  const said = inv && inv.waiver_stages && inv.waiver_stages.unconditional && unesc(inv.waiver_stages.unconditional.kind || "");
  if (said) return said;
  const final = j.work_status === "complete" && j.money.unbilled_completed_cents === 0 && j.money.outstanding_cents === 0
    && !(j.money.draft_cents > 0) && !(j.money.retainage_open_cents > 0) && !(j.change_orders || []).some(co => co.status === "pending");
  return final ? "unconditional_final" : "unconditional_progress";
}

// disabled: sending is off (email not confirmed, or no email for the client); the page says why.
// release: a retainage release's waiver (M20) instead of a pay app's.
export function waiverSendForm(j, inv, disabled = false, release = null) {
  const key = release ? release.id : inv ? inv.id : "job";
  const pick = waiverPick(j, inv, release);
  return `<div class="form-inline">
    <select id="waiver-kind-${key}" aria-label="Waiver type">${Object.entries(WAIVER_KINDS).map(([k, v]) => `<option value="${k}" ${k === pick ? "selected" : ""}>${v}</option>`).join("")}</select>
    <button class="btn small" data-action="waiver" data-id="${j.id}"${inv ? ` data-inv="${inv.id}"` : ""}${release ? ` data-release="${release.id}"` : ""}${disabled ? " disabled" : ""}>Approve & send</button>
  </div>
  <p class="sub waiver-hint" data-waiver-hint="${key}">${waiverHint(pick)}</p>`;
}

// The type in a sentence, as domain.WAIVER_TYPE_WORDS: "the signed unconditional final waiver".
export const WAIVER_TYPE_WORDS = { conditional_progress: "conditional", unconditional_progress: "unconditional",
  conditional_final: "conditional final", unconditional_final: "unconditional final" };
export function waiverTypeWords(code) { return WAIVER_TYPE_WORDS[unesc(code || "")] || ""; }

// One waiver stage of a pay app (M17), as the server's waiver_stages has it: "Conditional,
// progress payment: Attached", or the status alone when the type isn't known.
export function waiverStageText(stage) {
  if (!stage || !stage.status) return "—";
  // Not needed says why (B2); a waiver on file names the record that would go (B1).
  const status = stage.reason ? `${stage.status}: ${stage.reason}` : stage.record_label ? `${stage.status} (${stage.record_label})` : stage.status;
  return stage.kind ? `${waiverKindLabel(stage.kind)}: ${status}` : status;
}

// Whole days from an ISO moment to now, for "Done 12 days ago" (M26). Null without a date.
export function daysSince(iso, now = new Date()) {
  const d = toDate(unesc(iso || ""));
  return d ? Math.max(0, Math.floor((now - d) / 86400000)) : null;
}

export function daysAgoText(n) {
  return n == null ? "" : n === 0 ? "today" : n === 1 ? "1 day ago" : `${n} days ago`;
}

// The figures of a job's Retainage card and Closeout's retainage table (M23, M25, M26): the pay
// apps that held some (not drafts or cancelled ones), times asked, last asked, next allowed and
// days since the job was done and since the last ask.
export function retainageFacts(j, now = new Date()) {
  const m = j.money;
  const payApps = (j.invoices || []).filter(i => (i.retainage_cents || 0) > 0 && !["void", "draft", "awaiting_approval"].includes(i.status));
  const due = retainageAskDue(j);
  return {
    pct: Number(j.retainage_pct) || 0, held: m.retainage_held_cents, released: m.retainage_released_cents, open: m.retainage_open_cents,
    payApps, asked: Number(j.retainage_chases) || 0, last: due ? due.last : null, next: due ? due.due : null,
    doneDays: j.work_status === "complete" ? daysSince(j.completed_at, now) : null,
    askedDays: due ? daysSince(j.last_retainage_chase_at, now) : null,
    releases: j.retainage_payments || [],
  };
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

// When the next follow-up may go (domain.follow_up_due): the client's days between follow-ups
// after the last one on the oldest unpaid pay app, or after it was sent; or the date noted after a
// conversation (R4-06) until a follow-up goes out. invoices: every unpaid pay app, which
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
  // Approved on the client's page with a date to expect payment (R4-08): not before the day after.
  const promised = expectedPayment(sent);
  if (promised && promised.getTime() + 86400000 > due.getTime()) {
    due = new Date(promised.getTime() + 86400000);
    day = sameDayLocal(due);
  }
  return { invoice: sent, invoices: unpaid, due, day, promised };
}

// The date the client said payment would come when they approved the pay app (R4-08), or null.
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

// When the next retainage request may go (domain.chase_retainage): the client's days between
// contacts after the last request, whole or part. last and due are moments (the server's clock).
// Null when none has gone out.
export function retainageAskDue(j) {
  const last = toDate(unesc(j.last_retainage_chase_at || ""));
  if (!last) return null;
  const days = Number(unesc(String((j.payer || {}).follow_up_days || ""))) || 7;
  return { last, due: new Date(last.getTime() + days * 86400000) };
}

// The last request, while the server would still refuse the next one; else null.
export function retainageAskWaits(j, now = new Date()) {
  const r = retainageAskDue(j);
  return r && now < r.due ? r : null;
}

// Why the job page has no "Ask for retainage" while retainage is held (R3-04). Empty when it has one.
export function retainageNote(j, now = new Date()) {
  if (!(j.money.retainage_open_cents > 0) || j.closed) return "";
  const asked = retainageAskWaits(j, now);
  if (j.work_status !== "complete") return termText("Asked for after Final pay app.");
  const n = closeoutMissing(j);
  if (n) return `Closeout documents: ${n} item${n === 1 ? "" : "s"} missing.`;
  return asked ? `Asked for it ${fmtDate(asked.last)}; ask again from ${fmtDate(asked.due)}.` : "";
}

// "Waiting on Sam. Sent Oct 3; follow-up due Oct 10." while a sent pay app is unpaid. Empty otherwise.
// Once the date has passed: "...; a follow-up has been due since Oct 10."
export function waitingText(j, now = new Date()) {
  const f = followUpDue(j);
  if (!f) return "";
  const p = j.payer || {};
  const when = f.due <= now ? `a follow-up has been due since ${fmtDate(f.day)}` : `follow-up due ${fmtDate(f.day)}`;
  const inv = f.invoice;
  if (f.promised && f.due > now) return `Approved by ${inv.approved_by_payer || p.approver || p.name || "the client"} ${fmtDate(inv.approved_at)}; expect payment ${fmtDate(inv.expected_payment_on)}.`;
  if (f.noted && f.due > now) return `Waiting on ${p.approver || p.name || "the client"}. Conversation ${fmtDate(j.contact_noted_at)}; check again ${fmtDate(f.day)}.`;
  return `Waiting on ${p.approver || p.name || "the client"}. Sent ${fmtDate(f.invoice.sent_at)}; ${when}.`;
}

// The job page's buttons (R3-04): one main button (or none while the client has the pay app and
// no follow-up is due), then the rest in the order they matter, all of them shown. Each has a
// group: bill, paid (get paid) or close (close out). demo adds the QuickBooks sample import.
// Who may take a job action (S6): the permission each needs, as domain/profiles.py
// JOB_ACTION_PERMS on the server, which refuses the same. An action not listed is anyone's.
export const ACTION_PERM = {
  send: "send_payapps", "unconditional-look": "send_payapps", waiver: "send_waivers", "waiver-not-needed": "send_waivers", retainage: "ask_retainage",
  "match-payment": "record_payments", "retainage-paid": "record_payments", "retainage-credit": "record_payments",
  "overbilling-settled": "record_payments",
  "accept-short-pay": "void", "void-draft": "void", "void-sent": "void", delete: "void",
  close: "close_jobs", reopen: "close_jobs",
};

// perms: {list: the signed-in person's permissions (me.perms)}; {owner: true} allows all.
// Without perms (tests, older callers) everything is allowed.
export function mayTake(action, perms) {
  const need = ACTION_PERM[action];
  if (!perms || !need) return true;
  if (Array.isArray(perms.list)) return perms.list.includes(need);
  return !!perms.owner;
}

// The job-page button keys that need a permission.
const KEY_ACTION = { send: "send", waiver: "waiver", retainage: "retainage", void: "void-draft", close: "close", match: "match-payment", retpay: "retainage-paid",
  draft: "draft", progress: "progress" };

const SECONDARY_ORDER = ["clear", "send", "waiver", "match", "follow", "conversation", "pencil", "pencil-ok", "pencil-no", "sentback", "packet", "closeout",
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
  // Labels in the company's word for a pay app (terms.js, R6-01). minor: a rare or destructive one,
  // drawn smaller. danger: drawn as one that throws something away.
  const add = (key, label, attrs, group, opts = {}) => { if (mayTake(KEY_ACTION[key], perms)) all.push({ key, label: termText(label), attrs, group, ...opts }); };
  if (isBlocked(j)) {
    const cos = coBlockers(j);
    if (onlyCoBlockers(j)) add("clear", blockerButton(j), `data-action="open-co" data-job="${j.id}" data-co="${cos[0].co_id}"`, "bill");
    else {
      const first = (j.blocker_items || []).find(b => b.item_id);
      add("clear", blockerButton(j), `data-action="open-packet" data-job="${j.id}"${draft ? ` data-inv="${draft.id}"` : ""}${first ? ` data-item="${first.item_id}"` : ""}`, "bill");
    }
  }
  // Every send passes "Before you send" (R1-06): the job page opens it instead of sending.
  if (draft) add("send", "Review & send", `data-action="open-payapp" data-job="${j.id}" data-inv="${draft.id}" data-focus=".send-check"`, "bill");
  if (j.waiver_due && !j.waiver_sent) add("waiver", "Choose waiver and send", `data-action="open-waiver" data-id="${j.id}"`, "paid");
  if (waitingPay) add("match", "Record a payment", `data-action="toggle-form" data-form="match"`, "paid");
  if (follow) add("follow", followDue ? "Send follow-up" : "Follow up now", `data-action="follow-up" data-id="${j.id}"${followDue ? "" : ` data-early="1"`}`, "paid");
  // A conversation is a record on any open job. While a pay app waits for payment it counts as a
  // follow-up, and its check-again date holds the next one (R4-06).
  add("conversation", "Log a conversation", `data-action="toggle-form" data-form="conversation"`, "paid");
  if (j.work_status === "complete" && !draft && m.unbilled_completed_cents > 0) add("draft", "Draft pay app", `data-action="draft" data-id="${j.id}"`, "bill");
  if (j.work_status !== "complete") add("progress", "Progress pay app", `data-action="toggle-form" data-form="progress"`, "bill");
  if (j.work_status === "complete" && closeoutMissing(j)) add("closeout", "Open closeout documents", closeoutAttrs(j), "close");
  if (j.work_status !== "complete") add("done", "Final pay app", `data-action="toggle-form" data-form="done"`, "close");
  if (canClose) add("close", "Finish job", `data-action="close" data-id="${j.id}"`, "close");
  // Final Pay App by mistake (R4-02): while the final pay app has not gone out.
  if (j.can_undo_done) add("undo", "Undo done", `data-action="undo-done" data-id="${j.id}"`, "close", { minor: true });
  // Asking for retainage only once the work is done and the closeout documents are in, and not again
  // before the client's days between contacts (chase_retainage); the Money card says why.
  if (m.retainage_open_cents > 0 && j.work_status === "complete" && closeoutMissing(j) === 0 && !retainageAskWaits(j, now)) add("retainage", "Ask for retainage", `data-action="retainage" data-id="${j.id}"`, "paid");
  if (draft && !pencilOut) add("pencil", "Send draft for their review", `data-action="pencil-review" data-job="${j.id}" data-inv="${draft.id}"`, "bill");
  if (pencilOut) add("pencil-ok", "Client agreed to the draft", `data-action="pencil-agreed" data-id="${j.id}"`, "bill");
  if (pencilOut) add("pencil-no", "Client wants changes", `data-action="toggle-form" data-form="pencilchanges"`, "bill");
  if (isBlocked(j)) add("sentback", "Client sent pay app back", `data-action="toggle-form" data-form="sentback"`, "bill");
  if (m.retainage_open_cents > 0) add("retpay", "Record retainage paid", `data-action="toggle-form" data-form="retpay"`, "paid");
  add("co", "New change order", `data-action="new-co"`, "bill");
  if (draft) add("packet", "Open supporting documents", `data-action="open-packet" data-job="${j.id}" data-inv="${draft.id}"`, "bill");
  if (draft) add("void", "Delete draft", `data-action="void-draft" data-id="${j.id}"`, "bill", { minor: true, danger: true });
  if (demo) add("qbo", "Import QuickBooks sample", `data-action="import-qbo" data-id="${j.id}"`, "paid", { minor: true });
  // A draft out for the client's review waits on their answer: sending comes second.
  const order = pencilOut ? ["pencil-ok", "clear", "send", "waiver"] : ["clear", "send", "waiver"];
  // While the client has the pay app the main step is the follow-up once it is due, then finished
  // work still to bill, then recording the payment.
  if (waitingPay) order.push(...(followDue ? ["follow"] : []), "draft", "match");
  else order.push("draft", "progress", "closeout", "close", "retainage", "retpay");
  const primary = order.map(k => all.find(a => a.key === k)).find(Boolean) || null;
  const more = all.filter(a => a !== primary).sort((a, b) => SECONDARY_ORDER.indexOf(a.key) - SECONDARY_ORDER.indexOf(b.key));
  return { primary, more };
}

export const ACTION_GROUPS = [["bill", "Bill"], ["paid", "Get paid"], ["close", "Close out"]];

// The buttons beside one thing that stands between the job and Finish job (R3-08), from the
// server's close_blocker_items ({kind, text, invoice_id, co_id, number}): [{label, attrs}].
// perms as for mayTake: a member doesn't see what only an owner may do.
export function closeBlockerActions(j, b, perms = null, now = new Date()) {
  return closeBlockerButtons(j, b, now).filter(a => mayTake(a.needs, perms)).map(a => ({ ...a, label: termText(a.label) }));
}

function closeoutAttrs(j) {
  const first = (j.closeout_packet || []).find(p => p.required && !p.present);
  return `data-action="open-packet" data-job="${j.id}"${(j.closeout_packet || []).length ? ` data-inv="closeout"` : ""}${first ? ` data-item="${first.id}"` : ""}`;
}

function closeBlockerButtons(j, b, now) {
  const id = j.id;
  switch (b.kind) {
    case "not_done": return [{ label: "Final pay app", attrs: `data-action="toggle-form" data-form="done"` }];
    case "draft": return [
      { label: "Review & send", attrs: `data-action="open-payapp" data-job="${id}" data-inv="${b.invoice_id}" data-focus=".send-check"`, needs: "send" },
      { label: "Delete draft", attrs: `data-action="void-draft" data-id="${id}"`, needs: "void-draft" }];
    case "co": return [
      { label: `Get CO ${b.number} Signed`, attrs: `data-action="open-co" data-job="${id}" data-co="${b.co_id}"` },
      { label: "Withdraw", attrs: `data-action="withdraw-co" data-id="${id}" data-co="${b.co_id}"` }];
    case "unbilled": return [{ label: "Draft pay app", attrs: `data-action="draft" data-id="${id}"` }];
    case "overbilled": return [
      ...(b.invoice_id ? [{ label: `Open ${b.number}`, attrs: `data-action="open-payapp" data-job="${id}" data-inv="${b.invoice_id}"` }] : []),
      ...(b.creditable_cents ? [{ label: "Take credit off retainage", attrs: `data-action="toggle-form" data-form="retcredit"`, needs: "retainage-credit" }] : []),
      ...(b.settleable === false ? [] : [{ label: "Record refund or credit", attrs: `data-action="toggle-form" data-form="overbilled"`, needs: "overbilling-settled" }])];
    case "outstanding": return [{ label: "Record a payment", attrs: `data-action="toggle-form" data-form="match"` },
      ...shortPayable(j).map(i => ({ label: shortPayLabel(i), attrs: shortPayAttrs(j, i), needs: "accept-short-pay" }))];
    case "retainage": return [
      { label: "Record retainage paid", attrs: `data-action="toggle-form" data-form="retpay"` },
      ...(retainageAskWaits(j, now) && !closeoutMissing(j) ? [] : [closeoutMissing(j)
        // An older job without a closeout packet keeps those items in the job paperwork (as on Closeout).
        ? { label: "Open closeout documents", attrs: closeoutAttrs(j) }
        : { label: "Ask for retainage", attrs: `data-action="retainage" data-id="${id}"`, needs: "retainage" }])];
    case "waiver": return [{ label: "Send waiver", attrs: `data-action="open-waiver" data-id="${id}"`, needs: "waiver" }];
    default: return [];
  }
}

// Part-paid pay apps the client may not pay in full (R4-01): accepting the short pay settles them.
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

// ---------- jobs-clients (Stephen's Oct 29 review) ----------

// Client type (J2): the five kinds of client, as domain.CLIENT_TYPES has them. GC and another
// subcontractor start with a general contractor's paperwork and timing; the rest, an owner's.
export const CLIENT_TYPES = [
  ["gc", "General contractor"], ["owner", "Owner or developer"], ["homeowner", "Homeowner"],
  ["sub", "Another subcontractor (you are a lower tier)"], ["public", "Public agency"],
];

// The client's type in words; one saved before there were types reads from its role.
export function clientTypeLabel(p) {
  const kind = unesc((p && p.client_type) || "") || (unesc((p && p.role) || "") === "GC" ? "gc" : "owner");
  return (CLIENT_TYPES.find(([k]) => k === kind) || CLIENT_TYPES[1])[1];
}

// The jobs still being worked (J9): a finished job leaves the working lists, pickers and the
// calendar, and stays under Job history and its client's card. Money (Pay apps, Change orders,
// Unpaid, Payments, Closeout), the Dashboard and the Calendar filter with this too.
export function openJobs(jobs) { return (jobs || []).filter(j => !j.closed); }

// The open jobs a new pay app can be started on from Money, Pay apps (G10), each with the job
// page's own step for it as jobActions offers it to this person: "progress" (the Progress pay app
// form, while the work goes on) or "draft" (Draft pay app, for a job marked done with work not
// billed). One way of drafting, whichever page it starts from. Left out: a job with a draft open,
// including one waiting for the client's approval (Review & send opens it), and a job in progress
// whose contract is billed in full.
export function payAppStarts(jobs, perms = null) {
  return openJobs(jobs).flatMap(j => {
    if ((j.invoices || []).some(i => DRAFTS.includes(i.status))) return [];
    if (j.work_status !== "complete" && fullyBilled(j)) return [];
    const { primary, more } = jobActions(j, { perms });
    const step = [primary, ...more].find(a => a && (a.key === "draft" || a.key === "progress"));
    return step ? [{ job: j, step: step.key }] : [];
  });
}

// Billed and retainage held already reach the contract (with approved change orders), as the
// server counts what is left to bill. A job with no contract amount is never counted as billed.
function fullyBilled(j) {
  const m = j.money || {};
  const total = m.contract_total_cents || 0;
  return total > 0 && (m.billed_cents || 0) + (m.retainage_held_cents || 0) >= total;
}

// The day of the daily record a draft's quantities came from (M1), as the schedule card shows
// per line (from_daily_on): the latest among the lines this draft bills for work this period.
// "" for a sent pay app, or when the quantities were typed on the job page.
export function draftFromDailyOn(j, inv) {
  if (!j || !inv || !["draft", "awaiting_approval"].includes(inv.status)) return "";
  const lines = (j.sov_view && j.sov_view.lines) || [];
  const days = (inv.sov_lines || []).filter(e => (e.qty_milli_this || 0) > 0)
    .map(e => (lines.find(l => l.id === e.line_id) || {}).from_daily_on).filter(Boolean);
  // Quantities recorded after the draft was made aren't on it.
  const made = String(inv.created_at || "9999").slice(0, 10);
  return days.filter(d => d <= made).sort().pop() || "";
}

// A job filter is worth a select only with two jobs or more to pick between (round 1, item 8).
export const jobFilterShown = (jobs) => (jobs || []).length > 1;

// A job filter on a page of records (Activity log, Outbox, Everything received, Sent pay apps):
// the open jobs, then the recently finished ones under "Finished jobs", so an old job's entries
// can still be found. Names and ids come escaped from the server, as on every other picker.
export function jobFilterOptions(jobs, selected = "") {
  const opt = (j) => `<option value="${j.id}"${selected === j.id ? " selected" : ""}>${j.name}</option>`;
  const done = (jobs || []).filter(j => j.closed);
  return openJobs(jobs).map(opt).join("") + (done.length ? `<optgroup label="Finished jobs">${done.map(opt).join("")}</optgroup>` : "");
}

// The job records in groups (J5), each with its kinds (RECORD_KINDS keys, and "ticket:type" for a
// ticket that says which it is). A kind not listed goes under Notes and other.
export const RECORD_GROUPS = [
  ["contract", "Contract and change orders", ["contract", "change_order"]],
  ["daily", "Diary entries", ["daily"]],
  ["delivery", "Delivery tickets", ["ticket:delivery"]],
  ["photo", "Progress photos", ["photo"]],
  ["time_sheets", "Time sheets", ["ticket:time_sheet"]],
  ["tickets", "Tickets", ["ticket"]],
  ["invoices", "Invoices", ["supplier_invoice"]],
  ["inspections", "Inspections", ["inspection"]],
  ["stored", "Stored materials", ["stored"]],
  ["waiver", "Lien waivers", ["waiver"]],
  ["insurance", "Insurance and certificates", ["insurance", "payroll", "notarized"]],
  ["closeout", "Closeout", ["punch", "completion", "asbuilt", "warranty"]],
  ["pay_app", "Pay app backup", ["pay_app"]],
  ["conversation", "Conversations", ["conversation"]],
  ["other", "Notes and other", ["note", "other"]],
];

// A note from before conversations were records, labelled "Spoke to Sam", reads and files as a
// conversation; what is stored stays until someone edits it.
export function recordFace(r) {
  const label = unesc(r.label || "");
  if (unesc(r.kind || "") !== "note" || !/^Spoke to /i.test(label)) return r;
  return { ...r, kind: "conversation", label: String(r.label).replace(/^Spoke to /i, "Conversation with ") };
}

// [{key, label, records}] for the groups that have records, newest first in each.
export function groupRecords(records) {
  records = (records || []).map(recordFace);
  const known = new Set(RECORD_GROUPS.flatMap(([, , kinds]) => kinds));
  const typed = (r) => { const k = unesc(r.kind || ""), t = unesc(r.ticket_type || ""); return k === "ticket" && TICKET_TYPES[t] ? `ticket:${t}` : k; };
  return RECORD_GROUPS.map(([key, label, kinds]) => ({
    key, label,
    records: (records || []).filter(r => kinds.includes(typed(r)) || (key === "other" && !known.has(typed(r)))).slice().reverse(),
  })).filter(g => g.records.length);
}

// The paperwork a client usually needs (S11), as the Client playbook's checkboxes: the server's
// standard catalog (domain.PAPERWORK_ITEMS, sent as state.paperwork_catalog), in two groups.
// The labels are saved as the comma list the server reads (domain.parse_packet_text, which keeps
// a catalog label with "and" in it whole); the server knows the closeout ones by their words.
export function paperworkChoices(catalog) {
  const labels = (closeout) => (catalog || []).filter(e => !!e.closeout === closeout).map(e => unesc(e.label));
  return [["Each pay app", labels(false)], ["At closeout", labels(true)]];
}
const choiceList = (catalog) => paperworkChoices(catalog).flatMap(([, list]) => list);
const choiceKey = (label) => String(label).toLowerCase().replace(/\s+/g, " ").trim();

// The saved list as {checked: [labels], other: "text"}: an item that is a checkbox ticks it
// (case and spacing aside); the rest go in Other, as typed.
export function packetChoices(items, catalog) {
  const choices = choiceList(catalog);
  const checked = [], other = [];
  for (const item of items || []) {
    const text = unesc(item);
    const hit = choices.find(c => choiceKey(c) === choiceKey(text));
    if (hit) checked.push(hit); else other.push(text);
  }
  return { checked, other: other.join(", ") };
}

// What a save sends as the paperwork list: the ticked labels, then Other, in one comma list.
export function packetText(checked, other, catalog) {
  return [...choiceList(catalog).filter(c => checked.includes(c)), ...String(other || "").split(/[,;\n]/).map(t => t.trim()).filter(Boolean)].join(", ");
}

// The job's start date limits (J8): about 10 years back and 2 years ahead. The server
// (domain/jobinfo.py START_BACK_DAYS) allows a day more each way, for a device a time zone off.
export function startDateLimits(now = new Date()) {
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const back = new Date(now), ahead = new Date(now);
  back.setDate(back.getDate() - 3652);
  ahead.setDate(ahead.getDate() + 730);
  return { min: iso(back), max: iso(ahead), today: iso(now) };
}
