// Money, Paperwork: what goes with each pay app and the closeout paperwork, and the paperwork
// part of a pay app's Before you send (beforeYouSendHtml).
import { unesc, money, words, INVOICE_WORDS } from "../format.js";
import { RECORD_KINDS, isBlocked, coBlockers } from "../jobs.js";
import { pathFor } from "../routes.js";
import { field } from "../forms.js";
import { termText } from "../terms.js";
import { state, currentJobId, currentDetailId, formOpen } from "../state.js";
import { may, render, toast, post, backButton, emptyState, nav } from "../app-core.js";

// The job-record kind for a file taken for a paperwork item: the item's own kind when it is one
// (waiver, ticket, contract, photo...), else Other.
function recordKindFor(item) {
  return RECORD_KINDS[unesc(item.kind || "")] && !["note", "daily"].includes(item.kind) ? item.kind : "other";
}

// The paperwork line of a pay app's Before you send card: what goes with the email.
export function beforeYouSendHtml(j, inv) {
  const pkt = inv.packet || j.packet || [];
  return `<div class="money-row"><span class="label">Attached</span><span>Pay app PDF${inv.sov_sheet && inv.sov_sheet.length ? " with continuation sheet" : ""}, paperwork checklist, ${pkt.filter(p => p.present).length} paperwork item${pkt.filter(p => p.present).length === 1 ? "" : "s"}</span></div>`;
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  packetDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j) return backButton("payapps", termText("Pay apps")) + emptyState("Paperwork not found", "The job may have been deleted.", `<button class="btn" data-action="nav" data-view="payapps">${termText("Go to Pay apps")}</button>`);
    const isCloseout = currentDetailId === "closeout";
    const inv = isCloseout ? null : (j.invoices.find(i => i.id === currentDetailId) || [...j.invoices].reverse().find(i => i.packet) || j.invoices[j.invoices.length - 1]);
    const packet = isCloseout ? (j.closeout_packet || []) : ((inv && inv.packet) || j.packet || []);
    const jobRecords = j.records || [];
    if (!inv && !packet.length) return backButton("job", j.name, j.id) + emptyState("No paperwork yet", "Draft a pay app. Its paperwork list comes with it, from this customer's settings. Until then, add notes and files to the job record.", `<button class="btn" data-action="open-job" data-id="${j.id}">Back to ${j.name}</button>`);
    const missing = packet.filter(p => p.required && !p.present).length;
    // Unsigned change orders on the bill block the same send; say so here with a way to them (R3-05).
    const cosBlocking = !isCloseout && isBlocked(j) ? coBlockers(j) : [];
    // What went with a pay app stays as it was sent: Remove is for a draft's paperwork, the job's and closeout's.
    const canRemove = !j.closed && !(inv && !["draft", "awaiting_approval"].includes(inv.status));
    return `
      ${backButton("job", j.name, j.id)}
      <h1>${isCloseout ? "Closeout paperwork" : (inv ? "Paperwork for " + inv.number : "Paperwork")}</h1>
      <p class="sub">${j.name}${inv ? " · " + money(inv.amount_cents) + " · " + (INVOICE_WORDS[inv.status] || words(inv.status)) : ""}${isCloseout ? " · needed before you ask for the money held back" : ""} · ${missing ? `<span class="blocker">${missing} missing</span>` : "complete"}</p>
      <div class="card">
        <h2>Required items</h2>
        ${packet.length ? packet.map(p => {
          const skipped = !p.required && p.skipped_reason;
          return `
          <div class="list-item packet-item ${p.present || skipped ? "present" : "absent"}">
            <div><span class="mark" aria-hidden="true">${p.present ? "✓" : skipped ? "–" : "✗"}</span> <strong>${p.label}</strong> <span class="visually-hidden">${p.present ? "in" : skipped ? "not needed" : "missing"}</span>
              ${!p.present && !skipped ? `<span class="field-error">${p.blocker}</span>` : ""}
              ${skipped ? `<div class="sub">Not needed for this customer${p.skipped_reason !== "Not needed for this customer" ? ": " + p.skipped_reason : ""}</div>` : ""}
              ${p.present && p.present_reason === "Produced by Duracash" ? `<div class="sub">Produced by Duracash with the pay app</div>` : ""}
              ${p.present && p.present_reason && p.present_reason !== "Produced by Duracash" ? `<div class="sub">Marked as in hand: ${p.present_reason}</div>` : ""}
              ${p.present && !p.present_reason && !(p.attachments || []).length ? `<div class="sub">Marked as in hand.</div>` : ""}
              ${(p.attachments || []).map(a => `<div class="sub">${a.auto ? "Attached from the job record" : "Attached"}: ${a.name}</div>`).join("")}
              ${p.present && p.present_reason !== "Produced by Duracash" && canRemove ? `<button class="btn small secondary" data-action="packet-detach" data-id="${j.id}" data-item="${p.id}">Remove</button>` : ""}
            </div>
            ${!p.present && !skipped ? `<div class="form-inline packet-add">
              <button class="btn small" data-action="pick-files" data-job="${j.id}" data-attach="${p.id}" data-kind="${recordKindFor(p)}" data-capture="1">Take a photo</button>
              <button class="btn small secondary" data-action="pick-files" data-job="${j.id}" data-attach="${p.id}" data-kind="${recordKindFor(p)}">Choose file</button>
            </div>
            <div class="form-inline">
              ${jobRecords.length ? `<select id="attach-rec-${p.id}" aria-label="Job record to attach"><option value="">Choose a record…</option>${jobRecords.map(r => `<option value="${r.id}">${r.label}</option>`).join("")}</select><button class="btn small secondary" data-action="attach-record" data-id="${j.id}" data-item="${p.id}">Attach</button>` : ""}
              <button class="btn small secondary" data-action="toggle-form" data-form="resolve-${p.id}" aria-expanded="${!!formOpen["resolve-" + p.id]}">I have this…</button>
              ${isCloseout ? "" : `<button class="btn small secondary" data-action="packet-skip" data-id="${j.id}" data-item="${p.id}">Not needed for this customer</button>`}
            </div>
            ${formOpen["resolve-" + p.id] ? `<div class="form-inline"><input id="resolve-reason-${p.id}" placeholder="Where is it? (optional)" aria-label="Where is it? (optional)" class="grow"><button class="btn small" data-action="resolve" data-id="${j.id}" data-item="${p.id}">Save</button></div>` : ""}` : ""}
          </div>`;
        }).join("") : `<p class="sub">${termText("This pay app needs no paperwork.")}</p>`}
      </div>
      ${!isCloseout && cosBlocking.length ? `<div class="card also-blocking" role="note">
        <h2>Also blocking send</h2>
        ${cosBlocking.map(b => `<div class="list-item">${b.text} <a href="${pathFor("co-detail", j.id, b.co_id)}" data-action="open-co" data-job="${j.id}" data-co="${b.co_id}">Open CO ${b.number}</a></div>`).join("")}
      </div>` : ""}
    `;
  },
  "packet-detail": function() { return views.packetDetail(); },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  "resolve": async (el, id) => {
    const box = document.getElementById(`resolve-reason-${el.dataset.item}`);
    const reason = (box?.value || "").trim();   // optional (R3-05)
    const r = await post(`/api/jobs/${id}/resolve/${el.dataset.item}`, { reason });
    if (r && r.ok) { formOpen[`resolve-${el.dataset.item}`] = false; render(); }
  },
  "goto-packet": (el, id) => nav("packet-detail", id),
  "open-packet": (el) => nav("packet-detail", el.dataset.job, el.dataset.inv),
  "attach": async (el, id) => {
    const name = document.querySelector("#attach-name")?.value || "attachment.pdf";
    await post(`/api/jobs/${id}/attach/${el.dataset.item}`, { name });
  },
  "packet-detach": (el, id) => post(`/api/jobs/${id}/detach/${el.dataset.item}`),
  "packet-skip": (el, id) => post(`/api/jobs/${id}/packet-skip/${el.dataset.item}`, {}),
  "attach-record": async (el, id) => {
    const recordId = document.querySelector(`#attach-rec-${el.dataset.item}`)?.value;
    if (!recordId) { toast("Choose a record first."); document.querySelector(`#attach-rec-${el.dataset.item}`)?.focus(); return; }
    await post(`/api/jobs/${id}/attach-record/${el.dataset.item}`, { record_id: recordId });
  },
};
