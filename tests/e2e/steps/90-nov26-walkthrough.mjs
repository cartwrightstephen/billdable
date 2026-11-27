// Browser steps for the Nov 26 walkthrough's interface items: the ? at the lower left, over no
// button; meta text behind an (i); everyone's notifications on Team; profile templates; % inside
// its box; 44 px buttons at iPad width; the job page's Money card; the ZIP asked about on Company.
// Run by tests/e2e/smoke.mjs.
import assert from "node:assert/strict";

export default async function ({ browser, BASE, step, errors }) {
  async function newCompany(prefix, viewport = { width: 1024, height: 768 }) {
    const ctx = await browser.newContext({ viewport });
    const p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    const invite = await p.evaluate(async (email) => {
      const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      await post("/api/auth/signup", { accept_terms: true, email, password: "smoke test password", name: "Wendy Walk", company_name: "Walk Builders" });
      return (await (await post("/api/team/invite", { email: "crew-" + email, profile_id: "member" })).json()).invite_path;
    }, `${prefix}-${Date.now()}@example.com`);
    // The invited person joins in a browser of their own.
    const other = await browser.newContext();
    const q = await other.newPage();
    await q.goto(BASE + "/");
    const joined = await q.evaluate(async (token) => (await fetch("/api/auth/accept-invite", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ accept_terms: true, token, name: "Casey Crew", password: "smoke test password" }) })).status, invite.split("#invite=")[1]);
    assert.equal(joined, 200);
    return { ctx, p, other, q };
  }

  await step("At iPad width the ? sits at the lower left, over no button, and the main buttons are 44 px tall", async () => {
    const { ctx, p, other } = await newCompany("badge");
    for (const path of ["/settings/account", "/settings/company", "/jobs"]) {
      await p.goto(BASE + path);
      await p.locator("#view h1").waitFor();
      const clash = await p.evaluate(() => {
        const b = document.getElementById("ai-badge").getBoundingClientRect();
        const main = document.getElementById("main").getBoundingClientRect();
        const hits = [...document.querySelectorAll("#main .btn")].filter(el => el.offsetParent).map(el => el.getBoundingClientRect())
          .filter(r => r.left < b.right && r.right > b.left && r.top < b.bottom && r.bottom > b.top);
        return { hits: hits.length, left: b.left < main.left + 1, low: b.bottom > innerHeight - 160 };
      });
      assert.deepEqual(clash, { hits: 0, left: true, low: true }, path);
    }
    await p.goto(BASE + "/settings/account");
    const save = await p.locator(".save-bar .btn").boundingBox();
    assert.ok(save.height >= 44, `Save is ${save.height}px tall`);
    await other.close();
    await ctx.close();
  });

  await step("Notifications' explanation is behind its (i); the owner sets a team member's from Team", async () => {
    const { ctx, p, other, q } = await newCompany("notes", { width: 1280, height: 900 });
    await p.goto(BASE + "/settings/account");
    const card = p.locator("#notifications");
    await card.locator("input[data-reminder]").first().waitFor();
    assert.equal(await card.getByText("For you and your team").isVisible(), false, "not on the card");
    await card.getByLabel("About notifications").click();
    await card.getByText("For you and your team, never your clients.").waitFor();
    await p.goto(BASE + "/settings/team");
    const row = p.locator(".team-table tr", { hasText: "Casey Crew" });
    await row.getByText("Their own choice").waitFor();
    await row.getByRole("button", { name: "Set notifications for Casey Crew" }).click();
    const form = p.locator("#team-notes-form");
    await form.locator('input[data-team-reminder="dates"][value="email"]').check();
    await form.getByRole("button", { name: "Save notifications" }).click();
    await row.getByText("As Wendy Walk set them").waitFor();
    // Casey turns that email off on My account: theirs applies, and Team shows it.
    await q.goto(BASE + "/settings/account");
    await q.getByText("Wendy Walk set these for you").waitFor();
    await q.locator('#notifications input[data-reminder="dates"][value="email"]').uncheck();
    await q.locator(".save-bar .btn").click();
    await q.locator("#toast", { hasText: "Saved" }).waitFor();
    await p.reload();
    await p.locator(".team-table tr", { hasText: "Casey Crew" }).getByText("Email off for Important dates").waitFor();
    await other.close();
    await ctx.close();
  });

  await step("A new profile starts from a template; the % sits inside its box", async () => {
    const { ctx, p, other } = await newCompany("tmpl", { width: 1280, height: 900 });
    await p.goto(BASE + "/settings/team");
    await p.getByRole("button", { name: "New profile" }).click();
    await p.getByRole("button", { name: "Superintendent" }).click();
    assert.equal(await p.locator("#profile-name").inputValue(), "Superintendent");
    assert.equal(await p.locator('[data-perm="manage_jobs"]').isChecked(), true);
    assert.equal(await p.locator('[data-perm="send_payapps"]').isChecked(), false);
    await p.locator('[data-perm="record_payments"]').check();
    await p.getByRole("button", { name: "Save profile" }).click();
    await p.locator(".profiles-table th", { hasText: "Superintendent" }).waitFor();
    await p.goto(BASE + "/jobs");
    await p.locator('[data-action="toggle-form"][data-form="newjob"]').first().click();
    // The page may redraw as it settles: measured in one go, once both are there.
    const inside = await p.waitForFunction(() => {
      const box = document.getElementById("nj-retainage"), pct = box && box.nextElementSibling;
      if (!pct || !pct.classList.contains("suffix")) return null;
      const b = box.getBoundingClientRect(), s = pct.getBoundingClientRect();
      return s.width > 0 && s.left > b.left && s.right <= b.right ? "inside" : "outside";
    });
    assert.equal(await inside.jsonValue(), "inside", "the % is inside the box");
    await other.close();
    await ctx.close();
  });

  await step("Company: a ZIP from another state is asked about as soon as the page opens", async () => {
    const { ctx, p, other } = await newCompany("zip", { width: 1280, height: 900 });
    await p.evaluate(() => fetch("/api/company", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Walk Builders", street: "1 Peach St", city: "Atlanta", state: "GA", zip: "33909" }) }));
    await p.goto(BASE + "/settings/company");
    await p.getByText("ZIP 33909 is in Florida, not Georgia.").waitFor();
    await p.locator("#co-zip").fill("30303");
    await p.locator("#co-zip").blur();
    await p.getByText("ZIP 33909 is in Florida").waitFor({ state: "hidden" });
    await other.close();
    await ctx.close();
  });

  await step("The job page's Money card has no fold on a computer and keeps its own height", async () => {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    const p = await ctx.newPage();
    p.on("pageerror", (e) => errors.push(e.message));
    await p.goto(BASE + "/");
    await p.evaluate(() => fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email: "owner@redbudelectric.example", password: "demo" }) }));
    await p.goto(BASE + "/jobs/job-turner");
    await p.locator("section.money-file").waitFor();
    assert.equal(await p.locator("details.money-file").count(), 0);
    assert.equal(await p.locator("section.money-file").evaluate(e => getComputedStyle(e.parentElement).alignItems), "start", "each card its own height");
    await ctx.close();
  });
}
