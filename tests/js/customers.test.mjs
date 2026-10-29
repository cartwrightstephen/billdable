// Customers (Oct 23 review): "Same as ABC Builders?" under the name box (note 5) and Delete
// customer for a customer no job uses (note 47). node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { sameCustomerBox, showSameCustomer, hideSameCustomer, deletePayerHtml } from "../../static/js/views/jobs.js";

// The one element the helpers touch, without a browser.
function withBox(fn) {
  const box = { hidden: true, innerHTML: "" };
  const seen = [];
  globalThis.document = { querySelector: (sel) => { seen.push(sel); return box; } };
  try { return fn(box, seen); } finally { delete globalThis.document; }
}

test("the question's place starts hidden, one per form", () => {
  assert.equal(sameCustomerBox("newjob"), '<div class="span-2 same-customer form-inline" data-same-customer="newjob" hidden></div>');
});

test("a near-match refusal shows Use and Keep, with the names escaped", () => {
  withBox((box, seen) => {
    const shown = showSameCustomer("newjob", "nj", { same_customer: { id: "payer-1", name: "ABC <Builders>", typed: "ABC Builders Inc" } });
    assert.equal(shown, true);
    assert.equal(box.hidden, false);
    assert.deepEqual(seen, ['[data-same-customer="newjob"]']);
    assert.match(box.innerHTML, /data-action="nj-same-use" data-name="ABC &lt;Builders&gt;" data-id="payer-1">Use ABC &lt;Builders&gt;<\/button>/);
    assert.match(box.innerHTML, /data-action="nj-same-keep" data-name="ABC Builders Inc">Keep “ABC Builders Inc” as a new customer<\/button>/);
    hideSameCustomer("newjob");
    assert.equal(box.hidden, true);
    assert.equal(box.innerHTML, "");
  });
});

test("any other refusal leaves the question hidden", () => {
  withBox((box) => {
    assert.equal(showSameCustomer("addpayer", "ap", { error: "Enter the customer's name." }), false);
    assert.equal(showSameCustomer("addpayer", "ap", undefined), false);
    assert.equal(box.hidden, true);
  });
});

test("Delete customer: owners only, and only with no job, finished ones included", () => {
  const p = { id: "payer-1", name: "Spare Co", job_count: 0 };
  assert.equal(deletePayerHtml(p, { owner: false }), "", "a member never sees it");
  assert.match(deletePayerHtml(p, { owner: true }), /data-action="delete-payer" data-id="payer-1" data-name="Spare Co">Delete customer<\/button>/);
  const used = deletePayerHtml({ ...p, job_count: 2 }, { owner: true, jobs: [] });
  assert.doesNotMatch(used, /data-action="delete-payer"/);
  assert.match(used, /can't be deleted, finished jobs included\. To remove Spare Co, merge it into another customer\./);
  // Without the server's count (an older page), the jobs on the page decide.
  assert.doesNotMatch(deletePayerHtml({ id: "payer-1", name: "Spare Co" }, { owner: true, jobs: [{ payer_id: "payer-1" }] }), /data-action/);
  assert.match(deletePayerHtml({ id: "payer-1", name: "Spare Co" }, { owner: true, jobs: [] }), /Delete customer/);
});
