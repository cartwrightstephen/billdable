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
