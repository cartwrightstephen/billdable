# Client-ready tuning: punch list

> **Retired Oct 29, 2026 (R6-11).** Every item on this list was absorbed by an item of `docs/improvement-plan.md`, named under its heading with where it stands; E6 alone was deferred, on purpose. The plan's appendix has the same map. Kept as the record of what a first client would have hit: labels and line numbers below are as of Oct 22 and are not current. For how the app works now, read `docs/client-flows.md`.

Goal: take Duracash from a working concept to something a 2-12 person contractor can use on day one without help. Stephen's bar: "really intuitive for manual entry", "works exactly how they'd need", "simple ... easy".

How this list was made: each item was checked against the current code (mainly `static/app.js`, plus `domain.py`, `server.py`, `importer.py`). Labels in quotes are copied from the code. Line numbers are as of 2026-10-22. Items I could not confirm in the code were dropped, and duplicates found in more than one area were merged.

Effort: **S** = under half a day, client only. **M** = 1-3 days, or touches the server. **L** = a week or more, or needs outside setup.

---

## A. Blockers for a first paying client

These cause wrong emails, stuck jobs or a pay app that never goes out.

### A1. "Notes for the job record" becomes the customer's paperwork checklist (M)

> Absorbed by **R1-04** (done).
- **Where:** `/jobs` > New job. `static/app.js:2148-2152` (textarea, placeholder "Contract notes, what this customer usually wants, anything to keep with the job"), sent as `packet_notes` at `static/app.js:1093`. Server: `domain.py:1589-1591` (packet rule = the notes), `domain.py:1646-1653` (record "Packet requirements"), split into required items by `domain.py:1410-1420`.
- **What a contractor hits today:** they type "Gate code 1234, call before 8am". For a new customer this replaces the default packet. It becomes two required items ("Gate code 1234", "Call before 8am"), and both block Approve & send on the first pay app.
- **Change:** split the field in two.
  1. "Notes" (kept on the job record only; never parsed).
  2. "Paperwork this customer needs with each pay app": a checklist with the defaults already ticked (Owner: Signed contract, Conditional waiver, Completion photos, Pay-app backup. GC: AIA-style pay app, Schedule of values, Lien waiver, Insurance certificate, Daily tickets, from `domain.py:1589-1591`), plus "Add another".

  Show this checklist only when the customer is new.

### A2. Approver and AP email are required on every job, and ignored for repeat customers (M)

> Absorbed by **R1-05** and **R2-01** (both done).
- **Where:** `/jobs` > New job. `static/app.js:2143-2144`, client checks `static/app.js:1096-1103`. Server: `domain.py:1575-1578` (required), `domain.py:1607-1626` (existing payer: typed values are dropped, logged only as audit `payer_kept`). The toast still says "Job saved" (`static/app.js:1138`).
- **What a contractor hits today:**
  - A homeowner remodel has no AP department, so the contractor makes up an email to save the job.
  - On a repeat customer, a new AP email is thrown away without a word, and pay apps keep going to the old address.
- **Change:**
  - Make Customer a type-ahead over existing payers.
  - When it matches an existing payer, replace the Approver and AP email inputs with a read-only line, "Pay apps go to <approver> · <ap_email>", and a "Change in Payer playbook" link.
  - For a new homeowner customer, default Approver to the Customer name and make AP email optional. Ask for it in "Before you send" (`static/app.js:2427-2436` already shows "no AP email").

### A3. A short-paid pay app can never be settled (M)

> Absorbed by **R4-01** (done).
- **Where:** pay app and job page. `domain.py:572-575` (status `partial`), close blocked by outstanding money (`domain.py:82-103`). No write-off or short-pay action exists anywhere (searched `domain.py`, `server.py`, `static/app.js`).
- **What a contractor hits today:** a GC pays $9,500 on a $10,000 pay app and won't pay the rest. The pay app stays "Partly paid" forever. No waiver comes due, "Close for record" never unlocks, and the job sits in Collections and Today for good.
- **Change:** add an "Accept short pay" button on a partly paid pay app.
  - It asks for "Why (for example: backcharge for cleanup)".
  - It records an adjustment, marks the pay app paid, and opens the waiver step.

### A4. An unconfirmed email silently stops the first pay app (S/M)

> Absorbed by **R1-07** (done).
- **Where:** sign-up `static/app.js:418-434` ignores the `verification` the server returns (`server.py:1173-1174`). The banner (`static/app.js:1599-1605`) shows only when mail is set up. The outcome is the email status "not sent: verify your email address first (Team & account page), then Send again" (`mailer.py:47`).
- **What a contractor hits today:** they build a job, clear the packet and press Approve & send. The pay app is marked sent in Duracash, but the email never left.
- **Change:**
  - After sign-up, show a "Check your email" step with "Send the link again".
  - Disable "Approve & send" while the email is unconfirmed, with the reason "Confirm your email first so the pay app can go out from you", and put a resend button next to it.

### A5. "Approve & send" on the job page sends without showing who gets it (S)

> Absorbed by **R1-06** (done).
- **Where:** `/jobs/<id>` primary button "Approve & send", `static/app.js:655`. It posts straight away (`static/app.js:894`). Only the pay app page has the "Before you send" check, with the first-send warning "Check the AP email above; a typo sends it to a stranger." (`static/app.js:2427-2436`). Today's "Review & send" does not reach that check: it opens the job page (`open-job`, `static/app.js:601`, `static/app.js:1079-1082`), and the `.send-check` it scrolls to exists only on the pay app page (`static/app.js:2427`).
- **What a contractor hits today:** one tap emails the pay app. There is no recipient, no amount and no undo. Coming from Today's "Review & send" lands them on that same button.
- **Change:** make the job-page button open `/money/pay-app/<job>/<inv>` scrolled to `.send-check`, and rename it "Review & send". Point Today's "Review & send" at the same page (`open-payapp` with the draft's id) instead of `open-job`.

### A6. Landing page tells buyers billing and QuickBooks aren't real (S)

> Absorbed by **R2-02** (done).
- **Where:** `/` landing. `static/app.js:350-353` ("Planned pricing", "$299/mo", "Billing is not live yet."). `static/app.js:356` ("a direct QuickBooks connection is coming"). Meanwhile "Connect QuickBooks" exists (`static/app.js:2747`) and Stripe "Subscribe" exists (`static/app.js:1666-1676`). The trial isn't mentioned, although the banner states it (`static/app.js:1724`).
- **Change:**
  - Eyebrow: "Pricing".
  - Sub: "Free until the first pay app you send through Duracash is paid. Then $299/mo. No percent of collections."
  - Footer: "Reads payments from QuickBooks (connect it, or import a CSV). Never changes your books."
  - Keep the copy in step with Stephen's tier decision (free + $300) before launch.

### A7. First pay app can go out with only the company name in the From block (S)

> Absorbed by **R2-03** and **R2-05** (both done).
- **Where:** the onboarding card "Get your first pay app out", `static/app.js:1729-1753`, has 5 steps and none of them is company details. The Company card says "On every pay app and its PDF, in the From block." (`static/app.js:1629-1643`). With no address, phone or licence, the PDF's From block has only the company name (`payapp_pdf.py:166-168`).
- **Change:**
  - Add step 0, "Add your company address, phone and licence number (printed on every pay app)", with a button "Company details" that goes to `/settings` `#company-card`.
  - Count it as done when the address is non-blank.

---

## B. Manual entry: fewer fields, smart defaults, plain labels

### B1. New job: 11 fields down to 5 visible (M)

> Absorbed by **R2-01** (done).
- **Where:** `static/app.js:2126-2155`.
- **Today:** 3 sections and 11 inputs (Job name, Address, Contract number, Contract amount ($), Retainage %, Customer, Role, Approver, AP email, Phone, Notes for the job record), six of them required.
- **Change:**
  - Visible fields: "Job name", "Address", "Customer" (type-ahead, A2), "Contract amount", "Who pays you?" (B4).
  - Everything else goes under a collapsed "More details (optional)": Contract number, Retainage %, Phone, Notes, and the paperwork checklist from A1.
  - Retainage stays 0 by default. For a GC customer, pre-fill it from that payer's last job.
  - Drop the sub-line "The packet is made later, with the first pay app." (`static/app.js:2129`).

### B2. Money inputs reject "48,500" and "$48,500" (S)

> Absorbed by **R1-01** (done).
- **Where:**
  - New job amount: `static/app.js:2136` (type=number) and `static/app.js:1094` (parseFloat).
  - The same pattern recurs in Match payment, Progress and Record retainage paid (`static/app.js:2230`, `2233`, `2234`) and in Edit job.
- **Today:** a comma or "$" makes the field invalid, and the user gets "Enter the contract amount in dollars."
- **Change:**
  - Use `type="text" inputmode="decimal"` and parse with the existing `sovNum` (`static/js/sov.js:7-11`, which already strips `$`, commas and spaces).
  - Show the amount formatted ("$48,500.00") on blur.

### B3. Retainage % drops decimals without saying so (S/M)

> Absorbed by **R1-01** (done).
- **Where:** `static/app.js:2137` and `static/app.js:1095` (`parseInt`), Edit job `static/app.js:1154`.
- **Today:** 7.5 is saved as 7, with no warning.
- **Change:** accept one decimal on the client and the server. Until then, reject non-whole numbers with "Use a whole number from 0 to 20 for now."

### B4. "Role: Owner / GC" becomes "Who pays you?" (S)

> Absorbed by **R2-01** (done).
- **Where:** `static/app.js:2142`. The choice silently changes the default packet and the follow-up/approval days (`domain.py:1589-1602`).
- **Change:**
  - Label: "Who pays you?"
  - Options: "Homeowner or property owner" (value Owner) and "General contractor" (value GC).
  - Hint under it: "Sets the paperwork they usually need and when to follow up. You can change both later."

### B5. Progress pay app asks for a cumulative number with no context (M)

> Absorbed by **R3-01** (done).
- **Where:** `/jobs/<id>` > "Progress pay app". Placeholder "Completed to date $" (`static/app.js:2230`). Handler `static/app.js:895-901`. Server errors `domain.py:230-238`.
- **Today:**
  - A contractor types this month's amount. They get "Completed-to-date must be higher than the last recorded amount." or underbill.
  - The form shows neither the last recorded amount nor the contract.
- **Change:**
  - Add a line above the input: "Last recorded: $X of $Y contract (Z%)".
  - Add a toggle with "This period $" (default), "Total to date $" and "% complete". The form converts the entry to the to-date figure.
  - Show "This pay app: $A less retainage $R = $N" before "Draft progress pay app".

### B6. Match payment: no date, no "paid in full", jargon label (M)

> Absorbed by **R3-02** (done).
- **Where:** `/jobs/<id>` button "Match payment" (`static/app.js:657`). Form `static/app.js:2233` (placeholders "Amount in dollars", "Reference (check no., QBO id)"). Handler `static/app.js:997-1004`. `domain.match_payment` already accepts `received_on` (`domain.py:559`, used by the CSV import), but the form never sends it and the job-page route drops it (`server.py:2223` passes only amount, reference and invoice_id), so the server needs a one-line change too.
- **Today:**
  - Payments typed in later are dated today. That skews measured days to pay.
  - The user has to work out the balance themselves.
- **Change:**
  - Button and card title: "Record a payment".
  - Pre-fill the amount with the remaining balance of the selected pay app.
  - Add "Date received" (default today, sent as `received_on`, and passed through by the `match-payment` route).
  - Rename the reference field "Check or reference no.".
  - Close the card after saving.

### B7. Payer playbook: blank fields can't be cleared, labels are jargon (S)

> Absorbed by **R3-07** (done).
- **Where:** `/settings/playbook`, `static/app.js:2841-2852`. Save handler `static/app.js:1177-1182` skips blanks.
- **Change:**
  - Send blanks as clears, except Approver and AP email, which must stay set.
  - Rename the labels:

    | Today | New |
    |---|---|
    | "Packet" | "Paperwork needed with each pay app" |
    | "Rejects when" | "They send pay apps back when" |
    | "Copy" | "Also email (CC)" |
    | "Usual approval days" | "Days they usually take to pay" |
    | "Follow-up days" | "Days between follow-ups" |

### B8. Packet items: "Mark present…" jargon, wrong default record, no remove (M)

> Absorbed by **R3-05** (done).
- **Where:** `/money/packet/<job>/<inv>`.
  - `static/app.js:2371`: the record select preselects the first record.
  - `static/app.js:2372-2374`: "Mark present…", "Why it is present, e.g. emailed to the payer on 10/12", "Save reason".
  - There is no detach or unresolve action. Inbox Undo then dead-ends with "A packet already uses this record. Remove it from the packet first." (`importer.py:556-558`).
- **Change:**
  - "Mark present…" becomes "I have this", with an optional note "Where is it? (optional)". A reason stays in the audit when one is given; the server needs the matching change.
  - The select starts on "Choose a record…".
  - Each attached or marked item gets a "Remove" link.

### B9. Mark done and Close for record are one-tap and permanent (S)

> Absorbed by **R3-03** (done).
- **Where:**
  - "Confirm done" (`static/app.js:2232`, handler `static/app.js:902-906`) sets the job to 100% complete and drafts the final pay app (`domain.py:252-273`).
  - "Close for record" (`static/app.js:661`) posts with no confirm (`static/app.js:1043`). There is no reopen.
- **Change:**
  - Add `askConfirm` (already used for Void draft at `static/app.js:992-995`) to both:
    - "Mark <job> done? This bills the rest of the contract on a final pay app."
    - "Finish <job>? It moves to Job history and no more payments can be recorded."
  - Rename "Close for record" to "Finish job".

### B10. "Send waiver" sends with no type choice and no check for the file (S)

> Absorbed by **R1-06** (done).
- **Where:**
  - Job page primary "Send waiver" (`static/app.js:656`) posts with no kind (`static/app.js:913-916`, no `waiver-kind-job` element is rendered on the job page). The server picks the kind, and the email says the waiver "will follow separately" when no waiver record exists (`domain.py:639-642`).
  - Today's "Review waiver" opens the job, not the waiver page (`static/app.js:603`).
- **Change:** both buttons go to `/money/waiver/<job>`, which has the type select (`static/js/jobs.js:65-73`). Rename them "Choose waiver and send". When no record of kind "Lien waiver" exists, show "No signed waiver on the job yet" with "Take a photo" and "Choose file" in place.

### B11. Change orders: "Add change order" leaves the job (M)

> Absorbed by **R3-06** (done).
- **Where:**
  - `static/app.js:1015` (goto-co leads to the `/money/change-orders` list).
  - After "Create change order" the user stays on the list with a toast only (`static/app.js:1016-1027`).
  - "Signed on paper…" preselects the first record, and with no records it dead-ends with "Add the signed copy to the job record first (a photo or PDF), then pick it here." (`static/app.js:562-571`).
- **Change:**
  - Open an inline "New change order" card on the job page (Title, Amount, What changed). After creating it, show "Request signature" right there.
  - In "Signed on paper…", default to the newest record of kind Change order and add "Take a photo of the signed copy".

### B12. Daily record: silent failure, no remembered job (S)

> Absorbed by **R2-07** (done).
- **Where:** `/today/daily`, `static/app.js:2325-2338`. Handler `static/app.js:1033-1037` does nothing when the job or the notes are empty.
- **Change:**
  - Toast "Pick a job." or "Write what happened today."
  - Preselect the last job used (per-viewer `localStorage`, read and written inside try/catch).
  (A successful save already shows the "Saved" toast from `post()`, `static/app.js:487`.)

---

## C. Navigation and wording

### C1. Job page shows up to about 14 equal secondary buttons, explained only by hover tooltips (M)

> Absorbed by **R3-04** (done).
- **Where:** `jobActions` `static/app.js:643-674` (Clear N blockers, Approve & send, Send waiver, Match payment, Draft pay app, Progress pay app, Mark done, Close for record, Ask for retainage, Send pencil copy, Payer agreed to pencil, Payer wants changes, Follow up, Record retainage paid, Add change order, Open packet, Void draft, Import QBO sample). Only the primary's `why` is shown as text (`static/app.js:2215-2216`); the rest are rendered at `static/app.js:2235-2238` with `title=` only.
- **Change:**
  - Show the primary button plus at most 2 relevant secondaries, each with its `why` text as a visible line under it.
  - Put the rest in a "More" menu.
  - Group them under "Bill", "Get paid" and "Close out".

### C2. Plain words for office jargon (S each)

> Absorbed by **R6-01** (done).
Keep the trade term as secondary text where an accountant needs it.

| Today (file:line) | New label |
|---|---|
| "Match payment" (`static/app.js:657`) | "Record a payment" |
| "Close for record" (`static/app.js:661`) | "Finish job" |
| "Money file" (`static/app.js:2242`) | "Money" |
| "Send pencil copy" (`static/app.js:663`) | "Send draft for their review" |
| "Retainage open" (`static/app.js:2253`) | "Held back (retainage), still owed" |
| "File to job record" (`static/app.js:1801`) | "Add to job" |
| "Mark present…" (`static/app.js:2372`) | "I have this" |
| "Put on next pay app" (`static/app.js:2270`) | "Bill now (send waits for signature)" |
| History pill `words(e.action)` (`static/app.js:636`), e.g. "Approve send", "Match payment", "Draft skipped" | Map each action code to a sentence ("Pay app sent", "Payment recorded", ...) |

### C3. Onboarding buttons go to the wrong job, and hiding is permanent (S)

> Absorbed by **R2-03** (done).
- **Where:** "Open the job" always opens the first open job (`static/app.js:882`). "×" ("Hide this") hides the card for the whole company with no way back (`static/app.js:883`, `static/app.js:1746`).
- **Change:**
  - Each step opens the job that is missing that step, deep-linked: step 2 to the Job record card, step 4 to "Progress pay app", step 5 to the pay app page's `.send-check`.
  - Make dismiss per user.
  - Add "Show setup checklist" on the Help tab.

### C4. Gate errors vanish as one long toast (M)

> Absorbed by **R1-02** (done).
- **Where:** `post()` `static/app.js:481-483` joins blockers with "; " into a toast, for example "Cannot close this job yet. Work is not marked done.; ...".
- **Change:** for 409 answers, render a persistent card right under the button that was tapped. List each blocker on its own line, with its fix button where one exists (Clear blockers, Void draft, Mark done). The New job form already does inline errors (`static/app.js:1096-1103`), so reuse that style.

### C5. Follow-ups send with one tap, even when not due (S)

> Absorbed by **R3-03** (done).
- **Where:** Today "Send follow-up" (`static/app.js:618`), Collections "Send follow-up" (`static/app.js:2715`, shown on every unpaid job) and job page "Follow up". All post at once (`static/app.js:996`). When the follow-up isn't due, the server refuses with "Follow-up on ... is not due until ..." (`domain.py:474-482`).
- **Change:**
  - In Collections, disable the button and label it "Next follow-up <date>" while inside the payer's window.
  - Before sending, show a confirm "Email a follow-up on <PA> to <ap_email>?", the same check the helper already asks for.

### C6. QuickBooks connect is buried under Money > Payments (S)

> Absorbed by **R2-03** and **R4-11** (both done).
- **Where:** "Connect QuickBooks" exists only on `/money/payments` (`static/app.js:2742-2751`).
- **Change:** add a "QuickBooks" card on Settings > Team & account with the same button and status. Add an optional onboarding step, "Connect QuickBooks (optional)".

### C7. "Make a new address" fails silently for members (S)

> Absorbed by **R5-09** (done).
- **Where:** `static/app.js:982-987` uses raw `fetch` and ignores the reply. The route is owner-only (`server.py:2170`). The button is shown on the Inbox (`static/app.js:2002`, `2006`) and the Calendar (`static/app.js:2613`).
- **Change:**
  - Hide the button for members.
  - Use `post()` so errors show.
  - Toast "New address made. Update it wherever you used the old one."

### C8. Landing defaults to sign-up for returning users (S)

> Absorbed by **R2-02** (done).
- **Where:** `static/app.js:13` (`authMode = "signup"`). The card title is "Create your company" (`static/app.js:311`).
- **Change:** after any successful sign-in, keep a per-device flag in `localStorage` (inside try/catch). When it is set, open on "Sign in".

---

## D. Phone use

### D1. Inbox puts the work below two desktop-style cards (S)

> Absorbed by **R6-04** (done).
- **Where:** `/inbox`, `static/app.js:1964-1993`. The order is: dropzone "Drop files anywhere on this page", then the paste card, then "To file (N)".
- **Change:**
  - Show "To file (N)" first.
  - On touch devices, replace the dropzone with one sticky "Add" button that opens "Take a photo", "Choose files" and "Paste text".
  - Keep the drop text for desktop only.

### D2. Inbox card: two long selects to file one photo (M)

> Absorbed by **R5-05** (done).
- **Where:** `inboxItemCard` `static/app.js:1798-1803`. It shows the job select ("Pick a job…", "<name> (<code>)"), the kind select (9 options), "File to job record" and "Dismiss".
- **Change:**
  - When a job is suggested, show one big button "Add to <job name>", plus small links "Other job" and "Change type (<kind>)".
  - Make "Dismiss" a text link.

### D3. Packet page has no way to add the missing document (M)

> Absorbed by **R3-05** (done).
- **Where:** `static/app.js:2371`. With no records it shows "Add the document to the job record first." and a link away.
- **Change:** on every missing item, add "Take a photo" and "Choose file". These upload with `?job=<id>&file=1` (already supported by `uploadFiles`, `static/app.js:1204-1231`) and then call attach-record in one step.

### D4. Schedule of values tables are 6-7 columns wide (M)

> Absorbed by **R6-02** (done).
- **Where:**
  - The editor (`static/app.js:1350-1388`) has Item, Description, Qty, Unit, Unit rate $, Scheduled value and ×.
  - The progress table (`static/js/sov.js:27-46`) mixes "lump sum" percent and quantities in one "Done to date" column.
- **Change:**
  - Below 600px, use one card per line.
  - Label lump-sum inputs "% done" and quantity inputs "Qty done (<unit>)".

### D5. Calendar subscribe steps are desktop-only (S)

> Absorbed by **R6-04** (done).
- **Where:** `static/app.js:2610-2614` ("In Google Calendar on a computer: ...").
- **Change:** add an "Add to my phone's calendar" button that links to the `webcal://` form of the feed URL. Keep the copy row for Google.

### D6. Settings are three taps deep, and Team & account is one long page (M)

> Absorbed by **R6-03** (done).
- **Where:**
  - Navigation: `SECTIONS` `static/app.js:130-137`, with tabs inside the ☰ drawer (`static/app.js:688-714`).
  - `accountView` (`static/app.js:1607-1700+`) stacks You, Company, Team, Billing, Two-step sign-in, and Your data with Reset.
- **Change:**
  - Split it into "Company", "Team", "My account" and "Billing" tabs.
  - Move Reset and Delete behind a "Danger zone" link at the bottom.

### D7. Several photos upload one by one with no progress (S)

> Absorbed by **R6-04** (done).
- **Where:** `uploadFiles` `static/app.js:1204-1231` is sequential. One toast appears at the end.
- **Change:** show a line "Uploading 2 of 5…" and upload 2-3 files at a time.

### D8. Sharing to Duracash while signed out drops the files (M)

> Absorbed by **R6-10** (done).
- **Where:** `server.py:1911-1923`. The share target answers 303 to `/inbox` and stores nothing when there is no session or the company is read-only.
- **Change:** hold the shared files for a short time (or in the service worker). After sign-in, show "Finish adding 3 shared items".

---

## E. Import

### E1. Pasted notes aren't split unless the user changes the dropdown (S)

> Absorbed by **R5-06** (done).
- **Where:** `static/app.js:1979-1984`. The default is "An email", and splitting happens only for "Notes: one daily record per date".
- **Change:**
  - When two or more lines start with a date, switch to Notes automatically and show "Looks like notes from N days. One daily record per date."
  - Otherwise default to "Something else".

### E2. Job matching misses how crews actually write (M)

> Absorbed by **R5-02** (done).
- **Where:** `importer.py:326-353`. Matching needs the full address, the full job name or an UPPERCASE code. Payer name or AP email ties across jobs, which gives "More than one job fits. Pick one."
- **Change:**
  - Match street number plus street name, and the job code in any case when it stands alone.
  - On ties, offer the candidates as one-tap chips on the card (D2) instead of no suggestion.

### E3. Photos and PDFs from the Inbox never get a job suggestion (M)

> Absorbed by **R5-07** (done).
- **Where:** apart from saved emails (.eml, `server.py:1710-1712`), only .txt and .csv contents are read (`server.py:1730-1732`). Phone photos named IMG_xxxx.jpg match nothing.
- **Change:** when uploading from the Inbox, ask "Which job?" first. Preselect the job used last, and offer "Not sure, file later". Pass `?job=` so the item is pre-suggested.

### E4. QuickBooks CSV import: paste-only, and unmatched rows are dead ends (M)

> Absorbed by **R5-03** and **R5-04** (both done).
- **Where:** `/money/payments`, collapsed card "Import payments from QuickBooks (CSV)" (`static/app.js:2729-2741`). Only rows with status `match` get a checkbox (`static/app.js:2734`). An uploaded .csv goes to the Inbox as a note (`server.py:1730-1732`).
- **Change:**
  - Add "Choose the CSV file" next to the textarea.
  - On "Pick one", "Too much" and "No match" rows, show a pay app select prefilled with the candidates.
  - Before launch, test against two real QuickBooks exports (`qbo_csv.py:9-10` says the column names were written from memory).

### E5. A job page has no paste box, though the server supports it (S)

> Absorbed by **R5-06** (done).
- **Where:** the Job record card says "Emails and texts come in through the Inbox" (`static/app.js:2278`). The `import-text` handler already sends `?file=1` with `data-job` (`static/app.js:945-951`). The server supports it (`server.py:1754-1761`).
- **Change:** add "Paste an email, a text or notes" (the same textarea and select) to the Job record card, with `data-job="<id>"`.

### E6. Email and text forwarding needs developer setup (L)

> Deferred: improvement plan section 5 (a provisioned forwarding address and a shared texting number; paste and the share target are the day-one paths).
- **Where:** `static/app.js:1997-2008` ("SendGrid Inbound Parse, Mailgun routes or Postmark inbound", "buy a number", "Set TWILIO_AUTH_TOKEN on the server", "private web address").
- **Change:**
  - Duracash provisions one forwarding address per company and one shared texting number.
  - The card then reads only "Forward or BCC job emails to <address>" and "Text photos and notes to <number>".
  - The webhook details move to a "For your IT person" disclosure.

### E7. Inbox copy and helper answers contradict the product (S)

> Absorbed by **R5-09** (done).
- **Where:**
  - Inbox: "Photos sent by text show as links for now; they are not downloaded." (`static/app.js:2005`). README says the same (`README.md:82`). But `twilio_media.py:1-9` downloads them when TWILIO_AUTH_TOKEN is set.
  - The helper "quickbooks" answer says "Not yet. ... Today you record payments by hand with Match payment" (`helper/answers.json:1382`), while CSV import and Connect QuickBooks exist.
- **Change:** update all three to match the code.

### E8. Dismiss and File all give weak feedback (S)

> Absorbed by **R5-05** (done).
- **Where:**
  - "Dismiss" posts with no undo toast (`static/app.js:970`). Undo lives in the collapsed "Recently filed or dismissed" list, which keeps the last 20 (`static/app.js:1966`, `1994-1996`).
  - "File all N with a suggested job" shows only when more than one item has a suggestion (`static/app.js:1991`), and gives one "Saved" toast per item (`static/app.js:960-969`).
- **Change:**
  - After a dismiss, toast "Dismissed." with an "Undo" action.
  - Show File all from 1 item up and finish with one toast, "Filed N items".

### E9. A wrong guessed kind can't be fixed after filing (M)

> Absorbed by **R5-07** (done).
- **Where:** guessed kinds come from `importer.py:72-79` (any "invoice" becomes "Pay app / invoice backup"). Record rows on the job page have no edit control (`static/app.js:2280`).
- **Change:** add "Edit" on each job record row (label and kind). Map supplier invoices to "Delivery or time ticket" unless the item names a PA number.

---

## F. Polish

- **F1. Two-step copy is internal (S).** "Owners should turn this on before billing goes live." (`static/app.js:1690`). Change it to "Recommended for owners." Show a QR code on desktop (the setup has only a manual key, `static/app.js:1685-1689`). Absorbed by **R6-05** (done).
- **F2. README signup body is wrong (S).** `README.md:214` documents `{email, password, name, company_name}`, but the server requires `accept_terms: true` (`server.py:1161-1163`). Add it. Absorbed by **R6-10** (done).
- **F3. Change order "Signed copy" card is always empty (S).** `static/app.js:2406-2408` reads `co.attachments`, which nothing sets, so it always shows its "Drop the signed change order on the job record" hint, even after signing. The signature or paper copy already shows in the Summary card above it (`coSignature`, `static/app.js:2403`, `static/js/jobs.js:49-54`). Drop the Signed copy card, or make it point to "Signed on paper…" while the change order is pending. Absorbed by **R3-06** (done).
- **F4. Waivers list says "Pick when sending" (S).** `static/app.js:2790`. Show "Not chosen yet". Add a one-line hint per waiver type ("Unconditional: you already have the money"). Absorbed by **R6-10** (done).
- **F5. Collections shows full pay app amounts, not what's left (S).** `static/app.js:2706-2708`. Show the remaining balance on partly paid pay apps. Absorbed by **R3-02** (done).
- **F6. Pay app numbers skip after a void (S).** `domain.py:304` counts every invoice. Count only non-void pay apps, or label voided ones "(void)" in lists. Absorbed by **R1-09** (done).
- **F7. Delete job disappears once any invoice exists, even a voided draft (S).** `static/app.js:2211`. Allow delete when only voided drafts exist and there are no payments (`domain.py:1749-1750` needs the same change). Absorbed by **R3-09** (done).
- **F8. Share-menu file types don't match uploads (S).** `static/manifest.webmanifest:23` lacks .xls, .doc and .txt, which the upload path accepts (`static/index.html:15`). Absorbed by **R6-04** (done).
- **F9. Billing card hidden from members (S).** The Billing card renders only for owners with Stripe set up (`static/app.js:1666`). Members do see the trial banner on Today (`static/app.js:1718-1727`, `2057`), but not in Settings. Show the trial status to everyone in Settings too. Absorbed by **R6-10** (done).

---

## Top 10 to do first

1. **A1** Split "Notes for the job record" from the paperwork checklist, so a note can't become a send blocker. (M) *Done in R1-04.*
2. **A2 + B1** New job form: 5 visible fields, customer type-ahead, Approver defaults to the Customer, and AP email is asked at first send. (M) *Done in R1-05, R2-01.*
3. **A5** Make the job-page "Approve & send" and Today's "Review & send" open "Before you send". (S) *Done in R1-06.*
4. **A4** Show a check-your-email step, and block send with the reason while the email is unconfirmed. (S/M) *Done in R1-07.*
5. **A3** Add "Accept short pay", so partly paid jobs can be finished. (M) *Done in R4-01.*
6. **B2 + B6** Money inputs accept "$48,500". "Record a payment" pre-fills the balance and asks for the date received. (S/M) *Done in R1-01, R3-02.*
7. **B5** Progress pay app: show last recorded and contract, and accept "This period $". (M) *Done in R3-01.*
8. **B9 + B10** Confirm before Mark done and Finish job. Route waivers to the waiver page. (S) *Done in R3-03, R1-06.*
9. **D3 + B8** "Take a photo" on each missing packet item, plus "I have this" and "Remove". (M) *Done in R3-05.*
10. **A6 + A7** Fix the landing pricing and QuickBooks copy, and add Company details as the first onboarding step. (S) *Done in R2-02, R2-03, R2-05.*
