// The words the app uses (R6-01, the glossary in docs/improvement-plan.md section 4): what a
// company that bills homeowners in draws sees in place of "pay app", and the History pills as
// sentences instead of action codes. Nothing here reads the page; app.js sets the billing style
// when the company's data arrives, so node can test it (tests/js/terms.test.mjs).

import { words } from "./format.js";

// Company setting billing_style: "pay_apps" (the default) or "draws" (homeowners, R6-01).
let style = "pay_apps";

export function setBillingStyle(value) { style = value === "draws" ? "draws" : "pay_apps"; }

export function billingStyle() { return style; }

// One word per style. The PA- numbers on the documents stay as they are.
export const TERM = {
  pay_apps: { one: "pay app", many: "pay apps", One: "Pay app", Many: "Pay apps" },
  draws: { one: "draw", many: "draws", One: "Draw", Many: "Draws" },
};

export function term(key) { return TERM[style][key]; }

// Fixed copy with "pay app" in it, in the company's word: "Draft pay app" reads "Draft draw" in
// draws mode. Only for the app's own words, never for what someone typed (a job name, a note).
export function termText(text, s = style) {
  if (s !== "draws" || !text) return text;
  return String(text)
    .replace(/\bPay apps\b/g, "Draws").replace(/\bpay apps\b/g, "draws")
    .replace(/\bPay app\b/g, "Draw").replace(/\bpay app\b/g, "draw")
    .replace(/\bPAY APPS\b/g, "DRAWS").replace(/\bPAY APP\b/g, "DRAW");
}

// The History pill for each action in the activity log: a short sentence, not the code
// ("Approve send" read like an order). Codes not listed fall back to their words.
export const ACTION_WORDS = {
  approve_send: "Pay app sent", draft_invoice: "Pay app drafted", record_progress: "Progress recorded",
  draft_replaced: "Draft replaced", draft_skipped: "Draft replaced", void_draft: "Draft deleted", void_sent: "Pay app pulled back",
  match_payment: "Payment recorded", short_pay: "Short pay accepted", retainage_paid: "Held-back money received",
  chase_retainage: "Asked for held-back money", follow_up: "Follow-up sent", contact_noted: "Spoke to the customer",
  send_waiver: "Waiver sent", job_done: "Job marked done", undo_done: "Back to in progress", close_job: "Job finished",
  reopen_job: "Job reopened", create_job: "Job added", edit_job: "Job changed", delete_job: "Job deleted",
  contract_changed: "Contract changed", add_co: "Change order added", approve_co: "Change order signed",
  withdraw_co: "Change order withdrawn", include_co: "Change order put on the bill", change_order_off_draft: "Change order taken off the draft",
  request_co_signature: "Signature requested", add_record: "Added to the job record", edit_record: "Job record changed",
  add_evidence: "Added to the job record", attach_record: "Paperwork added", packet_attachment: "Paperwork added",
  packet_resolved: "Paperwork in", paperwork_detached: "Paperwork taken off", paperwork_not_needed: "Paperwork not needed",
  daily_record: "Daily record saved", pencil_sent: "Draft sent for their review", pencil_response: "Customer answered the draft",
  payer_received: "Customer opened it", payer_approved: "Customer approved", payer_question: "Customer asked a question",
  reply_captured: "Customer replied", learn_rejection: "Customer's reason saved", playbook_learned: "Customer settings learned",
  playbook_update: "Customer settings changed", payer_add: "Customer added", payer_merge: "Customers merged", payer_kept: "Customer kept",
  import_filed: "Added to the job", import_undone: "Taken off the job", sov_saved: "Schedule of values saved", sov_removed: "Schedule of values removed",
  billing: "Billing changed", company_update: "Company changed", team: "Team changed", team_join: "Joined the team",
  qbo_connected: "QuickBooks connected", qbo_disconnected: "QuickBooks disconnected", read_only: "Read-only changed",
};

// The pill's words for an action code (escaped, as words() is), in the company's word.
export function actionWords(code) {
  const known = ACTION_WORDS[code];
  return known ? termText(known) : words(code);
}

// Each billing style's line under the Settings choice (R6-01).
export const STYLE_NOTE = "Changes the word used across the app and sets the free trial: 90 days, or 45 for draws.";
