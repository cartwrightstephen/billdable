// Browser steps for Round 2, decision 12 (Stephen, 10 Nov): the "?" helper opens a page, starts a task
// and answers from the company's own figures, and never changes anything itself. Job names come from
// the demo's own data, so a reseeded demo still runs. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

async function openHelper(p) {
  await p.click("#ai-badge");
  await p.locator("#ai-panel.open").waitFor();
}

async function ask(p, q) {
  const before = await p.locator("#ai-replies .msg.helper").count();
  await p.fill("#ai-input", q);
  await p.press("#ai-input", "Enter");
  await p.locator("#ai-replies .msg.helper").nth(before).waitFor();
  await p.locator("#ai-replies .typing").waitFor({ state: "detached" });
  return (await p.locator("#ai-replies .msg.helper").last().innerText()).trim();
}

// Every request but the helper's own question is a read: what the helper did changed nothing.
function watchWrites(p) {
  const writes = [];
  p.on("request", (r) => { if (r.method() !== "GET" && !/\/api\/helper\/(ask|feedback)$/.test(r.url())) writes.push(`${r.method()} ${r.url()}`); });
  return writes;
}

const jobsOf = (p) => p.evaluate(async () => (await (await fetch("/api/state")).json()).jobs.filter(j => !j.closed));
// A job no other open job shares a word of its name with (earlier steps add jobs to the demo).
const uniqueJob = (jobs, fits = () => true) => jobs.find(j => fits(j) && !jobs.some(o => o.id !== j.id
  && o.name.toLowerCase().split(/\W+/).some(w => w.length >= 3 && j.name.toLowerCase().split(/\W+/).includes(w))));

export default async function ({ BASE, step, signedInPage }) {
  await step("Helper: \"take me to Unpaid\" opens Unpaid, and \"open\" a job by name opens its page", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const writes = watchWrites(p);
    await p.goto(`${BASE}/dashboard`);
    await openHelper(p);
    assert.match(await ask(p, "take me to unpaid"), /^Opening Unpaid\./);
    await p.waitForURL(/\/money\/collections$/);
    const job = uniqueJob(await jobsOf(p));
    assert.match(await ask(p, `open the ${job.name} job`), new RegExp(`^Opening ${job.name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\.`));
    await p.waitForURL(new RegExp(`/jobs/${job.id}$`));
    assert.deepEqual(writes, []);
    await p.context().close();
  });

  await step("Helper: \"new change order for <job>\" opens the job's New change order form, empty, and saves nothing", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const writes = watchWrites(p);
    await p.goto(`${BASE}/dashboard`);
    const job = uniqueJob(await jobsOf(p));
    const cos = job.change_orders.length;
    await openHelper(p);
    await ask(p, `new change order for ${job.name}`);
    await p.waitForURL(new RegExp(`/jobs/${job.id}$`));
    await p.locator("#co-title").waitFor();
    assert.equal(await p.inputValue("#co-title"), "");
    assert.equal((await jobsOf(p)).find(j => j.id === job.id).change_orders.length, cos);
    assert.deepEqual(writes, []);
    await p.context().close();
  });

  await step("Helper: \"record a payment on <job>\" opens Record a payment with what is owed filled in", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    const writes = watchWrites(p);
    await p.goto(`${BASE}/dashboard`);
    const job = uniqueJob(await jobsOf(p), j => j.invoices.some(i => ["sent", "approved", "partial"].includes(i.status)));
    assert.ok(job, "the demo has a pay app waiting for payment");
    await openHelper(p);
    await ask(p, `record a payment on ${job.name}`);
    await p.waitForURL(new RegExp(`/jobs/${job.id}$`));
    await p.locator("#match-amt").waitFor();
    assert.notEqual(await p.inputValue("#match-amt"), "");
    assert.deepEqual(writes, []);
    await p.context().close();
  });

  await step("Helper: \"who owes me money\" answers from the company's figures, with a button to Unpaid", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(`${BASE}/jobs`);
    const owed = await p.evaluate(async () => (await (await fetch("/api/financials")).json()).receivable_cents);
    await openHelper(p);
    const said = await ask(p, "who owes me money");
    if (owed > 0) {
      const dollars = (owed / 100).toLocaleString("en-US", { style: "currency", currency: "USD" });
      assert.ok(said.includes(`Owed to you: ${dollars}`), said);
    } else {
      assert.match(said, /Nobody owes you/);
      await p.context().close();
      return;
    }
    await p.locator("#ai-replies .msg.helper").last().locator("button", { hasText: "Open Unpaid" }).click();
    await p.waitForURL(/\/money\/collections$/);
    await p.context().close();
  });
}
