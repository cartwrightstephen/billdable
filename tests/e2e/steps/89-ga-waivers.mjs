// Browser steps for Georgia waivers, Nov 18 (Stephen: "Build as needed by your read of the law"):
// the filled-in final form for the retainage request when that is the last money, and the
// 90-day affidavit-of-nonpayment row. Run by tests/e2e/smoke.mjs. The form part checks whichever
// way the server is started: with DURACASH_WAIVER_FORMS=GA it opens the filled-in PDF, without it
// (the full suite) the form stays hidden and refused.
import assert from "node:assert/strict";

async function api(p, path, body) {
  return p.evaluate(async ([path, body]) => {
    const res = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let data = null;
    try { data = await res.json(); } catch { data = null; }
    return { status: res.status, data };
  }, [path, body]);
}

const ok = (r) => assert.equal(r.status, 200, JSON.stringify(r.data));
const jpeg = (name) => ({ name, mimeType: "image/jpeg", buffer: Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0, 16]) });

// A Georgia job of its own, $10,000 at 10% retainage, finished, billed in full and paid but the
// $1,000 retainage, with every closeout document in but the final lien waiver.
async function lastMoneyJob(p, signFirst) {
  const run = Date.now().toString(36);   // a run of its own on a server already used
  const name = `Peach depot R2-89 ${run}`;
  const made = await api(p, "/api/jobs", { name, address: "40 Peach Ave, Savannah, GA 31401", customer: `Peachline Builders 89 ${run}`,
    approver: "Mara Lee", ap_email: `ap-${run}@peachline89.example`, phone: "(912) 555-0142", contract_cents: 1_000_000, retainage_pct: 10, role: "GC",
    client_type: "gc", new_customer: true, approver_confirmed: true });
  ok(made);
  const job = made.data.jobs.find((j) => j.name === name);
  const line = job.sov_view && job.sov_view.lines && job.sov_view.lines[0];
  ok(line ? await api(p, `/api/jobs/${job.id}/sov-progress`, { done: { [line.id]: "1" }, notes: "", period_to: "" })
    : await api(p, `/api/jobs/${job.id}/progress`, { completed_cents: 1_000_000, notes: "", period_to: "" }));
  ok(await api(p, `/api/jobs/${job.id}/done`, { notes: "Finished", period_to: "" }));
  let view = (await api(p, `/api/jobs/${job.id}`)).data.job;
  const draft = view.invoices.find((i) => i.status === "draft" || i.status === "awaiting_approval");
  for (const item of draft.packet || []) {
    if (item.required && !item.present) ok(await api(p, `/api/jobs/${job.id}/resolve/${item.id}`, { reason: "Browser test" }));
  }
  view = (await api(p, `/api/jobs/${job.id}`)).data.job;
  const ready = view.invoices.find((i) => i.id === draft.id);
  assert.equal(await signFirst(p, job.id), 200, "the owner signs before it goes (Stephen, 18 Nov)");
  ok(await api(p, `/api/jobs/${job.id}/send`, { reviewed: (ready.email_preview || {}).stamp || "" }));
  ok(await api(p, `/api/jobs/${job.id}/match-payment`, { amount_cents: ready.amount_cents, reference: "R2-89", invoice_id: draft.id }));
  view = (await api(p, `/api/jobs/${job.id}`)).data.job;
  let waiverItem = null;
  for (const item of view.closeout_packet || []) {
    if (String(item.waiver_kind || "").endsWith("_final")) waiverItem = item;
    else if (item.required && !item.present) ok(await api(p, `/api/jobs/${job.id}/resolve/${item.id}`, { reason: "Browser test" }));
  }
  assert.ok(waiverItem, "the closeout documents ask for the final lien waiver");
  return { jobId: job.id, itemId: waiverItem.id };
}

export default async function ({ BASE, step, signedInPage, signFirst }) {
  let made = null;

  await step("Georgia: the final form goes with the retainage request when the retainage is the last money", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    made = await lastMoneyJob(p, signFirst);
    const job = (await api(p, `/api/jobs/${made.jobId}`)).data.job;
    assert.equal(job.waiver_state, "GA");
    assert.deepEqual(job.retainage_final_holds, [], "the request's gate and the form's rule: nothing else is open");
    assert.ok(job.final_waiver_holds.length, "the retainage is still to come");
    const on = (await api(p, "/api/waiver-forms")).data.states.GA.fillable;
    await p.goto(`${BASE}/money/packet/${made.jobId}/closeout`);
    const item = p.locator(`#packet-item-${made.itemId}`);
    await item.waitFor();
    await item.locator("summary", { hasText: "What the signed waiver must show" }).click();
    await item.locator(".waiver-form-state").waitFor();
    const url = `/api/jobs/${made.jobId}/waiver-form/final.pdf?for=retainage`;
    const fetched = await p.evaluate(async (u) => {
      const res = await fetch(u);
      const bytes = new Uint8Array(await res.arrayBuffer());
      return { status: res.status, type: res.headers.get("content-type"), text: Array.from(bytes, (b) => String.fromCharCode(b)).join("") };
    }, url);
    if (on) {
      const link = item.locator("[data-filled-form]");
      assert.equal(await link.getAttribute("href"), url);
      assert.equal(await link.innerText(), "Fill in the Georgia form");
      assert.equal(fetched.status, 200);
      assert.equal(fetched.type, "application/pdf");
      assert.match(fetched.text, /UPON FINAL/);
      assert.match(fetched.text, /\(1,000\.00\) Tj/, "the sum: the whole amount still owed, the retainage");
      // Without the request it still waits for all the money (decision 6).
      assert.equal((await api(p, `/api/jobs/${made.jobId}/waiver-form/final.pdf`)).status, 409);
    } else {
      assert.equal(await item.locator("[data-filled-form]").count(), 0, "held back while the server has it off");
      assert.equal(fetched.status, 404);
    }
    await p.context().close();
  });

  await step("Georgia: the retainage request carries the signed final waiver, and its 90-day clock starts quietly", async () => {
    assert.ok(made, "the job from the step before");
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(`${BASE}/money/packet/${made.jobId}/closeout`);
    const item = p.locator(`#packet-item-${made.itemId}`);
    await item.waitFor();
    const [chooser] = await Promise.all([p.waitForEvent("filechooser"), item.getByRole("button", { name: "Choose file" }).click()]);
    await chooser.setFiles(jpeg("final-waiver-signed.jpg"));
    await item.getByText("Which waiver is this?").waitFor();
    await item.getByRole("button", { name: "Conditional", exact: true }).click();
    await item.locator(".mark", { hasText: "✓" }).waitFor();
    ok(await api(p, `/api/jobs/${made.jobId}/retainage`, {}));
    const state = (await api(p, "/api/state")).data;
    const email = [...state.email_log].reverse().find((e) => e.job_id === made.jobId && e.kind === "retainage");
    assert.ok(email && email.attachments.length, "the request went with the signed waiver");
    assert.equal(email.retainage_asked_cents, 100_000);
    // Day 0: nothing on the To Do yet; it shows from day 60 (tests/test_r2_ga_last_money.py runs the clock).
    assert.equal(state.today_rows.filter((r) => r.job_id === made.jobId && ["affidavit", "waiver_in_effect"].includes(r.kind)).length, 0);
    await p.context().close();
  });

  await step("Georgia: the affidavit row on the Dashboard's To Do, amber, then red, with Open", async () => {
    const p = await signedInPage({ width: 1280, height: 900 });
    await p.goto(`${BASE}/dashboard`);
    await p.locator(".todo-table, .todo-wrap, .card").first().waitFor();
    // The server's row as domain.today_rows sends it on day 60 (the clock itself is tested in Python),
    // drawn by the page's own modules.
    const drawn = async (tone) => p.evaluate(async ([tone]) => {
      // The page's own module instances: the same addresses the app loaded them from.
      const loaded = (name) => performance.getEntriesByType("resource").map((e) => e.name).find((u) => new URL(u).pathname === `/js/${name}`) || `/js/${name}`;
      const core = await import(loaded("app-core.js"));
      const page = await import(loaded("state.js"));
      const row = { rank: tone === "red" ? 0.8 : 3.4, kind: "affidavit", job_id: "j-ga", job: "Peach depot R2-89", payer: "Peachline Builders 89",
        label: "Affidavit of nonpayment", tone, note: "File it in the property's county; copy to the owner within 7 days",
        text: "PA-01: the lien waiver sent Nov 18, 2026 takes effect Feb 16, 2027 unless you file an affidavit of nonpayment. Payment hasn't come in.",
        button: "", path: "/jobs/j-ga", invoice_id: "i-ga", co_id: "", email_id: "", item_id: "", key: "affidavit:j-ga:i-ga", due: "",
        ball_in_court: "us", assignee_id: "", assignee_name: "", perm: "send_waivers" };
      core.setState({ ...page.state, today_rows: [row], tasks: [] });
      core.render();
      const tr = document.querySelector(".todo-table tr.action-item");
      return { text: tr ? tr.innerText : "", pill: tr ? tr.querySelector(".pill").className : "", open: !!(tr && tr.querySelector('[data-action="open-job"]')),
        main: tr ? tr.querySelectorAll(".action-buttons .btn.small:not(.secondary)").length : -1 };
    }, [tone]);
    const amber = await drawn("amber");
    assert.match(amber.text, /takes effect Feb 16, 2027 unless you file an affidavit of nonpayment\. Payment hasn't come in\./);
    assert.match(amber.text, /File it in the property's county; copy to the owner within 7 days/);
    assert.equal(amber.pill, "pill amber");
    assert.equal(amber.open, true);
    assert.equal(amber.main, 0, "nothing in Duracash files it");
    assert.equal((await drawn("red")).pill, "pill red");
    await p.context().close();
  });
}
