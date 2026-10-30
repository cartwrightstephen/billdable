// Forms: one field() for the label, required mark, input, error line and aria; what was typed
// kept across re-renders; errors shown in place, without a re-render.
//
// A form is any element with data-form-key="<key>". Each input carries data-field="<name>",
// where the name is the request's own key (ap_email, contract_cents), so a field error from
// the server (a 409's "fields") lands under the right input.

import { esc, centsFrom, dollarsText } from "./format.js";
import { validate, tidy } from "./validate.js";

export const formDraft = {};   // form key -> {field name: text as typed}
export const formErrors = {};  // form key -> {field name: message, _form: a message for no one field}

// What to show in a field: what was typed, else the saved value. Raw text, not escaped.
export function draftValue(key, name, fallback = "") {
  const d = formDraft[key];
  return d && d[name] != null ? d[name] : fallback;
}

// Forget a form's typed values and errors (after it saved, or was cancelled).
export function clearForm(key) {
  delete formDraft[key];
  delete formErrors[key];
}

// One labelled field. label, help, placeholder, value and option texts are raw text and
// escaped here, so a value from the escaped state goes in through unesc().
// options: [[value, text], ...] makes a select; multiline makes a textarea.
// helpKey names the field's FIELD_HELP entry (the (i) text), put on the label as data-help.
// rule names a check in validate.js (phone, zip, state, email, licence, contract_number, name,
// street, city): the box is checked and tidied when it is left (watchForms), as the server does.
export function field({ form, name, id, label, type = "text", required = false, help = "", value = "", placeholder = "",
  autocomplete = "", inputmode = "", options = null, multiline = false, wide = false, attrs = "", helpKey = "", rule = "" }) {
  const err = (formErrors[form] || {})[name] || "";
  const current = type === "password" ? "" : String(draftValue(form, name, value ?? ""));
  const describedBy = [help ? `${id}-help` : "", `${id}-err`].filter(Boolean).join(" ");
  const common = `id="${id}" name="${esc(name)}" data-field="${esc(name)}"${required ? " required" : ""}${err ? ' aria-invalid="true"' : ""} aria-describedby="${describedBy}"${placeholder ? ` placeholder="${esc(placeholder)}"` : ""}${autocomplete ? ` autocomplete="${autocomplete}"` : ""}${inputmode ? ` inputmode="${inputmode}"` : ""}${rule ? ` data-rule="${esc(rule)}"` : ""}${attrs ? " " + attrs : ""}`;
  let control;
  if (options) control = `<select ${common}>${options.map(([v, text]) => `<option value="${esc(v)}"${String(v) === current ? " selected" : ""}>${esc(text)}</option>`).join("")}</select>`;
  else if (multiline) control = `<textarea ${common}>${esc(current)}</textarea>`;
  else control = `<input ${common} type="${type}" value="${esc(current)}">`;
  return `<div class="field${wide ? " span-2" : ""}"><label for="${id}"${helpKey ? ` data-help="${esc(helpKey)}"` : ""}>${esc(label)}${required ? ' <span class="req">*</span>' : ""}</label>${control}${help ? `<span class="sub field-help" id="${id}-help">${esc(help)}</span>` : ""}<span class="field-error" id="${id}-err">${esc(err)}</span></div>`;
}

// Dollars: a text box (so "$48,500" and "48,500.00" both work), shown as "$48,500.00" once
// typed. cents is the saved amount, if any.
export function moneyField(opts) {
  const { cents, ...rest } = opts;
  // inputmode "text" where a minus sign is allowed (a phone's decimal pad has none).
  return field({ placeholder: "0.00", ...rest, type: "text", inputmode: opts.inputmode || "decimal", autocomplete: "off",
    value: cents != null ? dollarsText(cents) : (opts.value || ""), attrs: `data-money="1"${opts.attrs ? " " + opts.attrs : ""}` });
}

// The form-level error line, for a message that belongs to no one field.
export function formErrorLine(key) {
  const msg = (formErrors[key] || {})._form;
  return `<div class="form-error" role="alert" data-form-error${msg ? "" : " hidden"}>${esc(msg || "")}</div>`;
}

// Show these errors ({field name: message}) under their inputs, in place: no re-render, so
// nothing typed is lost. Errors for fields this form doesn't show go in its error line.
// Returns the first input with an error (to focus), or null.
export function showFieldErrors(key, errors = {}) {
  const root = document.querySelector(`[data-form-key="${key}"]`);
  const inputs = root ? [...root.querySelectorAll("[data-field]")] : [];
  const shown = new Set(inputs.map(i => i.dataset.field));
  const rest = Object.entries(errors).filter(([k]) => k !== "_form" && !shown.has(k)).map(([, m]) => m);
  if (errors._form) rest.unshift(errors._form);
  formErrors[key] = { ...errors, _form: rest.join(" ") };
  if (!formErrors[key]._form) delete formErrors[key]._form;
  let first = null;
  for (const input of inputs) {
    const msg = errors[input.dataset.field] || "";
    const span = document.getElementById(`${input.id}-err`);
    if (span) span.textContent = msg;
    if (msg) {
      input.setAttribute("aria-invalid", "true");
      first = first || input;
      // A field folded away under "More details" opens so its error can be seen.
      const folded = input.closest("details");
      if (folded) folded.open = true;
    }
    else input.removeAttribute("aria-invalid");
  }
  if (root) {
    let line = root.querySelector("[data-form-error]");
    if (!line && rest.length) {
      line = document.createElement("div");
      line.className = "form-error";
      line.setAttribute("role", "alert");
      line.setAttribute("data-form-error", "");
      root.prepend(line);
    }
    if (line) { line.textContent = rest.join(" "); line.hidden = !rest.length; }
  }
  return first;
}

// Show errors and move to the first one. Returns true when there were any.
export function refuseForm(key, errors) {
  if (!Object.keys(errors).length) return false;
  const first = showFieldErrors(key, errors);
  (first || document.querySelector(`[data-form-key="${key}"] [data-form-error]`))?.focus?.();
  first?.scrollIntoView?.({ block: "center", behavior: "smooth" });
  return true;
}

// The values of a form's fields, by name, as typed (trimmed).
export function formValues(key) {
  const out = {};
  document.querySelectorAll(`[data-form-key="${key}"] [data-field]`).forEach(i => { out[i.dataset.field] = i.value.trim(); });
  return out;
}

// Once, at start-up: remember what is typed, clear a field's error as it is fixed, and show
// dollars as "$48,500.00" when the box is left.
export function watchForms(root = document) {
  const remember = (e) => {
    const el = e.target;
    const form = el && el.dataset && el.dataset.field && el.closest("[data-form-key]");
    if (!form) return;
    const key = form.dataset.formKey;
    // A password is never kept: a re-render shows its box empty.
    if (el.type !== "password" && el.type !== "checkbox") (formDraft[key] ||= {})[el.dataset.field] = el.value;
    const errs = formErrors[key];
    if (errs && errs[el.dataset.field]) {
      delete errs[el.dataset.field];
      el.removeAttribute("aria-invalid");
      const span = document.getElementById(`${el.id}-err`);
      if (span) span.textContent = "";
    }
  };
  root.addEventListener("input", remember);
  root.addEventListener("change", remember);
  root.addEventListener("focusout", (e) => {
    const el = e.target;
    if (el && el.dataset && el.dataset.rule) checkRule(el);
    if (!el || !el.dataset || !el.dataset.money || !el.value.trim()) return;
    const { cents, error } = centsFrom(el.value);
    if (error) return;
    el.value = dollarsText(cents);
    const form = el.closest("[data-form-key]");
    if (form) (formDraft[form.dataset.formKey] ||= {})[el.dataset.field] = el.value;
  });
}

// A box with a rule, just left: tidied (a phone as (404) 555-0123), and its error shown under it,
// or cleared, in place. Blank is left to the required check on save.
export function checkRule(el) {
  const rule = el.dataset.rule;
  const value = tidy(rule, el.value.trim());
  if (value !== el.value.trim()) el.value = value;
  const form = el.closest && el.closest("[data-form-key]");
  const key = form && form.dataset.formKey;
  if (key && el.dataset.field) (formDraft[key] ||= {})[el.dataset.field] = el.value;
  const msg = validate(rule, el.value);
  if (key && el.dataset.field) {
    const errs = (formErrors[key] ||= {});
    if (msg) errs[el.dataset.field] = msg; else delete errs[el.dataset.field];
  }
  const span = document.getElementById(`${el.id}-err`);
  if (span) span.textContent = msg;
  if (msg) el.setAttribute("aria-invalid", "true"); else el.removeAttribute("aria-invalid");
  return msg;
}

// Forget every form (moving to another page).
export function clearAllForms() {
  for (const k of Object.keys(formDraft)) delete formDraft[k];
  for (const k of Object.keys(formErrors)) delete formErrors[k];
}
