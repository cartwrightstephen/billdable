// Where a job stands: blocked or not, the one next step, status pills, change-order signatures and waiver kinds.

import { unesc, fmtDate, fmtDateTime } from "./format.js";

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

// The button that opens the paperwork page.
export function blockerButton(j) {
  const n = j.blockers.length;
  return needsPaperwork(j) ? `Add ${n} missing document${n === 1 ? "" : "s"}` : `Clear ${n} blocker${n === 1 ? "" : "s"}`;
}

export function nextStep(j) {
  if (j.closed) return "Closed for the record.";
  if (needsPaperwork(j)) return "Attach the paperwork, then send: " + j.missing_paperwork.join(", ");
  if (isBlocked(j)) return "Clear blockers: " + j.blockers[0];
  const draft = j.invoices.find(i => ["draft", "awaiting_approval"].includes(i.status));
  if (draft) return "Pay app is ready. Review it, then Approve & send.";
  if (j.waiver_due && !j.waiver_sent) return "Payment received. Approve and send the waiver.";
  if (j.invoices.some(i => ["sent", "approved", "partial"].includes(i.status))) return "Waiting on the payer. Follow up or match a payment.";
  if (j.work_status !== "complete") return "Record progress, or mark the job done." + (j.blockers && j.blockers.length ? " The next pay app needs: " + j.blockers.join("; ") : "");
  if (j.money.retainage_open_cents > 0) return "Chase retainage once the closeout packet is complete.";
  if (j.money.outstanding_cents === 0 && j.money.unbilled_completed_cents === 0) return (j.close_blockers || []).length ? "Before closing: " + j.close_blockers[0] : "Ready to close for the record.";
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
  const final = j.work_status === "complete" && j.money.unbilled_completed_cents === 0 && j.money.outstanding_cents === 0;
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
