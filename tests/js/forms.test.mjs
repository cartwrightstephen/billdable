import { test } from "node:test";
import assert from "node:assert/strict";
import { field, moneyField, formDraft, formErrors, formErrorLine, clearForm } from "../../static/js/forms.js";

test("field() puts label, input, error line and aria in one place", () => {
  const html = field({ form: "t", name: "ap_email", id: "t-email", label: "AP email", type: "email", required: true });
  assert.match(html, /<label for="t-email">AP email <span class="req">\*<\/span><\/label>/);
  assert.match(html, /<input id="t-email" name="ap_email" data-field="ap_email" required aria-describedby="t-email-err"/);
  assert.match(html, /<span class="field-error" id="t-email-err"><\/span>/);
});

test("a typed value survives a re-render, escaped", () => {
  formDraft.t = { name: `Oak "St" <b>` };
  const html = field({ form: "t", name: "name", id: "t-name", label: "Job name", value: "saved" });
  assert.match(html, /value="Oak &quot;St&quot; &lt;b&gt;"/);
  clearForm("t");
  assert.match(field({ form: "t", name: "name", id: "t-name", label: "Job name", value: "saved" }), /value="saved"/);
});

test("an error from the server shows under its field", () => {
  formErrors.t = { ap_email: "Enter the email pay apps go to, like ap@builder.com.", _form: "Job not saved." };
  const html = field({ form: "t", name: "ap_email", id: "t-email", label: "AP email" });
  assert.match(html, /aria-invalid="true"/);
  assert.match(html, /like ap@builder.com.<\/span>/);
  assert.match(formErrorLine("t"), /Job not saved./);
  clearForm("t");
  assert.match(formErrorLine("t"), / hidden>/);
});

test("passwords are never written into the page", () => {
  formDraft.t = { password: "hunter2hunter2" };
  assert.doesNotMatch(field({ form: "t", name: "password", id: "t-pw", label: "Password", type: "password" }), /hunter2/);
  clearForm("t");
});

test("moneyField shows a saved amount as dollars, in a text box", () => {
  const html = moneyField({ form: "t", name: "contract_cents", id: "t-amt", label: "Contract amount ($)", cents: 4850000 });
  assert.match(html, /type="text" value="\$48,500.00"/);
  assert.match(html, /inputmode="decimal"/);
  assert.match(html, /data-money="1"/);
});

test("a select with one choice can read as text, and two choices can be two buttons", () => {  // round 1, item 8
  const one = field({ form: "t", name: "invoice_id", id: "t-inv", label: "Pay app", options: [["i1", "PA-1 · $100.00"]], oneAsText: true });
  assert.doesNotMatch(one, /<select/);
  assert.match(one, /<input type="hidden" id="t-inv" name="invoice_id" data-field="invoice_id"[^>]* value="i1">/);
  assert.match(one, /<span class="field-label">Pay app<\/span><span class="field-value">PA-1 · \$100\.00<\/span>/);
  const two = field({ form: "t", name: "invoice_id", id: "t-inv", label: "Pay app", options: [["i1", "PA-1"], ["i2", "PA-2"]], oneAsText: true });
  assert.match(two, /<select id="t-inv"/, "two or more stay a select");
  const btns = field({ form: "t", name: "ball", id: "t-ball", label: "Ball in court", options: [["us", "Us"], ["client", "Client"]], buttons: true });
  assert.doesNotMatch(btns, /<select/);
  assert.match(btns, /<span class="field-label" id="t-ball-label">Ball in court<\/span><div class="seg" role="group" aria-labelledby="t-ball-label">/);
  assert.match(btns, /<input type="hidden" id="t-ball" name="ball" data-field="ball"[^>]* value="us">/);
  assert.match(btns, /<button type="button" class="btn small" data-action="seg-pick" data-for="t-ball" data-value="us" aria-pressed="true">Us<\/button>/);
  assert.match(btns, /<button type="button" class="btn small secondary" data-action="seg-pick" data-for="t-ball" data-value="client" aria-pressed="false">Client<\/button>/);
  formDraft.t = { ball: "client" };
  assert.match(field({ form: "t", name: "ball", id: "t-ball", label: "Ball in court", options: [["us", "Us"], ["client", "Client"]], buttons: true }), /value="client">/);
  clearForm("t");
});
