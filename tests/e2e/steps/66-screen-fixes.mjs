// Browser steps for the acceptance test's screen fixes: a way forward from every empty page, a
// conversation's text editable, a missing document's button landing on it, the New job form with
// nothing folded, and a phone's tab row and boxes. Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ browser, BASE, step, errors }) {
  // A new company of its own, on a phone.
  const fresh = async (name) => {
    const ctx = await browser.newContext({ viewport: { width: 375, height: 812 } });
    const p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    await p.evaluate(async ([email, company]) => {
      await fetch("/api/auth/signup", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accept_terms: true, email, password: "smoke test password", name: "Fay Fix", company_name: company }) });
    }, [`${name}-${Date.now()}@example.com`, `${name} Builders`]);
    return p;
  };
  const addJob = (p) => p.evaluate(async () => {
    const r = await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      phone: "(404) 555-0100", approver_confirmed: true, approver: "Rae Homeowner", name: "Fix deck", address: "4 Oak St", customer: "Rae Homeowner", role: "Owner",
      ap_email: "rae@example.com", contract_cents: 2000000 }) });
    return (await r.json()).jobs[0];
  });

  await step("empty pages have a button: New job with no jobs, the next thing with one (criterion 62)", async () => {
    const p = await fresh("empty");
    for (const path of ["/money", "/jobs/history", "/money/payments"]) {
      await p.goto(BASE + path);
      await p.locator(".empty-state").getByRole("button", { name: "New job", exact: true }).waitFor();
    }
    await p.locator(".empty-state").getByRole("button", { name: "New job", exact: true }).click();
    await p.locator('[data-form-key="newjob"]').waitFor();
    await addJob(p);
    await p.goto(BASE + "/money");
    await p.locator(".empty-state").getByRole("button", { name: "New pay app" }).waitFor();
    await p.goto(BASE + "/money/payments");
    await p.locator(".empty-state").getByRole("button", { name: "New pay app" }).waitFor();
    await p.goto(BASE + "/jobs/history");
    await p.locator(".empty-state").getByRole("button", { name: "Open jobs" }).waitFor();
    await p.context().close();
  });

  await step("a conversation's text can be edited, and the edit is in the Activity log (criterion 59)", async () => {
    const p = await fresh("talk");
    const job = await addJob(p);
    await p.evaluate((id) => fetch(`/api/jobs/${id}/note-contact`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: "Check goes out Friday" }) }), job.id);
    await p.goto(`${BASE}/jobs/${job.id}`);
    // Job records is folded on a phone (Round 1, item 13): open it.
    const records = p.locator("details.job-records");
    if (await records.getAttribute("open") === null) await records.locator(":scope > summary").click();
    const group = p.locator('.record-group[data-group="conversation"]');
    await group.getByRole("button", { name: /^Edit / }).click();
    const box = group.locator("textarea");
    assert.equal(await box.inputValue(), "Check goes out Friday");
    await box.fill("Check goes out Monday");
    await group.locator('[data-action="record-save"]').click();
    await group.locator(".list-item", { hasText: "Check goes out Monday" }).waitFor();
    assert.equal(await group.locator("textarea").count(), 0);
    const logged = await p.evaluate(async (id) => (await (await fetch(`/api/audit?job=${id}&limit=5`)).json()).entries.map(e => e.detail).join(" "), job.id);
    assert.match(logged, /from 'Check goes out Friday' to 'Check goes out Monday'/);
    await p.context().close();
  });

  await step("a missing document's button lands on that document, focused (criterion 61)", async () => {
    const p = await fresh("land");
    const job = await addJob(p);
    const first = await p.evaluate(async ([id, line]) => {
      await fetch(`/api/jobs/${id}/sov-progress`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ done: { [line]: "0.25" } }) });
      const st = await (await fetch("/api/state")).json();
      return st.jobs[0].blocker_items[0];
    }, [job.id, job.sov.lines[0].id]);
    assert.ok(first.item_id);
    await p.goto(`${BASE}/jobs/${job.id}`);
    await p.locator(".next-card .primary-action").click();
    const row = p.locator(`#packet-item-${first.item_id}`);
    await row.waitFor();
    await p.waitForFunction((id) => document.activeElement && document.activeElement.id === id, `packet-item-${first.item_id}`);
    // Read in the page: the waiver forms arriving redraw the page, which can catch boundingBox mid-swap.
    const y = await p.evaluate((id) => document.getElementById(id).getBoundingClientRect().y, `packet-item-${first.item_id}`);
    assert.ok(y >= 0 && y < 812, `in view: ${y}`);
    // The Dashboard's row does the same.
    await p.goto(`${BASE}/dashboard`);
    await p.locator(`[data-action="open-packet"][data-item="${first.item_id}"]`).first().click();
    await p.waitForFunction((id) => document.activeElement && document.activeElement.id === id, `packet-item-${first.item_id}`);
    await p.context().close();
  });

  await step("New job: the money and date boxes show, with nothing folded (criterion 94)", async () => {
    const p = await fresh("form");
    await p.goto(BASE + "/jobs");
    await p.locator(".top").getByRole("button", { name: "New job", exact: true }).click();
    for (const id of ["#nj-amount", "#nj-start", "#nj-end", "#nj-retainage", "#nj-contract"]) {
      assert.ok(await p.locator(id).isVisible(), `${id} shows`);
    }
    assert.equal(await p.getByText("More details").count(), 0);
    await p.context().close();
  });

  await step("New job: who signs off and the client type are the person's to enter (criterion 3)", async () => {
    const p = await fresh("signer");
    await p.goto(BASE + "/jobs");
    await p.locator(".top").getByRole("button", { name: "New job", exact: true }).click();
    assert.equal(await p.inputValue("#nj-client-type"), "", "starts on Choose");
    await p.fill("#nj-customer", "Rae Homeowner");
    await p.press("#nj-customer", "Tab");
    assert.equal(await p.inputValue("#nj-approver"), "", "leaving the client box fills nothing");
    await p.click('[data-action="create-job"]');
    await p.locator("#nj-approver-err", { hasText: "who signs off" }).waitFor();
    assert.match(await p.locator("#nj-client-type-err").innerText(), /client type/);
    await p.context().close();
  });

  await step("New job: typing the client's own name as who signs off is an answer, and it stays answered", async () => {
    const p = await fresh("own-name");
    await p.goto(BASE + "/jobs");
    await p.locator(".top").getByRole("button", { name: "New job", exact: true }).click();
    await p.fill("#nj-name", "Own name deck");
    await p.fill("#nj-street", "4 Oak St");
    await p.fill("#nj-city", "Decatur");
    await p.selectOption("#nj-state", "GA");
    await p.fill("#nj-zip", "30030");
    await p.fill("#nj-amount", "20,000");
    await p.fill("#nj-customer", "Rae Homeowner");
    await p.selectOption("#nj-client-type", "homeowner");
    await p.fill("#nj-approver", "Rae Homeowner");
    await p.fill("#nj-email", "rae@example.com");
    await p.fill("#nj-phone", "404-555-0100");
    await p.click('[data-action="create-job"]');
    await p.waitForFunction(async () => ((await (await fetch("/api/state")).json()).jobs || []).length === 1);
    const payer = await p.evaluate(async () => (await (await fetch("/api/state")).json()).payers[0]);
    assert.equal(payer.approver_confirmed, true);
    assert.deepEqual(payer.playbook_missing || [], []);
    await p.context().close();
  });

  await step("Send draft for their review shows what goes first; missing documents don't hold it back (criterion 78)", async () => {
    const p = await fresh("pencil");
    const job = await addJob(p);
    await p.evaluate(([id, line]) => fetch(`/api/jobs/${id}/sov-progress`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ done: { [line]: "0.25" } }) }), [job.id, job.sov.lines[0].id]);
    const emails = () => p.evaluate(async () => (await (await fetch("/api/state")).json()).email_log.length);
    await p.goto(`${BASE}/jobs/${job.id}`);
    await p.locator(".job-actions").getByRole("button", { name: "Send draft for their review" }).click();
    const card = p.locator(".pencil-check");
    await card.waitFor();
    assert.equal(await emails(), 0, "nothing goes until the screen's own button");
    const text = await card.innerText();
    assert.match(text, /rae@example\.com/);
    assert.match(text, /\$5,000\.00/);
    assert.match(text, /Pencil copy for review/);
    assert.match(text, /Still missing/);
    await card.getByRole("link", { name: "Open the PDF" }).waitFor();
    await card.getByRole("button", { name: "Send draft for their review" }).click();
    await card.waitFor({ state: "detached" });
    assert.equal(await emails(), 1);
    await p.context().close();
  });

  await step("the Inbox offers Delivery ticket and Time sheet, and the record says which (criterion 83)", async () => {
    const p = await fresh("ticket");
    const job = await addJob(p);
    await p.evaluate(() => fetch("/api/import/text", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text: "Crew hours for Oak", source: "paste", job_id: "" }) }));
    await p.goto(`${BASE}/communications`);
    const kind = p.locator('[id^="inb-kind-"]').first();
    await kind.waitFor();
    const labels = await kind.locator("option").allInnerTexts();
    assert.ok(labels.includes("Delivery ticket") && labels.includes("Time sheet"), labels.join());
    assert.ok(!labels.includes("Delivery ticket or time sheet"));
    await kind.selectOption("ticket:time_sheet");
    await p.locator('[id^="inb-job-"]').first().selectOption(job.id);
    await p.locator('[data-action="inbox-file"]').first().click();
    await p.waitForFunction(async (id) => (await (await fetch("/api/state")).json()).jobs.find(j => j.id === id).records.some(r => r.ticket_type === "time_sheet" && r.kind === "ticket"), job.id);
    await p.context().close();
  });

  await step("phone: the active tab is brought into view, once, and boxes don't make iOS zoom", async () => {
    const p = await fresh("tabs");
    await p.goto(BASE + "/settings/audit");
    const inView = () => p.evaluate(() => {
      const row = document.querySelector(".tabs").getBoundingClientRect(), tab = document.querySelector(".tabs .tab.active").getBoundingClientRect();
      return tab.left >= row.left - 1 && tab.right <= row.right + 1;
    });
    await p.locator(".tabs .tab.active").waitFor();
    assert.ok(await inView(), "Settings: Activity log");
    // A person scrolling the row isn't moved back by a redraw.
    const left = await p.evaluate(() => { const t = document.querySelector(".tabs"); t.scrollLeft -= 20; return t.scrollLeft; });
    await p.evaluate(() => window.dispatchEvent(new Event("focus")));
    await p.waitForTimeout(100);
    assert.equal(await p.evaluate(() => document.querySelector(".tabs").scrollLeft), left);
    await p.goto(BASE + "/money/payments");
    await p.locator(".tabs .tab.active").waitFor();
    assert.ok(await inView(), "Money: Payments");
    await p.goto(BASE + "/jobs");
    await p.locator(".top").getByRole("button", { name: "New job", exact: true }).click();
    const small = await p.evaluate(() => [...document.querySelectorAll("#view input:not([type=checkbox]):not([type=radio]):not([type=file]), #view select, #view textarea")]
      .filter(e => parseFloat(getComputedStyle(e).fontSize) < 16).map(e => e.id));
    assert.deepEqual(small, []);
    await p.context().close();
  });
}
