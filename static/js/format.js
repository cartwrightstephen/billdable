// Formatting and escaping: money, dates, status words. Every value from the server is HTML-escaped once on arrival (escDeep); unesc gives back the raw text.

// Every string that comes from the server is HTML-escaped once, when it
// arrives. Templates below interpolate into innerHTML, so this is what keeps
// a job named <img onerror=...> from running as code. Never interpolate raw
// user input; wrap it in esc().
export function esc(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// The reverse of esc(), for values from escDeep'd state that are about to be escaped again
// (or placed with textContent). Never put its result into innerHTML without esc().
export function unesc(value) {
  const t = document.createElement("textarea");
  t.innerHTML = String(value);
  return t.value;
}

export function escDeep(value) {
  if (typeof value === "string") return esc(value);
  if (Array.isArray(value)) return value.map(escDeep);
  if (value && typeof value === "object") {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = escDeep(v);
    return out;
  }
  return value;
}

export const money = (cents) => {
  if (cents == null) return "$0.00";
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  return `${sign}$${(abs / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

// ---------- dates and words people read ----------
// Server values arrive escaped; dates are plain ISO strings, so unesc() is only a safety net.
export function toDate(iso) {
  const raw = unesc(iso || "");
  if (!raw) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) { const [y, m, d] = raw.split("-").map(Number); return new Date(y, m - 1, d); }
  const t = Date.parse(raw);
  return isNaN(t) ? null : new Date(t);
}

export function fmtDate(iso) {
  const d = toDate(iso);
  return d ? d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "—";
}

export function fmtDateTime(iso) {
  const d = toDate(iso);
  if (!d) return "—";
  if (/^\d{4}-\d{2}-\d{2}$/.test(unesc(iso))) return fmtDate(iso);
  return d.toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}

// "in_progress" -> "In progress"; the result is escaped again because it goes into HTML.
export function words(code) {
  const text = unesc(code || "").replace(/_/g, " ").trim();
  return esc(text.charAt(0).toUpperCase() + text.slice(1));
}

export const INVOICE_WORDS = { draft: "Draft", awaiting_approval: "Ready to send", sent: "Sent", approved: "Approved", partial: "Partly paid", paid: "Paid", void: "Void" };

export function invoicePill(inv) {
  const st = unesc(inv.status || "");
  const colour = st === "paid" ? "green" : ["sent", "approved", "partial"].includes(st) ? "amber" : st === "void" ? "" : "copper";
  return `<span class="pill ${colour}">${INVOICE_WORDS[st] || words(st)}</span>`;
}

export function companyLine(c) {
  return [c.crew ? `${c.crew} people` : "", c.books || ""].filter(Boolean).join(" · ");
}

// One line for the toast after an action that wrote to the Outbox.
export function emailOutcome(entries) {
  return entries.map(e => {
    const to = unesc(e.to), status = unesc(e.status || "");
    if (status === "sent") return `Emailed ${to}.`;
    if (status.startsWith("failed")) return `Saved, but the email to ${to} ${status.replace(/^failed: /, "failed: ")}. Open the Outbox to send it again.`;
    if (status.startsWith("not sent")) return `Saved. The email to ${to} was ${status}.`;
    return `Saved. Email to ${to}: ${status}.`;
  }).join(" ");
}

export function emailStatusHtml(e) {
  const st = e.status || "";
  const cls = st === "sent" ? "green" : (st.startsWith("failed") || st.startsWith("bounced")) ? "red" : st.startsWith("not sent") ? "amber" : "";
  const when = st === "sent" && e.delivered_at ? `<div class="sub">${fmtDateTime(e.delivered_at)}</div>` : "";
  // A row still "sending" or "queued" after 30 minutes died mid-send; the server allows a retry then.
  const since = Date.parse(e.sending_at || e.at || "") || Date.now();
  const stuck = (st === "sending" || st === "queued") && Date.now() - since > 30 * 60 * 1000;
  const retry = (st.startsWith("failed") || st.startsWith("not sent") || stuck) ? ` <button class="btn small secondary" data-action="email-retry" data-email="${e.id}">Send again</button>` : "";
  return `<span class="pill ${cls}">${words(st)}</span>${when}${retry}`;
}

export function helperFormat(text) {
  // text is escaped first; then "- " lines become a list, blank lines paragraphs.
  const lines = esc(text || "").split("\n");
  let html = "", inList = false;
  for (const line of lines) {
    if (line.startsWith("- ")) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${line.slice(2)}</li>`;
    } else {
      if (inList) { html += "</ul>"; inList = false; }
      html += line.trim() ? `<p>${line}</p>` : "";
    }
  }
  return html + (inList ? "</ul>" : "");
}
