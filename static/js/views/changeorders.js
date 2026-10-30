// Money, Change orders: the list with its New change order form, and each change order's page.
import { unesc, money } from "../format.js";
import { coSignature, coStatusWords, defaultJobId } from "../jobs.js";
import { pathFor } from "../routes.js";
import { askConfirm } from "../dom.js";
import { field, formErrorLine, refuseForm, formValues } from "../forms.js";
import { termText } from "../terms.js";
import { state, currentJobId, currentDetailId, formOpen, onSessionEnd } from "../state.js";
import { may, render, toast, post, backButton, emptyState, nav } from "../app-core.js";
import { coFormFields, coSignControls, addChangeOrder } from "./job-cos.js";

let coPreselectJob = null;
// Signed out: a job picked for a new change order is that company's.
onSessionEnd(() => { coPreselectJob = null; });

// This module's pages, by the name their address opens (routes.js).
export const views = {
  coDetail() {
    const j = state.jobs.find(x => x.id === currentJobId);
    if (!j) return backButton("cos", "Change orders") + emptyState("Change order not found", "The job may have been deleted.", `<button class="btn" data-action="nav" data-view="cos">Go to Change orders</button>`);
    const co = j.change_orders.find(c => c.id === currentDetailId) || j.change_orders[j.change_orders.length - 1];
    if (!co) {
      return `
        ${backButton("cos", "Change orders")}
        <h1>New change order</h1>
        <div data-form-key="newco">${formErrorLine("newco")}<div class="form-row">
          ${coFormFields()}
          <button class="btn" data-action="add-co" data-id="${j.id}">Create</button>
        </div></div>`;
    }
    return `
      ${backButton("cos", "Change orders")}
      <h1>CO ${co.number}: ${co.title}</h1>
      <p class="sub"><a href="${pathFor("job", j.id)}" data-action="open-job" data-id="${j.id}">${j.name}</a> · ${money(co.amount_cents)} · <span class="pill ${co.status === "approved" ? "green" : "amber"}">${coStatusWords(co.status)}</span></p>
      <div class="card">
        <h2>Summary</h2>
        ${co.description ? `<p>${co.description}</p>` : ""}
        <div class="money-row"><span class="label">Waiting on</span><span>${co.waiting_on || (co.status === "pending" ? "Client signature" : "Nothing")}</span></div>
        <div class="money-row"><span class="label">${termText("On a pay app")}</span><span>${co.included_in_billing ? "Yes" : "No"}</span></div>
        ${coSignature(co)}
        ${coSignControls(j, co)}
      </div>
    `;
  },
  "co-detail": function() { return views.coDetail(); },
  cos() {
    const cos = [];
    state.jobs.forEach(j => j.change_orders.forEach(co => cos.push({ ...co, job: j.name, jobId: j.id })));
    const openJobs = state.jobs.filter(j => !j.closed);
    return `
      <h1>Change orders</h1>
      ${openJobs.length ? `<div class="card" data-form-key="newco">
        <h2>New change order</h2>
        ${formErrorLine("newco")}
        <div class="form-grid">
          ${field({ form: "newco", name: "job", id: "new-co-job", label: "Job", value: unesc(defaultJobId(openJobs, coPreselectJob || "")), options: [["", "Pick a job…"], ...openJobs.map(j => [unesc(j.id), unesc(j.name)])] })}
          ${coFormFields()}
        </div>
        <button class="btn" data-action="add-co-from-list">Create change order</button>
      </div>` : ""}
      ${cos.length ? `<div class="table-wrap">
        <table>
          <tr><th>Change order</th><th>Job</th><th>Amount</th><th>Status</th><th>Waiting on</th><th>${termText("On a pay app")}</th></tr>
          ${cos.slice().reverse().map(c => `<tr>
            <td><a href="${pathFor("co-detail", c.jobId, c.id)}" data-action="open-co" data-job="${c.jobId}" data-co="${c.id}">CO ${c.number}: ${c.title}</a>${c.description ? `<div class="sub row-sub">${c.description.slice(0, 80)}</div>` : ""}</td>
            <td>${c.job}</td>
            <td class="num">${money(c.amount_cents)}</td>
            <td><span class="pill ${c.status === "approved" ? "green" : c.status === "withdrawn" ? "" : "amber"}">${coStatusWords(c.status)}</span></td>
            <td>${c.waiting_on || (c.status === "pending" ? "Signature" : "—")}</td>
            <td>${c.included_in_billing ? "Yes" : "No"}</td>
          </tr>`).join("")}
        </table>
      </div>` : emptyState("No change orders", "", openJobs.length ? "" : `<button class="btn" data-action="onb-newjob">New job</button>`)}
    `;
  },
};

// What this module's buttons do: ACTIONS[name](el, el.dataset.id, event), merged in views/index.js.
export const actions = {
  "withdraw-co": async (el, id) => {
    if (!(await askConfirm("Withdraw this change order? Use this when the client will not sign it.", "Withdraw", true))) return;
    await post(`/api/jobs/${id}/withdraw-co/${el.dataset.co}`);
  },
  "add-co": async (el, id) => {
    const r = await addChangeOrder(id);
    if (r && r.ok) nav("co-detail", id);
  },
  "goto-co": (el, id) => { coPreselectJob = id; nav("cos"); },
  "add-co-from-list": async () => {
    const jobId = formValues("newco").job;
    if (!jobId) { refuseForm("newco", { job: "Pick a job." }); return; }
    const r = await addChangeOrder(jobId);
    if (r && r.ok) toast(`Change order created on ${unesc((state.jobs.find(j => j.id === jobId) || {}).name || "the job")}. It stays unbilled until signed.`);
  },
  "open-co": (el) => nav("co-detail", el.dataset.job, el.dataset.co),
  "approve-co": async (el, id) => {
    const recordId = document.getElementById(`cosign-rec-${el.dataset.co}`)?.value || "";
    if (!recordId) { toast("Choose the record that holds the signed copy, or take a photo of it."); return; }
    const r = await post(`/api/jobs/${id}/approve-co/${el.dataset.co}`, { record_id: recordId });
    if (r && r.ok) { formOpen[`cosign-${el.dataset.co}`] = false; render(); }
  },
  "request-co-signature": (el, id) => post(`/api/jobs/${id}/request-co-signature/${el.dataset.co}`),
};
