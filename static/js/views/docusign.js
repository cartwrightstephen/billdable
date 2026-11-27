// DocuSign (the server's docusign.py): the connection card on Settings, Company, and "Sign with
// DocuSign" beside the typed-name signature on a pay app, a change order and a state's waiver form.
// Both show for everyone (founders' decision, Nov 27). Before the server has its DocuSign keys, or
// before an owner has connected it, pressing either says so in a short note right there and points
// to the typed-name signature; nothing is ever marked signed in DocuSign that wasn't.
import { fmtDate, escDeep } from "../format.js";
import { readJson, setNetBanner, postJson } from "../net.js";
import { askConfirm } from "../dom.js";
import { formValues } from "../forms.js";
import { me, onSessionEnd } from "../state.js";
import { render, toast, can, load } from "../app-core.js";

export let docusignStatus = null;   // /api/docusign/status, escaped: configured, connected, account_name, waiting
let asked = false;
let note = null;   // {key, message}: the note shown under the button that was pressed
onSessionEnd(() => { docusignStatus = null; asked = false; note = null; });

const SWITCHING_ON = "DocuSign is being switched on for your account. We'll email you when it's ready. Until then, sign with your typed name \u2014 it's legally binding.";

// Which button a note belongs to: what it signs, from its data- attributes.
function noteKey(d) { return [d.kind, d.id, d.inv || d.co || d.for || "", d.which || ""].join("|"); }
function attrsOf(attrs) {
  const out = {};
  for (const [, k, v] of String(attrs).matchAll(/data-([a-z-]+)="([^"]*)"/g)) out[k.replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v;
  return out;
}
const escText = (t) => String(t || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// The typed-name way, offered right under the note.
function typedOffer(d) {
  if (d.kind === "payapp") return `<button class="btn small" data-action="docusign-typed" data-focus="approve-${d.inv || ""}-signature">Sign with your typed name</button>`;
  if (d.kind === "co") return `<span class="sub">Until then, Request signature sends your client a page where they sign with their typed name.</span>`;
  if (d.kind === "waiver") return `<span class="sub">Until then, fill in the form, sign it and file the signed copy on the job.</span>`;
  return "";
}
function noteHtml(message, offer = "") {
  // A span (display: block in styles.css): the waiver's note sits inside a <p>.
  return `<span class="docusign-note" role="status"><span class="sub">${escText(message)}</span> ${offer}</span>`;
}
export function setDocusignNote(key, message) { note = key ? { key, message } : null; }

// What the signer reads on coming back from DocuSign's signing page (?docusign=<event>).
const BACK_WORDS = {
  signing_complete: "Signed in DocuSign. It shows as signed as soon as DocuSign has finished it.",
  cancel: "Not signed. Press Sign with DocuSign again when you are ready.",
  decline: "You declined to sign in DocuSign. Nothing was signed.",
  session_timeout: "The DocuSign page timed out. Nothing was signed.",
  ttl_expired: "That DocuSign link was used or expired. Press Sign with DocuSign again.",
};

export function backFromDocusign(search) {
  const event = new URLSearchParams(search || "").get("docusign");
  if (!event) return "";
  return BACK_WORDS[event] || "DocuSign didn't finish the signing. Try again.";
}

function sayWhereFrom() {
  if (typeof location === "undefined") return;
  const words = backFromDocusign(location.search);
  if (!words) return;
  try { history.replaceState(history.state, "", location.pathname); } catch (_) { /* the words still show */ }
  toast(words);
}

// For the page tests, as setWaiverForms: the status as if the server had answered.
export function setDocusignStatus(data) { docusignStatus = data; asked = true; }

export async function loadDocusignStatus() {
  asked = true;
  const who = me;
  try {
    const res = await fetch("/api/docusign/status");
    const data = res.ok ? await readJson(res) : null;
    if (me !== who) return;
    docusignStatus = data ? escDeep(data) : { configured: false, connected: false };
  } catch (_) { if (me === who) asked = false; setNetBanner(true); return; }
  sayWhereFrom();
  render();
}

// Connected: the Sign step offers DocuSign. Asked once per session; a redraw follows the answer.
export function docusignReady() {
  if (!docusignStatus && !asked && typeof fetch === "function" && me) loadDocusignStatus();
  return !!(docusignStatus && docusignStatus.configured && docusignStatus.connected);
}

// The button beside a typed-name signature; attrs say what is signed (data-kind and its ids).
// Shown to everyone who may sign; the server says whether DocuSign is ready when it is pressed.
export function docusignButton(attrs) {
  docusignReady();
  const d = attrsOf(attrs);
  const mine = note && note.key === noteKey(d);
  return `<button class="btn small secondary" data-action="docusign-sign" ${attrs}>Sign with DocuSign</button>${mine ? noteHtml(note.message, typedOffer(d)) : ""}`;
}

// Beside a state's waiver form (paperwork.js filledFormLink): the same form, signed in DocuSign and
// filed on the job's records as a lien waiver of this type, for this pay app or release.
export function docusignWaiver(j, kind, forId, final) {
  return docusignButton(`data-kind="waiver" data-id="${j.id}" data-for="${forId || ""}" data-which="${final ? "final" : "interim"}" data-waiver-kind="${kind || ""}"`);
}

const KIND_WORDS = { payapp: "Pay app", co: "Change order", waiver: "Lien waiver" };

// Always shown, as the QuickBooks card is; Connect before the server has its keys says so.
export function docusignCardHtml() {
  docusignReady();
  const d = docusignStatus;
  const owner = can("company");
  if (!d || !d.configured || !d.connected) {
    const said = note && note.key === "connect" ? noteHtml(note.message) : "";
    return `<div class="card docusign-card"><h2>DocuSign eSignature</h2>
      <p class="sub">Sign pay apps, lien waivers and change orders with DocuSign. The signed copy and DocuSign's certificate go on the job's records.</p>${owner
      ? `<button class="btn" data-action="docusign-connect">Connect DocuSign</button>${said}`
      : `<p class="sub">An owner of this company can connect DocuSign here.</p>`}</div>`;
  }
  const waiting = d.waiting || [];
  return `<div class="card docusign-card"><h2>DocuSign eSignature</h2>
    <p class="sub"><span class="pill green">Connected</span> ${d.account_name ? d.account_name + ". " : ""}${d.environment === "demo" ? "Demo account, for trying it out. " : ""}Since ${fmtDate(d.connected_at)}${d.connected_by ? ", by " + d.connected_by : ""}.</p>
    <p class="sub">Change orders: ${d.co_client_signs ? "you sign, then DocuSign emails the client's approver to sign. Their signature approves it." : "you sign in DocuSign; the client signs with Request signature or on paper."}</p>
    ${owner ? `<div class="form-inline"><button class="btn small secondary" data-action="docusign-co-client" data-on="${d.co_client_signs ? "" : "1"}" aria-pressed="${!!d.co_client_signs}">${d.co_client_signs ? "Stop asking clients to sign in DocuSign" : "Ask clients to sign change orders in DocuSign too"}</button>
      <button class="btn small secondary" data-action="docusign-disconnect">Disconnect</button></div>` : ""}
    ${waiting.length ? `<h3>Waiting in DocuSign</h3>${waiting.map(e => `<div class="list-item"><span class="pill amber">${KIND_WORDS[e.kind] || "Document"}</span> ${e.label || ""} · sent ${fmtDate(e.created_at)}${e.user_name ? " by " + e.user_name : ""} <button class="btn small secondary" data-action="docusign-check" data-envelope="${e.id}">Check DocuSign</button></div>`).join("")}` : ""}
  </div>`;
}

export const actions = {
  "docusign-connect": async () => {
    const r = await postJson("/api/docusign/connect", {});
    if (r.ok && r.data.pending) { note = { key: "connect", message: r.data.message || SWITCHING_ON }; render(); return; }
    if (!r.ok) { note = { key: "connect", message: r.data.error || "Couldn't start the DocuSign connection." }; render(); return; }
    if (typeof r.data.url === "string" && r.data.url.startsWith("https://")) location.assign(r.data.url);
  },
  // From the note: the typed-name signature beside it.
  "docusign-typed": (el) => {
    note = null;
    render();
    const box = document.getElementById(el.dataset.focus || "");
    if (box && typeof box.focus === "function") box.focus();
  },
  "docusign-disconnect": async () => {
    if (!(await askConfirm("Disconnect DocuSign? Signing goes back to typed names. Documents already signed stay on the job's records.", "Disconnect"))) return;
    const r = await postJson("/api/docusign/disconnect", {});
    if (!r.ok) { toast(r.data.error || "Couldn't disconnect DocuSign."); return; }
    docusignStatus = escDeep(r.data);
    render();
  },
  "docusign-co-client": async (el) => {
    const r = await postJson("/api/docusign/settings", { co_client_signs: el.dataset.on === "1" });
    if (!r.ok) { toast(r.data.error || "Couldn't save that."); return; }
    docusignStatus = escDeep(r.data);
    render();
  },
  "docusign-check": async (el) => {
    const r = await postJson("/api/docusign/check", { envelope_id: el.dataset.envelope || "" });
    if (!r.ok) { toast(r.data.error || "Couldn't ask DocuSign."); return; }
    docusignStatus = escDeep(r.data);
    toast(r.data.status === "completed" ? "Signed. It is on the job's records." : r.data.status === "declined" ? "Declined in DocuSign." : "Still waiting in DocuSign.");
    if (r.data.status === "completed") await load();
    render();
  },
  // The envelope is made on the server; this page goes to DocuSign's signing page and comes back.
  "docusign-sign": async (el) => {
    const d = el.dataset;
    const body = { kind: d.kind, job_id: d.id || "" };
    if (d.kind === "payapp") Object.assign(body, { invoice_id: d.inv || "", stamp: d.stamp || "", title: String(formValues(`approve-${d.inv || ""}`).title || "").trim() });
    if (d.kind === "co") body.co_id = d.co || "";
    if (d.kind === "waiver") Object.assign(body, { for_id: d.for || "", which: d.which || "interim", waiver_kind: d.waiverKind || "" });
    el.disabled = true;
    const r = await postJson("/api/docusign/sign", body);
    el.disabled = false;
    // Not switched on yet, or not connected: say so right there and offer the typed name. Never "signed".
    if (r.ok && r.data.pending) { note = { key: noteKey(d), message: r.data.message || SWITCHING_ON }; render(); return; }
    if (!r.ok && !r.data.review_changed && r.status === 409) { note = { key: noteKey(d), message: r.data.error || "Couldn't open DocuSign." }; render(); return; }
    if (!r.ok) { toast(r.data.error || "Couldn't open DocuSign."); if (r.data.review_changed) { await load(); render(); } return; }
    if (typeof r.data.url === "string" && r.data.url.startsWith("https://")) location.assign(r.data.url);
  },
};

// Back from DocuSign on a page that doesn't draw a Sign step: the words still show.
// Waits for the sign-in check (me), up to ten seconds.
if (typeof location !== "undefined" && typeof setTimeout === "function" && backFromDocusign(location.search)) {
  let tries = 0;
  const once = () => {
    if (docusignStatus) sayWhereFrom();
    else if (me && !asked) loadDocusignStatus();
    else if (tries++ < 20) setTimeout(once, 500);
  };
  setTimeout(once, 0);
}
