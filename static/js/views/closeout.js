// Money, Closeout: lien waivers by pay app and the money held back (retainage), and each job's
// waiver page.
import { money, centsFrom, fmtDate, unesc } from "../format.js";
import { WAIVER_KINDS, waiverKindLabel, waiverTypeWords, waiverHint, waiverSendForm, waiverPick, waiverStageText, retainageAskWaits, retainageFacts, daysAgoText, openJobs } from "../jobs.js";
import { pathFor } from "../routes.js";
import { askConfirm } from "../dom.js";
import { moneyField, field, formErrorLine, refuseForm, formValues } from "../forms.js";
import { termText } from "../terms.js";
import { state, currentJobId } from "../state.js";
import { may, post, backButton, emptyState, nav } from "../app-core.js";
import { OWNER_SENDS, emailUnconfirmed, confirmFirstHtml } from "./money.js";
import { sendsTo } from "./job.js";
import { waiverChecklistHtml, waiverFormHtml } from "./paperwork.js";

// Ask for retainage from Closeout (M24): only once the job is marked done; an amount box, blank
// for all of it.
function retainageAskHtml(j) {
  const key = `retask-${j.id}`;
  if (j.work_status !== "complete") return `<span class="sub">Once the job is marked done.</span>`;
  if (!may("retainage")) return `<span class="sub">An owner asks for it.</span>`;
  // chase_retainage waits the client's days between contacts after the last request.
  const asked = retainageAskWaits(j);
  if (asked) return `<span class="sub">Asked ${fmtDate(asked.last)}. Ask again from ${fmtDate(asked.due)}.</span>`;
  return `<div class="retainage-ask" data-form-key="${key}">${formErrorLine(key)}<div class="form-inline">
    ${moneyField({ form: key, name: "amount_cents", id: `${key}-amt`, label: "Amount ($), blank for all" })}
    <button class="btn small" data-action="retainage" data-id="${j.id}">Ask for retainage</button></div></div>`;
}

// Everything that owes or owed a lien waiver on a job (M20): each paid pay app, each retainage
// release, and an older job-level one (Earlier payment). {inv | release | legacy, kind, amount,
// due, sent, sent_at, label, onFile, record (the signed waiver that would go out), which (it isn't
// said which pay app it is for, B1), notNeeded (the reason none goes out through Duracash, B2)}.
export function waiverRows(j) {
  const rows = [];
  for (const inv of j.invoices || []) {
    const off = inv.waiver_not_needed;
    if (!inv.waiver_due && !inv.waiver_sent && !off) continue;
    const stage = (inv.waiver_stages && inv.waiver_stages.unconditional) || {};
    rows.push({ inv, label: inv.number, amount: inv.amount_cents - (inv.short_pay_cents || 0), due: !inv.waiver_sent && !off,
      sent_at: inv.waiver_sent_at, kind: inv.waiver_sent ? unesc(inv.waiver_kind || "") : off ? "" : waiverPick(j, inv),
      onFile: stage.status === "Attached", which: stage.status === "Which pay app?", record: stage.record_label || "", recordId: unesc(stage.record_id || ""),
      notNeeded: off ? off.reason || "Not needed" : "" });
  }
  for (const release of j.retainage_waivers || []) {
    const off = release.waiver_not_needed;
    rows.push({ release, label: `Retainage released ${fmtDate(release.at)}`, amount: release.amount_cents, due: !release.waiver_sent && !off,
      sent_at: release.waiver_sent_at, kind: unesc(release.kind || ""), onFile: !!release.on_file, which: !!release.waiver_which,
      record: release.waiver_record_label || "", recordId: unesc(release.waiver_record_id || ""), notNeeded: off ? off.reason || "Not needed" : "" });
  }
  const lw = j.legacy_waiver;   // the older job-level waiver still owed (domain._paperwork_view)
  if (lw) {
    rows.push({ legacy: true, label: "Earlier payment", amount: null, due: true, sent_at: null, kind: waiverPick(j),
      onFile: !!lw.on_file, which: !!lw.which, record: lw.record_label || "", recordId: unesc(lw.record_id || ""), notNeeded: "" });
  } else if (!rows.length && (j.waiver_due || j.waiver_sent || j.waiver_not_needed)) {
    const off = j.waiver_not_needed;
    rows.push({ label: "Earlier payment", amount: null, due: !j.waiver_sent && !off, sent_at: j.waiver_sent_at, kind: "", onFile: true,
      notNeeded: off ? off.reason || "Not needed" : "" });
  }
  return rows;
}

// The row's id: its pay app's or release's, or "job" for the earlier payment (domain.EARLIER_PAYMENT).
function rowId(r) { return r.inv ? r.inv.id : r.release ? r.release.id : "job"; }

// "Take a photo" and "Choose file" for the signed waiver of one row: its type, and the pay app or
// release it is for (B1), so it can't go out for another.
function uploadButtons(j, kind, forId = "") {
  const also = `data-waiver-kind="${kind}"${forId ? ` data-waiver-for="${forId}"` : ""}`;
  return `<div class="form-inline">
    <button class="btn small" data-action="pick-files" data-job="${j.id}" data-kind="waiver" ${also} data-capture="1">Take a photo</button>
    <button class="btn small secondary" data-action="pick-files" data-job="${j.id}" data-kind="waiver" ${also}>Choose file</button>
  </div>`;
}

// A signed waiver is on the job record but not said to be for any pay app while several owe one
// (B1): pick which, then it can be sent. Each row has its own picker (the same waiver can show
// on several rows), and the earlier job-level payment is a choice too.
function waiverForHtml(j, r, rows) {
  const key = `wfor-${r.recordId}-${rowId(r)}`;
  const options = rows.filter(x => x.due && (x.inv || x.release || x.legacy)).map(x =>
    `<option value="${rowId(x)}"${rowId(x) === rowId(r) ? " selected" : ""}>${x.label}</option>`).join("");
  return `<div data-form-key="${key}">${formErrorLine(key)}
    <p class="blocker">${r.record} is on the job record. More than one waiver is due: say which one it is for, then send.</p>
    <div class="form-inline"><select id="${key}" aria-label="Which pay app this signed waiver is for">${options}</select>
    <button class="btn small" data-action="waiver-for" data-id="${j.id}" data-record="${r.recordId}" data-row="${rowId(r)}" data-kind="${r.kind}">This waiver is for it</button></div></div>`;
}

// The way out (B2): no waiver goes out through Duracash for this payment, with the reason, kept
// and shown. The server keeps it in the activity log.
function waiverNotNeededHtml(j, r) {
  const key = `wnn-${r.inv ? r.inv.id : r.release ? r.release.id : "job"}`;
  return `<details class="waiver-not-needed"><summary>Not needed, or handed over outside Duracash…</summary>
    <div data-form-key="${key}">${formErrorLine(key)}<div class="form-row">
      ${field({ form: key, name: "reason", id: `${key}-reason`, label: "Why no waiver goes out through Duracash", placeholder: "Handed the signed form to the owner on site", wide: true })}
      <button class="btn small secondary" data-action="waiver-not-needed" data-id="${j.id}"${r.inv ? ` data-inv="${r.inv.id}"` : ""}${r.release ? ` data-release="${r.release.id}"` : ""}>Mark not needed</button>
    </div></div></details>`;
}

// This module's pages, by the name their address opens (routes.js).
export const views = {
  waiverDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j) return backButton("waivers", "Closeout") + emptyState("Waiver not found", "The job may have been deleted.", `<button class="btn" data-action="nav" data-view="waivers">Go to Closeout</button>`);
    const locked = emailUnconfirmed();
    const rows = waiverRows(j);
    const owed = rows.some(r => r.due);
    const first = rows.find(r => r.due);
    const off = locked || !j.payer.ap_email;
    const payApps = (j.invoices || []).filter(i => i.status !== "void" && i.waiver_stages);
    return `
      ${backButton("waivers", "Closeout")}
      <h1>Closeout: ${j.name}</h1>
      <div class="card">
        <h2>What the client needs</h2>
        <p>${j.waiver_standard || j.payer.packet || "See the Client playbook."}</p>
      </div>
      <div class="card send-check">
        <h2>Waivers due</h2>
        ${owed && j.payer.ap_email ? `<div class="money-row"><span class="label">To</span><span>${j.payer.approver ? j.payer.approver + " · " : ""}${j.payer.ap_email}</span></div>` : ""}
        ${owed && !j.payer.ap_email ? `<p class="blocker">Add the email pay apps go to in <button class="linkish" data-action="nav" data-view="playbook">Client playbook</button>, then send.</p>` : ""}
        ${owed && locked ? confirmFirstHtml("the waiver") : ""}
        ${rows.length ? rows.map(r => `<div class="list-item waiver-row">
            <div><strong>${r.label}</strong>${r.amount != null ? " · " + money(r.amount) : ""} · ${r.due ? `<span class="pill amber">Due</span>` : r.notNeeded ? `<span class="pill">Not needed</span> ${r.notNeeded}` : `sent ${fmtDate(r.sent_at)}${r.kind ? " · " + waiverKindLabel(r.kind) : ""}`}</div>
            ${r.due && !j.summary && r.which && r.recordId && may("waiver") ? waiverForHtml(j, r, rows) : ""}
            ${r.due && !j.summary && !r.onFile && !r.which ? `<p class="blocker">Add the signed ${waiverTypeWords(r.kind)} waiver to the job record, then send.</p>${uploadButtons(j, r.kind, rowId(r))}` : ""}
            ${r.due && r.onFile && r.record ? `<p class="sub">Sends ${r.record} from the job record.</p>` : ""}
            ${r.due ? (may("waiver") ? waiverSendForm(j, r.inv || null, off || !r.onFile, r.release || null) + (j.summary ? "" : waiverNotNeededHtml(j, r)) : OWNER_SENDS) : ""}
          </div>`).join("")
          : `<p class="sub">${termText("No waiver is due yet. One is due after each paid pay app.")}</p>`}
      </div>
      ${first ? `<div class="card">${waiverChecklistHtml(j, first.kind, first.inv || null, first.amount)}</div>` : ""}
      <div class="card">${waiverFormHtml(j, first ? first.kind : "")}</div>
      ${payApps.length ? `<div class="card">
        <h2>${termText("By pay app")}</h2>
        <div class="table-wrap"><table>
          <tr><th>${termText("Pay app")}</th><th>Before payment</th><th>After payment</th></tr>
          ${payApps.map(i => `<tr><td>${i.number}</td><td>${waiverStageText(i.waiver_stages.conditional)}</td><td>${waiverStageText(i.waiver_stages.unconditional)}</td></tr>`).join("")}
        </table></div>
      </div>` : ""}
    `;
  },
  "waiver-detail": function() { return views.waiverDetail(); },
  // Closeout (R6-01): the lien waivers, one row per pay app and per retainage release (M20), and
  // the money held back. /money/retainage opens the same page at the held-back part.
  waivers() {
    // A finished job's waivers and releases are in its job file (J9).
    const jobs = openJobs(state.jobs);
    const rows = jobs.flatMap(j => waiverRows(j).map(r => ({ j, r })));
    rows.sort((a, b) => (b.r.due - a.r.due));
    const open = jobs.filter(j => j.money.retainage_open_cents > 0);
    return `
      <h1>Closeout</h1>
      <section class="card closeout-waivers" aria-labelledby="closeout-waivers-h">
        <h2 id="closeout-waivers-h">Lien waivers</h2>
        <details class="waiver-hints"><summary>Which type?</summary>
          <ul>${Object.entries(WAIVER_KINDS).map(([k, v]) => `<li><strong>${v}.</strong> ${waiverHint(k)}</li>`).join("")}</ul>
        </details>
        ${rows.length ? `<div class="table-wrap">
          <table>
            <tr><th>Job</th><th>${termText("Pay app")}</th><th>Type</th><th>Amount</th><th>Status</th><th>Action</th></tr>
            ${rows.map(({ j, r }) => `<tr>
                <td><a href="${pathFor("waiver-detail", j.id)}" data-action="open-waiver" data-id="${j.id}">${j.name}</a></td>
                <td>${r.label}</td>
                <td>${r.notNeeded ? "—" : r.kind ? waiverKindLabel(r.kind) : "Not chosen yet"}</td>
                <td class="num">${r.amount != null ? money(r.amount) : "—"}</td>
                <td>${r.due ? `<span class="pill amber">Due</span>` : r.notNeeded ? `<span class="pill">Not needed</span>` : `<span class="pill green">Sent</span>`}</td>
                <td>${r.due ? `<button class="btn small" data-action="open-waiver" data-id="${j.id}">Choose type and send</button>` : "—"}</td>
              </tr>`).join("")}
          </table>
        </div>` : emptyState("No waivers due")}
      </section>
      <section class="card closeout-retainage" id="closeout-retainage" aria-labelledby="closeout-retainage-h">
        <h2 id="closeout-retainage-h">Held back, still owed</h2>
        ${open.length ? `<div class="table-wrap">
          <table>
            <tr><th>Job</th><th>Held back</th><th>Done</th><th>Times asked</th><th>Last asked</th><th>Holding it up</th><th>Next</th><th>Contact</th></tr>
            ${open.map(j => {
              const missing = j.closeout_missing || [];   // labels (domain.closeout_missing)
              const f = retainageFacts(j);
              return `<tr>
                <td><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a></td>
                <td class="num">${money(j.money.retainage_open_cents)}</td>
                <td>${f.doneDays != null ? daysAgoText(f.doneDays) : "Not yet"}</td>
                <td class="num">${f.asked}</td>
                <td>${f.last ? `${fmtDate(f.last.toISOString())}<div class="sub row-sub">${daysAgoText(f.askedDays)}</div>` : "—"}</td>
                <td>${missing.length ? missing.join(", ") : (j.work_status === "complete" ? "Closeout paperwork complete" : "Job still in progress")}</td>
                <td>${j.work_status === "complete" && missing.length ? `<button class="btn small" data-action="open-packet" data-job="${j.id}" ${j.closeout_packet && j.closeout_packet.length ? `data-inv="closeout"` : ""}>Open closeout paperwork</button>` : retainageAskHtml(j)}</td>
                <td>${j.payer.approver}<div class="sub row-sub"><a href="mailto:${j.payer.ap_email}">${j.payer.ap_email}</a></div></td>
              </tr>`;
            }).join("")}
          </table>
        </div>` : emptyState("Nothing held back")}
      </section>
    `;
  },
  retainage() { return views.waivers(); },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  // A person approves each waiver (M21): the confirm names the type, what it covers and who gets it.
  "waiver": async (el, id) => {
    const key = el.dataset.release || el.dataset.inv || "job";
    const kind = document.getElementById(`waiver-kind-${key}`)?.value;
    const j = state.jobs.find(x => x.id === id);
    if (j) {
      const row = waiverRows(j).find(r => r.due && rowId(r) === key) || waiverRows(j).find(r => r.due);
      const what = row ? `${unesc(row.label)}${row.amount != null ? ", " + money(row.amount) : ""}` : j.name;
      if (!(await askConfirm(`Email the signed ${waiverTypeWords(kind || (row && row.kind) || "")} waiver for ${what} to ${sendsTo(j)}?`, "Send the waiver"))) return;
    }
    await post(`/api/jobs/${id}/waiver`, { ...(el.dataset.inv ? { invoice_id: el.dataset.inv } : {}), ...(el.dataset.release ? { release_id: el.dataset.release } : {}), ...(kind ? { kind } : {}) });
  },
  // All of the retainage, or the part typed in the box, once the job is done (M24).
  "retainage": async (el, id) => {
    const j = state.jobs.find(x => x.id === id);
    const key = `retask-${id}`, form = document.querySelector(`[data-form-key="${key}"]`);
    const open = j ? j.money.retainage_open_cents : 0;
    let cents = null;
    if (form) {
      const typed = String(formValues(key).amount_cents || "").trim();
      const a = centsFrom(typed);
      if (typed && (a.error || a.cents <= 0)) { refuseForm(key, { amount_cents: "Enter the retainage to ask for in dollars, like 5,000." }); return; }
      if (typed && a.cents > open) { refuseForm(key, { amount_cents: `Enter no more than the ${money(open)} held.` }); return; }
      if (typed) cents = a.cents;
    }
    const part = cents != null && cents < open;
    if (j && !(await askConfirm(`Email a request for ${part ? `${money(cents)} of the ${money(open)}` : `the ${money(open)}`} retainage to ${sendsTo(j)}?`, "Send the request"))) return;
    await post(`/api/jobs/${id}/retainage`, cents != null ? { amount_cents: cents } : {}, form ? { formKey: key } : {});
  },
  "open-waiver": (el, id) => nav("waiver-detail", id),
  // Say which paid pay app or release a signed waiver is for (B1), keeping its type.
  "waiver-for": async (el, id) => {
    const key = `wfor-${el.dataset.record}-${el.dataset.row}`;
    const forId = document.getElementById(key)?.value || "";
    await post(`/api/jobs/${id}/waiver-type/${el.dataset.record}`, { kind: el.dataset.kind, for_id: forId }, { formKey: key });
  },
  // No waiver through Duracash for this payment (B2): a reason is needed, and it is kept.
  "waiver-not-needed": async (el, id) => {
    const key = `wnn-${el.dataset.inv || el.dataset.release || "job"}`;
    const reason = String(formValues(key).reason || "").trim();
    if (!reason) { refuseForm(key, { reason: "Enter why no waiver goes out through Duracash, like handed to the owner on site." }); return; }
    await post(`/api/jobs/${id}/waiver-not-needed`, { reason, ...(el.dataset.inv ? { invoice_id: el.dataset.inv } : {}), ...(el.dataset.release ? { release_id: el.dataset.release } : {}) },
      { formKey: key });
  },
};
