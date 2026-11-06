// The waiver question on the page (the Nov 6 redesign) and the Round 1 hunt's waiver step: one tap
// says which waiver a file on a waiver item is, with the app's guess first and a warning when it
// reads as the other one; any other file that reads as a lien waiver is asked about with three
// buttons; the waiver step never sends when its check didn't come back. node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { installFakeDom, stubEl } from "./fakedom.mjs";

const dom = installFakeDom();
const core = await import("../../static/js/app-core.js");
const page = await import("../../static/js/state.js");
const reg = await import("../../static/js/views/index.js");
const { missingDocsHtml, beforeYouSendHtml, waiverQuestionHtml } = await import("../../static/js/views/paperwork.js");

const waiting = (over = {}) => ({ id: "pk-w", kind: "waiver", label: "Conditional waiver", required: true, present: false, blocker: "Conditional waiver is missing.",
  waiver_kind: "conditional_progress", accepts_kinds: ["waiver"], attachments: [{ record_id: "r2", name: "Signed", unconfirmed: true }], ...over });
const inv = (over = {}) => ({ id: "d1", number: "PA-1", status: "draft", amount_cents: 100000, excluded_file_ids: [], extra_record_ids: [], packet: [waiting()], ...over });
const job = (over = {}) => ({ id: "j1", name: "Pine porch", payer: { name: "Lee GC" }, payer_id: "p1", records: [{ id: "r2", kind: "waiver", label: "Signed", file_ids: ["f2"] }],
  invoices: [inv()], change_orders: [], money: {}, ...over });

function show(j, me = { name: "Pat", company_id: "co-p", role: "owner" }) {
  page.setMe(me);
  core.setState({ company: { name: "Pine" }, jobs: [j], payers: [], files: [{ id: "f2", name: "w.pdf" }, { id: "f9", name: "log.pdf" }] });
}

test("a waiver item asks which waiver it is, the guess filled in and the other reading said beside it", () => {
  const j = job();
  show(j);
  const html = missingDocsHtml(j, { ...inv(), packet: [waiting({ waiver_question: { guess: "unconditional", reads_as: "unconditional" },
    check: { status: "flagged", problems: ["This reads as unconditional.", "Amount not found: $1,000.00."] } })] });
  assert.match(html, /<strong>Which waiver is this\?<\/strong>/);
  assert.match(html, /<p class="blocker">This reads as unconditional\.<\/p>/);
  assert.match(html, /Amount not found: \$1,000\.00\./);
  assert.match(html, /<button class="btn small secondary" data-action="packet-confirm" data-id="j1" data-item="pk-w" data-answer="conditional" data-looked="1">Conditional<\/button>/);
  assert.match(html, /<button class="btn small" data-action="packet-confirm" data-id="j1" data-item="pk-w" data-answer="unconditional" data-looked="1" aria-current="true">Unconditional<\/button>/);
  assert.doesNotMatch(html, /I opened it and it's right|I checked this file/, "the answer is the one tap");
  assert.doesNotMatch(html, /style=|onclick=/);
});

test("a photo the app can't read picks nothing and says so; the tap is a choice of one of two buttons", () => {
  const j = job();
  show(j);
  const html = missingDocsHtml(j, { ...inv(), packet: [waiting({ waiver_question: { guess: "", reads_as: "" }, check: { status: "unchecked", problems: ["w.jpg is a photo or a scan, so it couldn't be checked."] } })] });
  assert.match(html, /The app couldn't tell which one this is\./);
  assert.match(html, /data-answer="conditional">Conditional</);
  assert.match(html, /data-answer="unconditional">Unconditional</);
  assert.doesNotMatch(html, /aria-current|This reads as/);
});

test("a file with a conditional and an unconditional waiver in it says so and picks nothing", () => {
  const j = job();
  show(j);
  const html = missingDocsHtml(j, { ...inv(), packet: [waiting({ waiver_question: { guess: "", reads_as: "", both: true } })] });
  assert.match(html, /It has both a conditional and an unconditional waiver in it, so the app couldn't tell which one this is\./);
  assert.doesNotMatch(html, /aria-current/);
});

test("someone who can't send pay apps sees which waiver it is without the buttons, and says who answers", () => {
  const j = job();
  show(j, { name: "Mo", company_id: "co-p", role: "member", perms: ["record_payments"] });
  const html = missingDocsHtml(j, { ...inv(), packet: [waiting({ waiver_question: { guess: "conditional", reads_as: "conditional" } })] });
  assert.match(html, /<strong>Which waiver is this\?<\/strong>/);
  assert.match(html, /Someone who can send pay apps says which waiver this is\./);
  assert.doesNotMatch(html, /data-action="packet-confirm"[^>]*data-answer/);
});

test("Is this a lien waiver? has three buttons, the guess filled in, and one tap answers it for the files shown", async () => {
  const rows = [{ record_id: "r9", label: "Daily log", file_ids: ["f9"], file_names: ["log.pdf"], guess: "unconditional" }];
  const j = job({ records: [{ id: "r9", kind: "ticket", label: "Daily log", file_ids: ["f9"] }], invoices: [inv({ packet: [], extra_record_ids: ["r9"], waiver_questions: rows })] });
  show(j);
  const html = beforeYouSendHtml(j, j.invoices[0]);
  assert.match(html, /<h3>Is this a lien waiver\?<\/h3>/);
  assert.match(html, /data-action="waiver-answer" data-id="j1" data-rec="r9" data-answer="no" data-files="f9">No</);
  assert.match(html, /<button class="btn small secondary" data-action="waiver-answer" data-id="j1" data-rec="r9" data-answer="conditional" data-files="f9">Yes, conditional</);
  assert.match(html, /<button class="btn small" data-action="waiver-answer" data-id="j1" data-rec="r9" data-answer="unconditional" data-files="f9" aria-current="true">Yes, unconditional</);
  assert.match(html, /href="\/api\/files\/f9"[^>]*>Open log\.pdf</);
  dom.requests.length = 0;
  dom.answers = [(p) => (p.includes("/waiver-answer/") ? { status: 200, body: { company: {}, jobs: [j], payers: [], inbox: [], email_log: [] } } : undefined)];
  await reg.actions["waiver-answer"]({ dataset: { rec: "r9", answer: "no", files: "f9" } }, "j1");
  const sent = dom.requests.find(r => r.path === "/api/jobs/j1/waiver-answer/r9");
  assert.deepEqual(sent.body, { answer: "no", shown: ["f9"] });
});

test("Is this a lien waiver? picks nothing when the app couldn't tell, and says it", () => {
  const rows = [{ record_id: "r9", label: "Scan", file_ids: ["f9"], file_names: ["scan.pdf"], guess: "" }, { record_id: "r8", label: "Packet", file_ids: ["f8"], file_names: ["p.pdf"], guess: "", both: true }];
  const j = job({ records: [{ id: "r9", kind: "other", label: "Scan", file_ids: ["f9"] }], invoices: [inv({ packet: [], extra_record_ids: ["r9"], waiver_questions: rows })] });
  show(j);
  const html = beforeYouSendHtml(j, j.invoices[0]);
  assert.match(html, /The app couldn't tell which one this is\./);
  assert.match(html, /It has both a conditional and an unconditional waiver in it, so the app couldn't tell which one this is\./);
  assert.doesNotMatch(html, /aria-current/);
  assert.equal((html.match(/data-answer="no"/g) || []).length, 2, "all three buttons are there for each");
});

test("on the closeout documents whoever can ask for retainage answers the question; on a pay app only someone who can send", () => {
  const rows = [{ record_id: "r9", label: "Paperwork", file_ids: ["f9"], file_names: ["p.pdf"], guess: "unconditional" }];
  page.setMe({ name: "Dee", company_id: "co-p", role: "member", perms: ["record_payments", "ask_retainage"] });
  assert.match(waiverQuestionHtml(job(), rows, true), /data-action="waiver-answer"/);
  const onPayApp = waiverQuestionHtml(job(), rows, false);
  assert.doesNotMatch(onPayApp, /data-action="waiver-answer"/);
  assert.match(onPayApp, /Someone who can send pay apps answers this\./);
});

test("someone who can't send pay apps sees the question but no buttons", () => {
  const rows = [{ record_id: "r9", label: "Daily log", file_ids: ["f9"], file_names: ["log.pdf"], guess: "conditional" }];
  page.setMe({ name: "Mo", company_id: "co-p", role: "member", perms: ["record_payments"] });
  const html = waiverQuestionHtml(job(), rows);
  assert.doesNotMatch(html, /data-action="waiver-answer"/);
  assert.match(html, /Someone who can send pay apps answers this\./);
});

// The waiver step (closeout.js): its check comes back, or nothing is sent.
const paid = () => job({ records: [], invoices: [{ id: "i1", number: "PA-1", status: "paid", amount_cents: 100000, waiver_due: true, waiver_sent: false,
  waiver_stages: { conditional: {}, unconditional: { kind: "unconditional_progress", status: "Attached" } } }], payer: { name: "Lee GC", ap_email: "ap@lee.example" } });

for (const [why, answer] of [["it can't reach the server", () => "offline"], ["the server fails", () => ({ status: 500, body: { error: "Boom." } })],
  ["it is refused for another reason", () => ({ status: 409, body: { error: "Add the signed unconditional waiver to the job records first." } })]]) {
  test(`the waiver step sends nothing when its check fails: ${why}`, async () => {
    const j = paid();
    show(j);
    dom.requests.length = 0;
    dom.answers = [(p) => (p.includes("/waiver") ? answer() : undefined), (p) => (p === "/api/state" ? { status: 200, body: { company: {}, jobs: [j], payers: [], inbox: [], email_log: [] } } : undefined)];
    await reg.actions.waiver(stubEl({ dataset: { inv: "i1" }, isConnected: false }), "j1");
    const posts = dom.requests.filter(r => r.path === "/api/jobs/j1/waiver");
    assert.equal(posts.length, 1, "only the check");
    assert.equal(posts[0].body.check_only, true);
  });
}

test("Ask for retainage with an unconditional waiver in the closeout documents takes an owner's reason", () => {
  const j = job({ work_status: "complete", money: { retainage_open_cents: 50000 }, closeout_unconditional: [{ record_id: "r2", label: "Final waiver", why: "Pat said it is the unconditional waiver" }] });
  show(j);
  const owner = reg.views.retainage();
  assert.match(owner, /An unconditional waiver is in the closeout documents: Final waiver\./);
  assert.match(owner, /id="retask-j1-why"/);
  assert.match(owner, /data-action="retainage" data-id="j1">Ask for retainage/);
  show(j, { name: "Mo", company_id: "co-p", role: "member", perms: ["ask_retainage"] });
  const member = reg.views.retainage();
  assert.match(member, /Only an owner can ask for retainage with it\./);
  assert.doesNotMatch(member, /data-action="retainage" data-id="j1"/);
});
