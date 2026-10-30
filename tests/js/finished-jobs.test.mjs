// A finished job leaves every working list (Stephen's Oct 29 review, J9): Money's Pay apps, Change
// orders, Unpaid, Payments and Closeout, and the Calendar. Pages of records (Activity log, Outbox,
// Everything received, Sent pay apps) list the open jobs first in their job filter, then the
// finished ones under "Finished jobs". node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom } from "./fakedom.mjs";

installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");
const { jobFilterOptions } = await import("../../static/js/jobs.js");

const money = { outstanding_cents: 0, retainage_open_cents: 0, unbilled_completed_cents: 0, contract_total_cents: 0, billed_cents: 0, collected_cents: 0 };
const payer = { id: "p1", name: "Ridge GC", approver: "Sam", ap_email: "ap@x.example" };
const now = new Date().toISOString();
const mk = (id, name, extra = {}) => ({
  id, name, code: name.toUpperCase().slice(0, 4), payer_id: "p1", closed: false, work_status: "in_progress",
  invoices: [{ id: `i-${id}`, number: `${name} PA-01`, status: "paid", sent_at: now, amount_cents: 1000, remaining_cents: 0 }],
  payments: [{ id: `pay-${id}`, invoice_id: `i-${id}`, amount_cents: 1000, matched_at: now, reference: `REF-${id}` }],
  change_orders: [{ id: `co-${id}`, number: 1, title: `${name} extra`, amount_cents: 500, status: "approved" }],
  money: { ...money }, blockers: [], records: [], ...extra,
});
const open = mk("ja", "Pine");
const done = mk("jb", "Oak", { closed: true, closed_at: now, completed_at: now });

function load() {
  page.setMe({ name: "Pat", company_id: "co-p", role: "owner", permissions: ["*"] });
  core.setState({ company: { name: "Pine" }, jobs: [open, done], payers: [payer], inbox: [], email_log: [], email_log_total: 0, closed_jobs_total: 1, totals: {} });
}

test("Money's lists and the Calendar leave out a finished job", () => {
  load();
  const pages = { payapps: reg.views.payapps(), cos: reg.views.cos(), payments: reg.views.payments(), calendar: reg.views.calendar() };
  for (const [name, html] of Object.entries(pages)) {
    assert.match(html, /Pine/, `${name} shows the open job`);
    assert.doesNotMatch(html, /Oak/, `${name} leaves out the finished job`);
  }
});

test("Unpaid and Closeout leave out a finished job even with money on it", () => {
  open.money.outstanding_cents = 2000; done.money.outstanding_cents = 2000;
  open.invoices[0].status = "sent"; done.invoices[0].status = "sent";
  load();
  const unpaid = reg.views.collections();
  assert.match(unpaid, /Pine/);
  assert.doesNotMatch(unpaid, /Oak/);
  assert.doesNotMatch(reg.views.waivers(), /Oak/);
  open.money.outstanding_cents = 0; done.money.outstanding_cents = 0;
  open.invoices[0].status = "paid"; done.invoices[0].status = "paid";
});

test("a job filter on a page of records lists open jobs first, finished ones under their own heading", () => {
  const html = jobFilterOptions([done, open], "jb");
  assert.ok(html.indexOf("Pine") < html.indexOf("Finished jobs"), "open first");
  assert.match(html, /<optgroup label="Finished jobs"><option value="jb" selected>Oak<\/option><\/optgroup>/);
  assert.doesNotMatch(jobFilterOptions([open]), /optgroup/, "no heading with nothing finished");
  load();
  for (const name of ["audit", "outbox"]) {
    const html = reg.views[name]();
    assert.match(html, /<optgroup label="Finished jobs">/, `${name} keeps finished jobs to filter by`);
  }
});

// The page shows what a person's profile allows, as the server's route checks (S6), not by role.
test("buttons follow the profile's permissions, not the Owner role", async () => {
  const { deletePayerHtml } = await import("../../static/js/views/jobs.js");
  const { newAddressButton } = await import("../../static/js/views/inbox.js");
  const { verifyBannerHtml } = await import("../../static/js/views/auth.js");
  const state = await import("../../static/js/state.js");
  load();
  const officeManager = { name: "Lee", company_id: "co-p", role: "member", perms: ["company", "data", "billing"] };
  const member = { name: "Sam", company_id: "co-p", role: "member", perms: ["record_payments"] };
  for (const [who, allowed] of [[officeManager, true], [member, false]]) {
    page.setMe(who);
    const has = (html, re, what) => (allowed ? assert.match : assert.doesNotMatch)(html, re, `${who.name}: ${what}`);
    has(newAddressButton("calendar"), /Make a new address/, "rotate an address (company)");
    has(reg.views.export(), /href="\/api\/export" download/, "download everything (data)");
    has(reg.views.calendar(), /Make a new address if it was shared by mistake/, "calendar address note (company)");
    state.setAccount({ billingInfo: { warning: "Card declined." } });
    has(verifyBannerHtml(), /data-view="billing"/, "billing link (billing)");
  }
  state.setAccount({ billingInfo: null });
  assert.equal(deletePayerHtml({ id: "p1", name: "Ridge", job_count: 0 }, { owner: false }), "");
});
