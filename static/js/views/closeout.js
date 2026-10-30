// Money, Closeout: lien waivers by pay app and the money held back (retainage), and each job's
// waiver page.
import { money, centsFrom, fmtDate } from "../format.js";
import { WAIVER_KINDS, waiverKindLabel, waiverHint, waiverTypeText, waiverSendForm, retainageAskWaits } from "../jobs.js";
import { pathFor } from "../routes.js";
import { askConfirm } from "../dom.js";
import { moneyField, formErrorLine, refuseForm, formValues } from "../forms.js";
import { termText } from "../terms.js";
import { state, currentJobId } from "../state.js";
import { may, post, backButton, emptyState, nav } from "../app-core.js";
import { OWNER_SENDS, emailUnconfirmed, confirmFirstHtml } from "./money.js";
import { sendsTo } from "./job.js";

// Ask for retainage from the Retainage page (R4-07): an amount box, blank for all of it once
// the job is done; before that, the part to ask for now.
function retainageAskHtml(j) {
  const key = `retask-${j.id}`, done = j.work_status === "complete";
  if (!may("retainage")) return `<span class="sub">An owner asks for it.</span>`;
  // chase_retainage waits the customer's days between contacts after the last request.
  const asked = retainageAskWaits(j);
  if (asked) return `<span class="sub">Asked ${fmtDate(asked.last)}. Ask again from ${fmtDate(asked.due)}.</span>`;
  return `<div class="retainage-ask" data-form-key="${key}">${formErrorLine(key)}<div class="form-inline">
    ${moneyField({ form: key, name: "amount_cents", id: `${key}-amt`, label: done ? "Amount ($), blank for all" : "Part to ask for now ($)" })}
    <button class="btn small" data-action="retainage" data-id="${j.id}">Ask for retainage</button></div></div>`;
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  waiverDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j) return backButton("waivers", "Closeout") + emptyState("Waiver not found", "The job may have been deleted.", `<button class="btn" data-action="nav" data-view="waivers">Go to Closeout</button>`);
    const due = j.invoices.filter(i => i.waiver_due && !i.waiver_sent);
    const sent = j.invoices.filter(i => i.waiver_sent);
    const locked = emailUnconfirmed();
    const owed = due.length || (j.waiver_due && !j.waiver_sent);
    // Sending attaches the newest lien waiver on the job record; say so when there is none (R1-06).
    const hasWaiver = (j.records || []).some(r => r.kind === "waiver");
    return `
      ${backButton("waivers", "Closeout")}
      <h1>Waivers: ${j.name}</h1>
      <p class="sub">Duracash never writes a waiver: state law sets the forms. Drop your signed waiver on the job record; sending attaches the newest one.</p>
      <div class="card">
        <h2>What the customer needs</h2>
        <p>${j.waiver_standard || j.payer.packet || "See Customer settings."}</p>
      </div>
      ${owed && !j.summary && !hasWaiver ? `<div class="card no-waiver">
        <h2>No signed waiver on the job yet</h2>
        <p class="sub">Sending without one tells ${j.payer.name} the signed waiver follows separately.</p>
        <div class="form-inline">
          <button class="btn small" data-action="pick-files" data-job="${j.id}" data-kind="waiver" data-capture="1">Take a photo</button>
          <button class="btn small secondary" data-action="pick-files" data-job="${j.id}" data-kind="waiver">Choose file</button>
        </div>
      </div>` : ""}
      <div class="card send-check">
        <h2>${termText("By pay app")}</h2>
        ${owed && j.payer.ap_email ? `<div class="money-row"><span class="label">To</span><span>${j.payer.approver ? j.payer.approver + " · " : ""}${j.payer.ap_email}</span></div>` : ""}
        ${owed && !j.payer.ap_email ? `<p class="blocker">Add the email pay apps go to in <button class="linkish" data-action="nav" data-view="playbook">Customer settings</button>, then send.</p>` : ""}
        ${owed && locked ? confirmFirstHtml("the waiver") : ""}
        ${due.concat(sent).length ? due.concat(sent).map(i => `<div class="list-item"><strong>${i.number}</strong> · ${money(i.amount_cents)} · ${i.waiver_sent ? `sent ${fmtDate(i.waiver_sent_at)}${i.waiver_kind ? " · " + waiverKindLabel(i.waiver_kind) : ""}` : `<span class="pill amber">Due</span>`}
          ${!i.waiver_sent ? (may("waiver") ? waiverSendForm(j, i, locked || !j.payer.ap_email) : OWNER_SENDS) : ""}</div>`).join("")
          : j.waiver_due && !j.waiver_sent ? (may("waiver") ? waiverSendForm(j, null, locked || !j.payer.ap_email) : OWNER_SENDS) : `<p class="sub">${j.waiver_sent ? "Sent " + fmtDate(j.waiver_sent_at) : termText("No waiver is due yet. One is due after each paid pay app.")}</p>`}
      </div>
    `;
  },
  "waiver-detail": function() { return views.waiverDetail(); },
  // Closeout (R6-01): the lien waivers due and the money held back, one tab. /money/retainage
  // opens the same page at the held-back part.
  waivers() {
    const due = state.jobs.filter(j => j.waiver_due && !j.waiver_sent);
    const sent = state.jobs.filter(j => j.waiver_sent);
    const rows = due.concat(sent);
    const open = state.jobs.filter(j => j.money.retainage_open_cents > 0);
    return `
      <h1>Closeout</h1>
      <p class="sub">${termText("Lien waivers for each paid pay app, and the money held back until the job is closed out.")}</p>
      <section class="card closeout-waivers" aria-labelledby="closeout-waivers-h">
        <h2 id="closeout-waivers-h">Lien waivers</h2>
        <p class="sub">${termText("One lien waiver per paid pay app. Duracash never writes the waiver; it sends the signed one on the job record.")}</p>
        <details class="waiver-hints"><summary>Which type?</summary>
          <ul>${Object.entries(WAIVER_KINDS).map(([k, v]) => `<li><strong>${v}.</strong> ${waiverHint(k)}</li>`).join("")}</ul>
        </details>
        ${rows.length ? `<div class="table-wrap">
          <table>
            <tr><th>Job</th><th>Type</th><th>Amount</th><th>Status</th><th>Action</th></tr>
            ${rows.map(j => {
              const paid = j.invoices.find(i => i.waiver_due && !i.waiver_sent) || [...j.invoices].reverse().find(i => i.status === "paid");
              return `<tr>
                <td><a href="${pathFor("waiver-detail", j.id)}" data-action="open-waiver" data-id="${j.id}">${j.name}</a></td>
                <td>${waiverTypeText(j, paid)}</td>
                <td class="num">${paid ? money(paid.amount_cents) : "—"}</td>
                <td>${j.waiver_sent ? `<span class="pill green">Sent</span>` : `<span class="pill amber">Due</span>`}</td>
                <td>${!j.waiver_sent && j.waiver_due ? `<button class="btn small" data-action="open-waiver" data-id="${j.id}">Choose type and send</button>` : "—"}</td>
              </tr>`;
            }).join("")}
          </table>
        </div>` : emptyState("No waivers due", "A waiver is due once a payment is recorded.")}
      </section>
      <section class="card closeout-retainage" id="closeout-retainage" aria-labelledby="closeout-retainage-h">
        <h2 id="closeout-retainage-h">Held back, still owed</h2>
        <p class="sub">Retainage: what is holding each release, and how to get it. Before a job is done you can ask for part of it, for example at substantial completion.</p>
        ${open.length ? `<div class="table-wrap">
          <table>
            <tr><th>Job</th><th>Held back</th><th>Holding it up</th><th>Next</th><th>Contact</th></tr>
            ${open.map(j => {
              const missing = j.closeout_missing || [];   // labels (domain.closeout_missing)
              return `<tr>
                <td><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a></td>
                <td class="num">${money(j.money.retainage_open_cents)}</td>
                <td>${missing.length ? missing.join(", ") : (j.work_status === "complete" ? "Closeout paperwork complete" : "Job still in progress")}</td>
                <td>${j.work_status === "complete" && missing.length ? `<button class="btn small" data-action="open-packet" data-job="${j.id}" ${j.closeout_packet && j.closeout_packet.length ? `data-inv="closeout"` : ""}>Open closeout paperwork</button>` : retainageAskHtml(j)}</td>
                <td>${j.payer.approver}<div class="sub row-sub"><a href="mailto:${j.payer.ap_email}">${j.payer.ap_email}</a></div></td>
              </tr>`;
            }).join("")}
          </table>
        </div>` : emptyState("Nothing held back", "Money held back (retainage) shows here once a job holds it. Set a retainage percent when creating the job.")}
      </section>
    `;
  },
  retainage() { return views.waivers(); },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  "waiver": async (el, id) => {
    const kind = document.getElementById(`waiver-kind-${el.dataset.inv || "job"}`)?.value;
    await post(`/api/jobs/${id}/waiver`, { ...(el.dataset.inv ? { invoice_id: el.dataset.inv } : {}), ...(kind ? { kind } : {}) });
  },
  // All of the retainage, or the part typed on the Retainage page (R4-07): before the job is
  // done only a part can be asked for.
  "retainage": async (el, id) => {
    const j = state.jobs.find(x => x.id === id);
    const key = `retask-${id}`, form = document.querySelector(`[data-form-key="${key}"]`);
    const open = j ? j.money.retainage_open_cents : 0;
    let cents = null;
    if (form) {
      const typed = String(formValues(key).amount_cents || "").trim();
      const a = centsFrom(typed);
      if (!typed && j && j.work_status !== "complete") { refuseForm(key, { amount_cents: "Enter the part to ask for now. All of it waits until the job is done." }); return; }
      if (typed && (a.error || a.cents <= 0)) { refuseForm(key, { amount_cents: "Enter the retainage to ask for in dollars, like 5,000." }); return; }
      if (typed && a.cents > open) { refuseForm(key, { amount_cents: `Enter no more than the ${money(open)} held.` }); return; }
      if (typed && a.cents >= open && j && j.work_status !== "complete") { refuseForm(key, { amount_cents: `Enter less than the ${money(open)} held. All of it waits until the job is done.` }); return; }
      if (typed) cents = a.cents;
    }
    const part = cents != null && cents < open;
    if (j && !(await askConfirm(`Email a request for ${part ? `${money(cents)} of the ${money(open)}` : `the ${money(open)}`} retainage to ${sendsTo(j)}?`, "Send the request"))) return;
    await post(`/api/jobs/${id}/retainage`, cents != null ? { amount_cents: cents } : {}, form ? { formKey: key } : {});
  },
  "open-waiver": (el, id) => nav("waiver-detail", id),
};
