// Field rules (G1): phone, ZIP, state, email, licence and contract numbers, names and address
// parts. domain/validate.py has the same rules, so a box is marked when it is left and the
// server refuses the same values; tests/validate_cases.txt is the table both are tested on.
// A form box takes a rule with field({ rule: "phone" }); forms.js checks it on blur.

export const US_STATES = [
  "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY",
  "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND",
  "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
  "DC", "PR", "VI", "GU", "AS", "MP",
];

const PHONE_RE = /^\s*(?:\+?1[\s.-]*)?\(?(\d{3})\)?[\s.-]*(\d{3})[\s.-]*(\d{4})\s*(?:(?:x|ext\.?|extension)\s*(\d{1,6}))?\s*$/i;
const ZIP_RE = /^\d{5}(?:-\d{4})?$/;
// Contract and licence numbers as GCs write them: "PO# 4500-12", "SC_2026_014", "2026-014 (Rev 1)".
const NUMBER_RE = /^[A-Za-z0-9][A-Za-z0-9 ./#_&()-]*$/;
// A name, street or city has a letter (any alphabet) or a digit, as the server's [^\W_].
const HAS_WORD_RE = /[\p{L}\p{N}]/u;
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

// rule -> [the message when it fails, the longest it may be]
export const FIELD_RULES = {
  phone: ["Enter a 10-digit phone number, like (404) 555-0123.", 40],
  zip: ["Enter a 5-digit ZIP code, like 30303, or ZIP+4, like 30303-1234.", 10],
  state: ["Enter the two-letter state, like GA.", 3],
  email: ["Enter an email address, like name@example.com.", 254],
  licence: ["Use letters, numbers, spaces and - / . # _ & ( ) only, up to 40.", 40],
  contract_number: ["Use letters, numbers, spaces and - / . # _ & ( ) only, up to 40.", 40],
  name: ["Enter a name with at least one letter or number, up to 120.", 120],
  street: ["Enter the street address, up to 120 characters, like 120 Peachtree St NE.", 120],
  city: ["Enter the city.", 60],
};

// A US phone as (xxx) xxx-xxxx (with " x123" for an extension), or null.
export function normalizePhone(value) {
  const m = PHONE_RE.exec(String(value ?? ""));
  if (!m) return null;
  return `(${m[1]}) ${m[2]}-${m[3]}` + (m[4] ? ` x${m[4]}` : "");
}

export function normalizeState(value) {
  const code = String(value ?? "").trim().replace(/\.$/, "").toUpperCase();
  return US_STATES.includes(code) ? code : null;
}

function passes(rule, v) {
  if (rule === "phone") return normalizePhone(v) !== null;
  if (v.length > FIELD_RULES[rule][1]) return false;
  if (rule === "zip") return ZIP_RE.test(v);
  if (rule === "state") return normalizeState(v) !== null;
  if (rule === "email") return EMAIL_RE.test(v);
  if (rule === "licence" || rule === "contract_number") return NUMBER_RE.test(v);
  return HAS_WORD_RE.test(v);   // name, street, city
}

// The message for a value that breaks the rule, else "". Blank passes unless required.
export function validate(rule, value, required = false) {
  if (!FIELD_RULES[rule]) return "";
  const v = String(value ?? "").trim();
  if (!v) return required ? FIELD_RULES[rule][0] : "";
  return passes(rule, v) ? "" : FIELD_RULES[rule][0];
}

// {field: message} for a form's values, by {field: rule}, the shape refuseForm takes.
export function checkFields(values, rules, required = []) {
  const out = {};
  for (const [field, rule] of Object.entries(rules)) {
    if (!(field in values) && !required.includes(field)) continue;
    const msg = validate(rule, values[field], required.includes(field));
    if (msg) out[field] = msg;
  }
  return out;
}

// What a box shows once left: a phone in (xxx) xxx-xxxx, a state in capitals; else as typed.
export function tidy(rule, value) {
  if (rule === "phone") return normalizePhone(value) || value;
  if (rule === "state") return normalizeState(value) || value;
  return value;
}

// Addresses: "Street, City, ST 12345" (domain.compose_address / split_address).
const ADDRESS_RE = /^\s*(.+?)\s*,\s*([^,]+?)\s*,?\s+([A-Za-z]{2})\.?(?:\s+(\d{5}(?:-\d{4})?))?\s*$/;

export function composeAddress(parts) {
  const street = String(parts.street ?? "").trim(), city = String(parts.city ?? "").trim();
  const state = (normalizeState(parts.state) || String(parts.state ?? "").trim()).toUpperCase();
  const tail = [state, String(parts.zip ?? "").trim()].filter(Boolean).join(" ");
  return [street, city, tail].filter(Boolean).join(", ");
}

export function splitAddress(text) {
  const m = ADDRESS_RE.exec(String(text ?? "").trim().replace(/\s*\n\s*/g, ", "));
  if (!m || !normalizeState(m[3])) return null;
  return { street: m[1], city: m[2], state: m[3].toUpperCase(), zip: m[4] || "" };
}

// A one-line address in parts for the four boxes; unreadable ones go whole in the street box.
export function parseLegacyAddress(text) {
  return splitAddress(text) || { street: String(text ?? "").trim(), city: "", state: "", zip: "" };
}
