// The short explanations shown next to field labels and tabs.

import { money, words } from "./format.js";

// Plain-language help for every field, column, tile and section. The (i) icon
// next to a label is hidden until the area is hovered or focused; hovering or
// focusing it shows the text. On a touch screen there are no icons: each card
// gets one "What do these mean?" list instead. A form field's key is the
// helpKey its field() call names (its label carries data-help); anything else
// is keyed by its visible label, lower case, without arrows or asterisks.
export const FIELD_HELP = {
  // the Money card and tiles
  "contract": "The original contract amount, before any change orders.",
  "signed change orders": "Change orders the customer has signed. They add to the contract total.",
  "unsigned change orders": "Change orders not signed yet. They are not billable until signed.",
  "contract total": "Contract plus signed change orders: the most you can bill on this job.",
  "billed": "Pay apps sent to the customer, after retainage is held back.",
  "collected": "Payments received on this job's pay apps.",
  "outstanding": "Billed but not yet paid.",
  "done, not billed": "Work you finished that is not on any pay app yet. This is money you can bill now.",
  "unbilled": "Work you finished that is not on any pay app yet.",
  "retainage held": "Retainage: the percentage the customer holds back from each pay app until closeout.",
  "retainage released": "Retainage the customer has paid you.",
  "held back, still owed": "Retainage still held. It is paid after the punch list and closeout paperwork are done.",
  "billed this month": "Total of pay apps sent this calendar month.",
  // tables
  "job": "The job. Open it for its money, paperwork and next steps.",
  "customer": "Who pays this job: the owner, homeowner or general contractor.",
  "status": "What is holding the job up right now, in words. Never just \"pending\".",
  "next": "The next thing to do to get paid on this job.",
  "code": "Short job code. Put it in an email subject or text and the Inbox files it to this job.",
  "number": "Pay app number: PA, the job code, then the sequence.",
  "amount": "Amount of this pay app after retainage, or of this payment.",
  "before it can go": "What must be fixed before this pay app can be sent: missing paperwork or an unsigned change order.",
  "missing": "Required paperwork that is not in yet.",
  "pay app": "The pay application (pay app) this paperwork goes with.",
  "co": "Change order number on this job.",
  "summary": "What the change order covers.",
  "unpaid": "Pay apps sent and not fully paid.",
  "contact": "Who signs off on your bills, and their email and phone.",
  "follow up": "Send a follow-up once the customer's days between follow-ups have passed.",
  "reference": "Check number or QuickBooks payment id. Each can be used once per job.",
  "received": "Date the payment came in.",
  "kind": "What type of document or message this is.",
  "held back": "Retainage still held on this job.",
  "holding it up": "Closeout items that must be done before you can ask for retainage.",
  "finished": "Date the job was finished.",
  "notes": "What was noted when the job was marked done, or where it stands.",
  "when": "When it happened.",
  "to": "Who it was addressed to: the customer's email for pay apps, from Customer settings.",
  "subject": "Subject line of the logged message.",
  "action": "What was done, in a few words.",
  // form fields: field() names its entry with helpKey, so the same words on another form
  // (the company's own address and phone) don't pick up this help (R3-10)
  "job_name": "What you call the job, for example \"Oak Street kitchen\".",
  "job_address": "The job site address. The Inbox uses it to match emails and texts to this job.",
  "contract_number": "The number on the contract, if it has one.",
  "contract_amount": "The signed contract amount in dollars, before change orders.",
  "retainage_pct": "Percent the customer holds back from each pay app until closeout. Commonly 5 to 10 percent; check your contract. A change applies from the next pay app.",
  "role": "The homeowner or property owner, or a general contractor when you are their sub. It sets the paperwork they usually need with each pay app and when to follow up.",
  "approver": "The person who approves your pay app. Left blank, it is the customer.",
  "ap_email": "Homeowner: their email. GC: their accounts-payable address. You can add it before the first send.",
  "customer_phone": "The customer's phone, for follow-up calls.",
  "job_notes": "Anything to keep with the job, like a gate code or who to call. The paperwork each pay app needs is set in Customer settings.",
  "customer_name": "The name you know them by. A new job for the same name, in any case, goes to this customer.",
  "follow_up_days": "Days to wait after sending before a follow-up is due. Set it to the customer's normal payment window. Blank goes back to 7.",
  "payapp_due_day": "The day of the month their pay apps are due by, if the contract sets one. Leave it blank if not.",
  "usual_approval_days": "How long this customer usually takes to pay after a pay app is sent. Sets the expected date on the calendar. Blank goes back to 14.",
  "packet": "What this customer needs with every pay app, separated by commas. New pay apps list these items. Leave it blank if they need nothing.",
  "rejects_when": "Why this customer has sent pay apps back before. It is shown before the next send.",
  "copy": "Email addresses to copy on pay apps for this customer, separated by commas. Names without an address are ignored.",
  "payment_terms_days": "The days the contract gives them to pay, like 30. A pay app sent today shows the date it is due, on the PDF and in Unpaid. Leave it blank if the contract sets none.",
  "escalation_email": "Someone above the person who signs off, like the owner or project manager. From the third follow-up on the same pay app, they are copied.",
  "period_to": "The last day of work this pay app covers. Usually today or the end of the month; it can't be before the last pay app's.",
  "due": "When payment is due under the customer's payment terms. Overdue once the date has passed.",
  // schedule of values
  "item": "The line number on the schedule of values, as the customer's form numbers it.",
  "description": "What the line covers, in the words the customer will recognize.",
  "qty": "How many units the line covers. A lump-sum line is 1 (unit LS).",
  "unit": "The unit the quantity is counted in: ea, lf, sf, hr, or LS for a lump sum.",
  "unit rate $": "Price per unit, before overhead and profit.",
  "unit rate": "Price per unit, before overhead and profit.",
  "scheduled value": "Quantity × unit rate: what this line is worth when finished.",
  "done to date": "How much of the line is finished so far, counted from the start of the job.",
  "earned to date": "The value of the work done to date on this line.",
  "balance to finish": "What is left to bill on this line when the work is done.",
  "overhead and profit": "A percentage added on top of the base lines, as your estimate carries it. Change orders are added at their own amount.",
  "schedule lines": "The total of quantity × unit rate across the lines.",
  "schedule total": "Lines plus overhead and profit. It has to equal the contract amount.",
  "scheduled": "How much this line covers.",
  "previous": "Billed on earlier pay apps.",
  "this period": "Billed on this pay app: work done since the last one.",
  "total to date": "Previous plus this period.",
  "%": "Share of the line's scheduled value billed to date.",
};

export const NAV_HELP = {
  today: "What needs you today, the money across open jobs, the calendar and the daily record.",
  money: "Pay apps and their paperwork, change orders, what is unpaid, payments, closeout and the Outbox.",
  settings: "Your company, team, account and billing, customer settings, the activity log and help.",
  inbox: "Bring in photos, PDFs, emails, texts and notes, then file them to a job in one click.",
  dashboard: "What needs attention and the money across open jobs.",
  daily: "A dated note of what happened on site today, saved on the job record.",
  jobs: "Every job, with its money, paperwork and next steps.",
  calendar: "Follow-ups, expected approvals, payments and closeouts by date. Can be added to Google Calendar.",
  results: "How fast work turns into pay apps and pay apps into money, from your own records.",
  payapps: "Pay applications (pay apps): the bill for work done this period, with the paperwork the customer needs in its Paperwork column.",
  packets: "The paperwork that must go with each pay app: waivers, tickets, photos, signed change orders.",
  cos: "Change orders: extra or changed work. Not billable until the customer signs.",
  collections: "Unpaid: what is owed and who to contact.",
  payments: "Payments received, each recorded against a pay app.",
  waivers: "Closeout: lien waivers for each paid pay app (you give up lien rights for money you have been paid), and the money held back (retainage) with what is holding it up.",
  retainage: "Money the customer holds back (retainage) until closeout, and what is holding it up.",
  history: "Finished jobs, kept on record.",
  playbook: "Each customer: where pay apps go, the paperwork they need, how long they take to pay, and why they send pay apps back.",
  outbox: "Every email approved to send, and whether it was delivered.",
  audit: "Activity log: every action, who took it and when.",
  help: "What the terms mean and how to start.",
  company: "What prints on every pay app, how you bill, sending and email, QuickBooks, your data and deleting the company.",
  team: "The people on your team, their roles and invites.",
  account: "Your name, password and two-step sign-in.",
  billing: "Your subscription and free trial. Owners only.",
  "helper-admin": "Questions the helper couldn't answer, and the answers it uses. Team only.",
};

export function helpFor(text) {
  const key = (text || "").replace(/[↑↓*]/g, "").replace(/\s+/g, " ").trim().toLowerCase();
  return FIELD_HELP[key];
}
