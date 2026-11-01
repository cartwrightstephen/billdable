// What the Money pages share: the send gate for an unconfirmed email and the owner-only note.
// Each Money tab has its own module: payapps.js, paperwork.js, changeorders.js, collections.js,
// payments.js and closeout.js; the Outbox is in communications.js.
import { me, mailReady } from "../state.js";
import { DEMO_COMPANY_ID } from "../app-core.js";

// A sent pay app downloads as a QuickBooks invoice-import file (R4-11).
export const QBO_READY = new Set(["sent", "approved", "partial", "paid"]);
export const qboCsvLink = (jobId, invId) => `<a class="btn secondary small qbo-csv" href="/api/jobs/${jobId}/payapp/${invId}.csv" download>Download for QuickBooks</a>`;

export const OWNER_SENDS = `<p class="sub owner-only">Only an owner can send this. Tell an owner it is ready.</p>`;

// On a server that sends mail, nothing goes out until the signed-in email is confirmed: the
// server refuses (R1-07), so the send buttons are off and say why, with the link to send again.
export function emailUnconfirmed() {
  return !!(mailReady && me && !me.email_verified && me.company_id !== DEMO_COMPANY_ID);
}

export function confirmFirstHtml(what) {
  return `<p class="notice confirm-first">Confirm your email first so ${what} goes out from you.
    <button class="btn small secondary" data-action="resend-verify">Send the link again</button></p>`;
}
