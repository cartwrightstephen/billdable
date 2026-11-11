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
  if (!m || new Set(m[1] + m[2] + m[3]).size === 1) return null;   // 0000000000 is nobody's number
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

// The state(s) a ZIP code belongs to, by its first three digits (USPS prefix ranges), for the
// question asked when a ZIP doesn't match the state typed (Stephen, 10 Nov). null: a prefix not
// listed (military mail, unassigned), which is never questioned.
const ZIP3 = [
  [5, 5, "NY"], [6, 7, "PR"], [8, 8, "VI"], [9, 9, "PR"], [10, 27, "MA"], [28, 29, "RI"], [30, 38, "NH"],
  [39, 49, "ME"], [50, 54, "VT"], [55, 55, "MA"], [56, 59, "VT"], [60, 69, "CT"], [70, 89, "NJ"],
  [100, 149, "NY"], [150, 196, "PA"], [197, 199, "DE"], [200, 200, "DC"], [201, 201, "VA"], [202, 205, "DC"],
  [206, 219, "MD"], [220, 246, "VA"], [247, 268, "WV"], [270, 289, "NC"], [290, 299, "SC"], [300, 319, "GA"],
  [320, 339, "FL"], [341, 349, "FL"], [350, 369, "AL"], [370, 385, "TN"], [386, 397, "MS"], [398, 399, "GA"],
  [400, 427, "KY"], [430, 459, "OH"], [460, 479, "IN"], [480, 499, "MI"], [500, 528, "IA"], [530, 549, "WI"],
  [550, 567, "MN"], [569, 569, "DC"], [570, 577, "SD"], [580, 588, "ND"], [590, 599, "MT"], [600, 629, "IL"],
  [630, 658, "MO"], [660, 679, "KS"], [680, 693, "NE"], [700, 714, "LA"], [716, 729, "AR"], [730, 732, "OK"],
  [733, 733, "TX"], [734, 749, "OK"], [750, 799, "TX"], [800, 816, "CO"], [820, 831, "WY"], [832, 838, "ID"],
  [840, 847, "UT"], [850, 865, "AZ"], [870, 884, "NM"], [885, 885, "TX"], [889, 898, "NV"], [900, 961, "CA"],
  [967, 968, "HI"], [969, 969, ["GU", "MP", "AS"]], [970, 979, "OR"], [980, 994, "WA"], [995, 999, "AK"],
];
const STATE_NAMES = { AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California", CO: "Colorado", CT: "Connecticut",
  DE: "Delaware", FL: "Florida", GA: "Georgia", HI: "Hawaii", ID: "Idaho", IL: "Illinois", IN: "Indiana", IA: "Iowa", KS: "Kansas",
  KY: "Kentucky", LA: "Louisiana", ME: "Maine", MD: "Maryland", MA: "Massachusetts", MI: "Michigan", MN: "Minnesota",
  MS: "Mississippi", MO: "Missouri", MT: "Montana", NE: "Nebraska", NV: "Nevada", NH: "New Hampshire", NJ: "New Jersey",
  NM: "New Mexico", NY: "New York", NC: "North Carolina", ND: "North Dakota", OH: "Ohio", OK: "Oklahoma", OR: "Oregon",
  PA: "Pennsylvania", RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota", TN: "Tennessee", TX: "Texas", UT: "Utah",
  VT: "Vermont", VA: "Virginia", WA: "Washington", WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming", DC: "DC",
  PR: "Puerto Rico", VI: "the Virgin Islands", GU: "Guam", AS: "American Samoa", MP: "the Northern Mariana Islands" };
export function zipStates(zip) {
  const m = /^(\d{3})\d{2}(?:-\d{4})?$/.exec(String(zip ?? "").trim());
  if (!m) return null;
  const n = Number(m[1]);
  const hit = ZIP3.find(([lo, hi]) => n >= lo && n <= hi);
  return hit ? [].concat(hit[2]) : null;
}

// The question under the ZIP box when it belongs to another state; "" when it fits, or either is
// blank or not yet valid. It doesn't stop a save: a few ZIPs cross state lines.
export function zipStateQuestion(zip, state) {
  const code = normalizeState(state);
  const states = zipStates(zip);
  if (!code || !states || states.includes(code)) return "";
  return `ZIP ${String(zip).trim()} is in ${STATE_NAMES[states[0]] || states[0]}, not ${STATE_NAMES[code] || code}. Check the state or the ZIP.`;
}
