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
// Without a page (node --test) it decodes what esc() writes plus numeric references, in one pass
// so "&amp;lt;" comes back as "&lt;", the way the browser decodes it.
const NAMED = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: "\u00a0" };
export function unesc(value) {
  if (typeof document === "undefined") {
    return String(value).replace(/&(?:#(\d+)|#x([0-9a-f]+)|([a-z]+));/gi, (m, dec, hex, name) => {
      const code = dec ? parseInt(dec, 10) : hex ? parseInt(hex, 16) : null;
      if (code !== null) return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : m;
      return NAMED[name.toLowerCase()] ?? m;
    });
  }
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

// Always US style, like domain.money, the pay app PDF and the emails, whatever the browser's locale.
export const money = (cents) => {
  if (cents == null) return "$0.00";
  const sign = cents < 0 ? "-" : "";
  const abs = Math.abs(cents);
  return `${sign}$${(abs / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

// ---------- numbers people type ----------
// "$12,500.50", "12500.5" and " 12,500 " are all the same amount. plainNumber drops what people
// type around a number ($ , % and spaces); sovNum (sov.js) and the parsers below build on it.
export const plainNumber = (v) => String(v ?? "").replace(/[$,\s%]/g, "");

// Dollars typed into a form, as whole cents, without floating-point drift:
// {cents, error}. Blank and anything that isn't an amount come back with an error to show.
export function centsFrom(text) {
  const t = plainNumber(text);
  if (!t) return { cents: null, error: "Enter an amount in dollars, like 12,500.50." };
  if (/^-?\d*\.\d{3,}$/.test(t)) return { cents: null, error: "Use no more than two digits after the point." };
  if (!/^-?(\d+\.?\d{0,2}|\.\d{1,2})$/.test(t)) return { cents: null, error: "Enter an amount in dollars, like 12,500.50." };
  const negative = t.startsWith("-");
  const [whole, frac = ""] = t.replace("-", "").split(".");
  const cents = Number(whole || "0") * 100 + Number((frac + "00").slice(0, 2));
  if (!Number.isSafeInteger(cents)) return { cents: null, error: "That amount is too large." };
  return { cents: negative ? -cents : cents, error: "" };
}

// A percent typed into a form ("5", "5.5", "5 %"), with at most one decimal: {value, error}.
export function percentFrom(text) {
  const t = plainNumber(text);
  if (!t) return { value: null, error: "Enter a percent, like 5 or 7.5." };
  if (!/^(\d+\.?\d?|\.\d)$/.test(t)) return { value: null, error: "Enter a percent, like 5 or 7.5." };
  return { value: Number(t), error: "" };
}

// Cents as a person types them back: "$48,500.00", always US style so centsFrom reads it again.
export function dollarsText(cents) {
  if (cents == null) return "";
  const sign = cents < 0 ? "-" : "";
  return `${sign}$${(Math.abs(cents) / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

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

export const INVOICE_WORDS = { draft: "Draft", awaiting_approval: "Ready to send", sent: "Sent", approved: "Approved", partial: "Partly paid", paid: "Paid", void: "Cancelled" };

export function invoicePill(inv) {
  const st = unesc(inv.status || "");
  const colour = st === "paid" ? "green" : ["sent", "approved", "partial"].includes(st) ? "amber" : st === "void" ? "" : "copper";
  // The last pay app on a finished job (R4-04) says so beside its status.
  return `<span class="pill ${colour}">${INVOICE_WORDS[st] || words(st)}</span>${inv.final && st !== "void" ? ` <span class="pill">Final</span>` : ""}`;
}

export function companyLine(c) {
  return [c.crew ? `${c.crew} people` : "", c.books || ""].filter(Boolean).join(" · ");
}

// One line for the toast after an action that wrote to the Outbox.
export function emailOutcome(entries) {
  return entries.map(e => {
    const to = unesc(e.to), status = unesc(e.status || "");
    if (status === "sent") return `Emailed ${to}.${refusedCopies(e) ? " " + unesc(refusedCopies(e)) + "." : ""}`;
    if (status.startsWith("failed")) return `Saved, but the email to ${to} ${status.replace(/^failed: /, "failed: ")}. Open the Outbox to send it again.`;
    if (status.startsWith("not sent")) return `Saved. The email to ${to} was ${status}.`;
    return `Saved. Email to ${to}: ${status}.`;
  }).join(" ");
}

// Copy addresses the mail server refused on an email that reached its To address (escaped, as it arrived).
function refusedCopies(e) {
  const r = e.refused || [];
  return r.length ? `Not delivered to the copy address${r.length === 1 ? "" : "es"} ${r.join(", ")}` : "";
}

export function emailStatusHtml(e) {
  const st = e.status || "";
  const cls = st === "sent" ? "green" : (st.startsWith("failed") || st.startsWith("bounced")) ? "red" : st.startsWith("not sent") ? "amber" : "";
  const when = (st === "sent" && e.delivered_at ? `<div class="sub">${fmtDateTime(e.delivered_at)}</div>` : "")
    + (refusedCopies(e) ? `<div class="sub">${refusedCopies(e)}</div>` : "");
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
