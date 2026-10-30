# Duracash client flows

For Stephen to review. Each chart shows what a contractor taps, screen by screen, to get one task done. Every screen name and button label is copied from the app code (`static/app.js`, `static/js/*.js`, `payer_page.py`, `domain/__init__.py`, `server.py`), regenerated from the code on Oct 29, 2026 after Round 6 (R6-11) and checked again against the code in the final pass the same day; the file:line after a label is where it lives today (a bare `:N` is in the file named last before it). Brought up to Stephen's Oct 29 review on Oct 30: the seven sections, Client for the people who pay, and the page modules split by tab. Read each chart from top to bottom. Diamonds are real branches the code takes.

The demo company is Hartline Remodeling (`seed.py`), opened with **Look around a sample company** on the landing page (no account) or the login `owner@hartline.example` / `demo`. The demo can't send email, so some steps end at "Not sent: demo account". The wireframes in `docs/wireframes/` draw the same screens; `Job`, `PhoneJob`, `Money`, `PaymentsImport`, `Inbox`, `Settings` and `PayerPage` were redrawn with this file in R6-11, and `Dashboard`, `PhoneToday`, `Packet`, `PhonePacket`, `SendCheck` and `Playbook` in the final pass.

Screen names used below (pages and tabs: `SECTIONS`, static/app.js:29-37; addresses: static/js/routes.js:9-32):

| Screen | Address |
|---|---|
| Dashboard: Overview, Results, Calendar | `/dashboard`, `/dashboard/results`, `/dashboard/calendar` |
| Jobs: Open jobs, Daily record, Job history, Client playbook | `/jobs`, `/jobs/daily`, `/jobs/history`, `/jobs/clients` |
| Job page | `/jobs/<id>` |
| Communications: Inbox, Outbox | `/communications`, `/communications/outbox` |
| Import/Export: Import, Export | `/data/import`, `/data/export` |
| Money: Pay apps, Change orders, Unpaid, Payments, Closeout | `/money`, `/money/change-orders`, `/money/collections`, `/money/payments`, `/money/waivers` (`/money/retainage` opens it at Held back, still owed) |
| Automations (under construction) | `/automations` |
| Pay app, Paperwork, Change order and Waiver pages | `/money/pay-app/<job>/<id>`, `/money/packet/<job>/<id>` (`closeout` for the closeout paperwork), `/money/change-order/<job>/<id>`, `/money/waiver/<job>` |
| Settings: Company, Team, My account, Billing, Activity log | `/settings/company` (`/settings` opens it), `/settings/team`, `/settings/account`, `/settings/billing`, `/settings/audit` |
| The client's page for one pay app; the signing page for one change order | `/p/<link>`, `/s/<link>` (no sign-in; `payer_page.py`) |

The menu has seven sections on a computer; on a phone it shows five and puts Import/Export and Automations under **More** (Stephen's Oct 29 review). The old addresses still open their page at its new one (`PATH_ALIASES`, static/js/routes.js): `/today...` the Dashboard's, `/inbox` and `/money/outbox` Communications, `/settings/playbook` the Client playbook, `/settings/help` My account (Help was emptied; **Show the setup checklist** and the account id are on My account). The in-app **←** button goes back to the page you came from and names it; opened from a link it goes to the page's own list (G3, static/js/trail.js).

On the job page the **Next step** card holds one main button and, beside it, the next two as two-line buttons (the label, then why). The rest sit under **More (N)**, grouped Bill, Get paid and Close out. The main button is picked in this order: the blocker button (**Fix N things before sending**, **Add N missing documents** or **Get CO N signed**), **Review & send**, **Choose waiver and send**, then, while a sent pay app is unpaid, **Send follow-up** once it is due and **Draft pay app**; otherwise **Draft pay app**, **Progress pay app**, **Finish job**, **Ask for retainage** (`jobActions`, static/js/jobs.js:344-394). While the client has the pay app and no follow-up is due there is no main button. When something blocks the send, the card's line reads **Before it can go:** and lists every blocker under it, with **Client sent a pay app back? Note why.** folded at the bottom (J6; `nextStep(j, now, { listed })`, static/js/jobs.js:68). Above it, **Where this job stands** shows the stage (Started, Work in progress with the percent billed, Pay app out, Approved, Paid, Work done, Retainage released, Finished) and the key dates: start date, this and next month's pay app due day, last pay app sent, payment due, next follow-up, marked done, retainage asked and the next ask, finished (J7; `standingCardHtml` static/js/views/job.js:142, from the server's `job_stage` and `job_key_dates`, domain/jobinfo.py:87 and :127). The job's log at the bottom is **Activity** (`jobHistoryHtml` static/js/views/job.js:128). Nobody sees a button their permission profile doesn't allow (send, waivers, retainage requests, record payments, delete a draft, pull back, finish, delete, accept short pay, Add more work; `ACTION_PERM`, static/js/jobs.js:330, `JOB_ACTION_PERMS`, domain/profiles.py:52, refused on the server too); the built-in Member profile records payments and drafts, and Settings, Team can give it, or a profile of your own, more (section 16).

A company set to bill homeowners in draws (a setting from before Oct 29; How you bill is no longer on the Company page) reads Draw and Draws wherever this file says pay app (`termText`, static/js/terms.js; `domain.term_text`).

---

## 1. The whole life of a job

```mermaid
flowchart TD
    A["Landing page: 'Create account'"] --> B["Dashboard: 'Get your first pay app out' checklist"]
    B --> C["Jobs: 'New job' then 'Save job'"]
    C --> D{"Bill line by line?"}
    D -->|"Yes, trade sub"| E["Job page: 'Set up schedule of values'"]
    D -->|"No, one contract amount"| F
    E --> F["Job record: 'Take a photo', 'Add a file', or the Inbox: contract, photos, tickets"]
    F --> G{"Work finished?"}
    G -->|"Not yet"| H["Job page: 'Progress pay app', then 'Draft progress pay app'"]
    G -->|"Yes"| I["Job page: 'Mark done', 'Confirm done', confirm"]
    H --> J["Draft pay app made, with its paperwork list"]
    I --> J
    CO["Extra work: 'New change order', client signs"] -.-> J
    J --> K{"Paperwork missing or a change order unsigned?"}
    K -->|"Yes"| L["Paperwork page: 'Take a photo', 'Choose file', 'Attach' or 'I have this…'"]
    L --> K
    K -->|"No"| M["Pay app page, 'Before you send': 'Approve & send to' the client's email"]
    M --> N{"Paid?"}
    N -->|"Not yet"| O["'Send follow-up' once the client's days between follow-ups pass"]
    O --> N
    N -->|"Part paid"| P["'Record a payment'; 'Accept $X short pay…' if the rest won't come"]
    P --> N
    N -->|"Paid in full"| Q["'Choose waiver and send'"]
    Q --> R{"More work on this job?"}
    R -->|"Yes, next month"| G
    R -->|"No, job done"| S{"Money held back?"}
    S -->|"Yes"| T["'Ask for retainage', then 'Record retainage paid'"]
    S -->|"No"| U
    T --> U["'Finish job' moves it to Job history"]
```

The money loop is the middle: draft, add the paperwork, send, follow up, record the payment, send the waiver, then back to next month's progress. A job usually goes round it once a month. Each step shows up on the Dashboard as a row, and in the owners' daily email, so the contractor can run the loop from Dashboard (section 14).

---

## 2. First day setup

**Goal:** create the company, confirm the email, and fill in the company details that print on every pay app. The screens are the wireframes `Landing`, `CheckEmail`, `FirstRun`, `NewJob` and `Settings`.

```mermaid
flowchart TD
    A["Landing page: 'Create your company' card, or 'Sign in' on a device that has signed in before"] --> A2{"Just looking?"}
    A2 -->|"Yes, and the server has the sample company"| DEMO["'Look around a sample company': the demo's Dashboard, no account"]
    A2 -->|"No"| B["Fill Your name, Company name (you can add it later), Email, Password"]
    B --> C["Tick 'I agree to the Terms of Service and the Privacy Policy.'"]
    C --> D["Tap 'Create account'"]
    D --> E{"Name, email, 10+ character password, box ticked?"}
    E -->|"No"| E1["Red note under that box; nothing typed is lost"]
    E1 --> B
    E -->|"Email already has an account"| E2["'Sign in instead' under the email"]
    E -->|"Yes"| F["Dashboard: 'Check your email' panel, 'Get your first pay app out' checklist"]
    F --> F1{"Did a link go out?"}
    F1 -->|"Yes"| G["Email: tap the confirmation link"]
    F1 -->|"No: mail not set up here"| F2["Panel reads 'Your email isn't confirmed yet' with the reason"]
    G --> H{"Link still good? 3 days"}
    H -->|"Yes"| I["Email confirmed: sending is open"]
    H -->|"No"| J["'Send the link again' on the panel or the banner"]
    J --> G
    F --> K["Checklist step 1: 'Company details'"]
    K --> L["Settings, Company: card 'Company details', cursor in Street address"]
    L --> M["Fill Company name, Phone, Street address, City, State, ZIP code, Contractor licence number; tap 'Save' at the bottom"]
    M --> N["Step 1 ticked; next: 'New job' (section 3)"]
    N --> O{"Office help?"}
    O -->|"Yes"| P0{"Your own email confirmed, or no mail on this server?"}
    P0 -->|"No"| P1["Settings, Team: Invite is off, with 'Confirm your own email address before inviting people.' and 'Send the link again'"]
    P1 --> G
    P0 -->|"Yes"| P["Settings, Team: 'Their email', 'Profile' (Owner, Member or one of your own), 'Invite'"]
    O -->|"No"| Q["Done: the checklist leads to the first job, its paperwork and the first send"]
    P --> R{"Email confirmed and mail set up?"}
    R -->|"Yes"| S["Invite emailed, good once for 7 days"]
    R -->|"Not emailed"| T["Copy the link shown and send it yourself"]
    S --> Q
    T --> Q
```

Code: landing and sign-up card `renderLanding`, static/js/views/auth.js:45 (Sign in first when this browser has signed in, static/js/views/auth.js:11-17; the terms box static/js/views/auth.js:94); **Look around a sample company** static/js/views/auth.js:71 and `lookAround` static/js/views/auth.js:219, server `_p_demo`; "Check your email" panel `checkEmailHtml`, static/js/views/auth.js:374 (**Send the link again**, **Got it**); checklist `onboardingHtml`, static/js/views/today.js:172-216, its step 1 **Company details** opens Settings, Company at `#co-address`; Company page `companyView`, static/js/views/settings.js:198; **Save** `save-company` static/js/views/settings.js:686 (the details, the address in four boxes and the switches together, S1 and S3; the server checks each box, `update_company`); Team `teamCardHtml`, static/js/views/settings.js:76, **Invite** `team-invite` static/js/views/settings.js:601 (off, with the reason and **Send the link again**, while `inviteLocked` static/js/setup.js:41; the server still refuses, `_p_team_invite`). Two-step sign-in is on for every account (section 16).

**Taps from start to done:** about 5. Create account (1, after typing 3 boxes and the tick), the email link (1), Company details on the checklist (1, lands in the Street address box), Save (1), Invite (1, optional).

**What changed since Round 2:** Settings is four pages (Company, Team, My account, Billing) instead of one long Team & account page (R6-03); the checklist and Before you send name Settings, Company. Sign-up is limited to 5 an hour per network (R6-05). The Company page has **Sending and email** (who may send, the daily email) for owners.

**Where it could still be simpler:**
- Hiding the checklist is per person, so a teammate who joins later sees it until they hide it (it can be brought back from Settings, My account).

---

## 3. Add a job by hand

**Goal:** get a job and the client who pays it into Duracash. Clients can also be added on their own (section 15).

```mermaid
flowchart TD
    A["Jobs page or the Dashboard checklist: tap 'New job'"] --> IC{"Have the contract as a file or text?"}
    IC -->|"Yes"| IM["'Import contract' (Word, PDF or text) or 'Paste the contract's text', 'Read it': the boxes it finds fill in, each marked 'From the contract'"]
    IC -->|"No"| B
    IM -->|"A scanned PDF"| IS["'Couldn't read text from this file; fill the fields in by hand.'"]
    IM --> B
    IS --> B
    B["Card 'New job': Job name, Street address, City, State, ZIP, Client (with a list of saved clients), Client type (a new client only), Contract amount"]
    B --> C{"Client already saved?"}
    C -->|"Yes"| C1["Line under the boxes: the client's type, who signs off and where pay apps go, with 'Change in the Client playbook'"]
    C -->|"No"| D
    C1 --> D["Optional: 'More details (optional)': Contract number, Retainage %, Start date (today), Assigned to (with a team), Phone, and for a new client Pay apps due by, Payment terms, Who signs off on your bills, Email for pay apps (can add later); Notes (kept on the job record)"]
    D --> E["Tap 'Save job'"]
    E --> F{"Each box as the field rules say? (a box is also checked when you leave it)"}
    F -->|"No"| G["Red note under the box, for example 'Enter a 5-digit ZIP code, like 30303, or ZIP+4, like 30303-1234.'"]
    G --> B
    F -->|"Yes"| H{"Client name already known?"}
    H -->|"New client"| I["New client made with its type's default paperwork list"]
    H -->|"Known client (same name, ignoring case)"| J["The client's settings are kept"]
    H -->|"Nearly the same as a saved one: 'ABC Builders Inc', 'abc builders, LLC'"| N["Under Client: 'Same as ABC Builders? Use the saved client, or keep ABC Builders Inc as a new one.' Nothing is saved yet"]
    N -->|"'Use ABC Builders'"| J
    N -->|"'Keep “ABC Builders Inc” as a new client'"| I
    I --> K["Job page opens; an imported contract is on its job record as Contract, with its schedule of values if one was ticked"]
    J --> K
```

Code: `views.jobs`, static/js/views/jobs.js:326-395 (**New job**, the boxes, **More details (optional)**, **Save job**); `jobFields` static/js/views/jobs.js:24 (the address as four boxes, J1, and Start date, J8) and its checks `jobFieldErrors` :53, with the field rules of static/js/validate.js (G1, checked on leaving a box by static/js/forms.js); the saved-client line `knownPayerText` :85; **Import contract** `contractImportHtml` :206, `readContract` :249 (POST /api/contract/read, server `contract_import.read`, contract_import.py:35, which reads Word files, text PDFs and pasted text and saves nothing), `fileContract` :265 (the file goes on the record as kind contract after the save); **Save job** `create-job` :428; server `create_job`, domain/__init__.py:3582 (each box checked again by the same rules in domain/validate.py; a refusal is keyed by field and shown under its box, `showFieldErrors` in static/js/forms.js; the address is kept whole too, "Street, City, ST 12345", for the pay app PDF, the client's page and Inbox matching). Client type has five kinds: General contractor and Another subcontractor use a general contractor's defaults, Owner or developer, Homeowner and Public agency an owner's (`CLIENT_TYPES` static/js/jobs.js:462, `client_type_role` domain/jobinfo.py:31). Notes become a "Job notes" record, never paperwork (R1-04). A near match (Oct 23 note 5) is found by `payer_near` domain/__init__.py:2118, which compares names with `customer_key` (:2103: case, spaces, punctuation, "The" in front and Inc, LLC, Co, Corp, Company, Ltd at the end don't count); the refusal carries `same_customer` and `showSameCustomer` static/js/views/jobs.js:103 shows the two buttons, **Use ABC Builders** `nj-same-use` :482 and **Keep “ABC Builders Inc” as a new client** `nj-same-keep` :492 (sent again with `new_customer: true`). Nothing is merged without a tap.

Jobs saved before the address had four boxes keep their one line: Edit job shows it all in Street address until someone splits it (migration step `_jobs_clients_address_parts`, migrations.py:111, which also splits a line that reads as "Street, City, ST 12345").

**Taps from start to done:** 2 taps (New job, Save job) and 8 boxes, two of them selects; with a contract file, 3 (Import contract, pick it, Save job) and a check of each box.

**Where it could still be simpler:**
- The paperwork list for a new client is set by its Client type and only shows once the first pay app is drafted; it is changed in the Client playbook, not on the New job form.
- Reading a contract is plain rules, not a reader of any layout: a scanned PDF, or a contract that words the sum or the parties unusually, leaves those boxes for you to fill.

---

## 4. Set up the schedule of values

**Goal:** bill line by line (quantity times rate), the way a GC wants a trade sub to bill.

```mermaid
flowchart TD
    A["Job page, card 'Schedule of values': tap 'Set up schedule of values'"] --> B{"Have it in a spreadsheet or estimate?"}
    B -->|"Yes"| C["Paste rows into the paste box, then tap 'Read pasted rows'"]
    C --> D["Lines appear to check; a title row above the header is skipped and said so; a row like '3 loads' (or 'loads, 3', or a Qty with a blank Rate under a header) comes in with the rate blank and 'needs a price'"]
    B -->|"No"| E["Type Item, Description, Qty, Unit, Unit rate; 'Add line' for more"]
    D --> F["Optional: Overhead and profit %"]
    E --> F
    F --> G["Tap 'Save schedule'"]
    G --> H{"Every line has a description, a quantity and a rate?"}
    H -->|"No"| I["The lines to fix are named ('One line needs a price. Enter its unit rate, then save.' when only the price is missing)"]
    I --> E
    H -->|"Yes"| J{"Schedule total equals the contract?"}
    J -->|"Yes"| K["Saved: the card shows the line table ('Edit schedule' until the first pay app; while that pay app is a draft, 'Delete draft' there)"]
    J -->|"No"| L["Error: the schedule total and the contract differ"]
    L --> M{"Change the contract to match?"}
    M -->|"Yes"| N["Tap 'Save and change the contract to $X', then confirm 'Change the contract'"]
    M -->|"No"| E
    N --> K
```

Code: `sovCardHtml`, static/js/views/job-sov.js:118-163 (**Set up schedule of values** static/js/views/job-sov.js:133, **Edit schedule** static/js/views/job-sov.js:159); editor `sovEditorHtml`, static/js/views/job-sov.js:78-116 (**Read pasted rows** static/js/views/job-sov.js:93, **Add line** static/js/views/job-sov.js:101, **Save schedule** static/js/views/job-sov.js:112, **Save and change the contract to $X** static/js/views/job-sov.js:74 and static/js/views/job-sov.js:113); the confirm static/js/views/job-sov.js:189; parse `sov.parse_rows`, sov.py:540 (a header found in the first 5 rows, R5-08; one number next to a unit word in `UNIT_WORDS` sov.py:420, after it or just before it, checked by `_counts_units` sov.py:510, is a quantity with no price, sent back with `needs_price` and a blank rate; under a header, a quantity with the rate and amount blank comes back the same way); the editor marks it with `sovLineProblem`, static/js/sov.js:53 ("needs a price" static/js/views/job-sov.js:61, the toast static/js/views/job-sov.js:186); save `save_sov`, domain/__init__.py:3773, which refuses a blank rate ("needs a price (unit rate)", sov.py:348). While the only pay app is a draft, the card's note and **Delete draft** come from `sovDraftLockHtml`, static/js/sov.js:68 (used at static/js/views/job-sov.js:127 for a job with no schedule and static/js/views/job-sov.js:137 for one with a schedule), from `draft_lock` in `_sov_view` (`_sov_draft_lock`, domain/__init__.py:3947); the button is the `void-draft` action and its confirm. On a phone each line is a card with the description on top (R6-02).

**Taps from start to done:** 3 with a paste (Set up schedule of values, Read pasted rows, Save schedule). One more (the confirm) if the contract changes to match.

**Where it could still be simpler:**
- The schedule is fixed once the first pay app exists, even an unsent draft. While the only pay app is a draft the card says "The schedule is fixed while PA-x is a draft. Delete the draft to change it." with a **Delete draft** button (owners; anyone else reads "An owner can delete the draft to change it." and no button); after one is sent it says "Fixed once the first pay app exists. Scope changes go through change orders, which add their own lines when approved." A mistake found on the second pay app means a change order. A job with no schedule whose only pay app is a draft shows one line instead of the card: "To bill line by line, delete draft PA-x first, then set up a schedule of values." with **Delete draft** (anyone else reads "To bill line by line, an owner has to delete draft PA-x first. Then you can set up a schedule of values."). Once a pay app is sent, a job with no schedule hides the card.

---

## 5. Import existing paperwork

**Goal:** get contracts, photos, tickets, emails and old notes onto the right job without retyping.

```mermaid
flowchart TD
    A{"Where are you?"} -->|"On the job page"| B["Job record: 'Take a photo' or 'Add a file', drop files, or 'Paste an email, a text thread, or notes' then 'Add to job'"]
    A -->|"On Jobs"| JR["'Add to a job record' (or 'Add to record' on a job's row, with that job picked): Job, then 'Take a photo', 'Add a file', a paste, or 'Add a note to the record' with its Kind"]
    JR --> C
    B --> C["Toast: 'N files added to the job record.' Done"]
    A -->|"Anywhere else"| D["Import/Export, Import (or drop or paste a file on any page)"]
    D --> E{"What do you have?"}
    E -->|"Files or photos"| F["'Which job?' (or 'Not sure, file later'), then 'Take a photo' / 'Choose files', a drop, or a pasted screenshot"]
    E -->|"Text"| G["Card 'Paste an email, a text thread, or notes'"]
    G --> H["Paste: the box picks An email, Text messages or Notes when it looks like one, and says so"]
    H --> K["Tap 'Add to Inbox'"]
    F --> L["Communications, Inbox: cards under 'Waiting (N)'"]
    K --> L
    L --> M{"Job suggested?"}
    M -->|"Yes, several cards"| N["Tap 'Add all N to their suggested jobs'"]
    M -->|"Yes"| O["Tap 'Add to (job)' (or 'Other job' / 'Change type')"]
    M -->|"Could be one of a few"| P["Tap the job's name, or 'Pick a job…' and 'Add to job'"]
    M -->|"A payments export"| PAY["'Read as payments (N rows)': Import opens with the rows, section 10"]
    M -->|"Junk"| Q["Tap 'Dismiss'"]
    N --> R["Listed under 'Everything received' with what became of it and why, 'Undo' and 'Print'"]
    O --> R
    P --> R
    Q --> R
```

Code: job record card `recordCardHtml`, static/js/views/job-record.js:40-62 (**Take a photo** / **Add a file** `pick-files` static/js/views/inbox.js:447, paste box `pasteBoxHtml` static/js/views/inbox.js:185 with **Add to job**); record rows with **Edit** `recordRow` static/js/views/job-record.js:18 (R5-07), in groups with their counts and a filter row (All, Contract and change orders, Daily records, Photos, Tickets, time sheets and supplier invoices, Lien waivers, Insurance and certificates, Inspections and closeout, Pay app backup, Notes and other; J5, `recordGroupsHtml` static/js/views/job-record.js:44, `RECORD_GROUPS` static/js/jobs.js:480); **Add to a job record** on Jobs `addRecordCardHtml` static/js/views/jobs.js:289 (J4: open jobs only, `jr-open` :516, `jr-paste` :525, `jr-add-note` :533); Import `views.import`, static/js/views/data.js:47-67 (the file box `dropzoneHtml` static/js/views/inbox.js:166 with **Which job?** and the touch-only **Take a photo** / **Choose files**, the paste box, the payments import of section 10, and **Schedule of values**: a job and **Open its schedule** `import-sov` static/js/views/data.js:112, which opens the job's schedule card for pasting); Inbox `views.inbox`, static/js/views/inbox.js:413-442 (**Waiting (N)**, a phone's **Take a photo**, **Everything received**, **Bring emails and texts in automatically**); **Everything received** `receivedHtml` static/js/views/inbox.js:265 (Received, From, Subject with the files, Came in by, Status in words `receivedStatus` :238 with how it was matched; filters **Came in by**, **Status**, **Job** and **Sort** `setReceivedFilter` :220; **Undo**, **Print** `print-received` :518), paged by the server `GET /api/inbox` (`_g_inbox_page` server.py:1183, `domain.inbox_page` domain/comms.py:78, the reason in words `_why` domain/comms.py:46); the page load carries only what waits and the last 20 others (`inbox_for_page_load` domain/comms.py:25, from `light_snapshot` domain/__init__.py:1606); cards `inboxItemCard` static/js/views/inbox.js:288 with `choiceJobs` / `choiceLine` / `pasteGuess` in static/js/inbox.js; uploads three at a time `uploadFiles` static/js/views/inbox.js:44; server matching `importer.match_job`, importer.py:466 (a job code in capitals, or in small letters as its own word when no job's name or address has that word; the full job name or a part of it no other job has, like Oak Street for Oak Street kitchen, or one telling word written with a capital, like Vesta; a code in small letters or one name word counts less than the client's AP email; a tie suggests nothing), kinds `guess_kind` importer.py:386, notes split `split_notes` importer.py:357, filing `file_item` importer.py:635; **Undo** `undo_item` importer.py:782, refused while paperwork uses the record by `_paperwork_refusal` importer.py:735, with a link in the refusal card (`showRefusal` static/js/app-core.js:348).

**Taps from start to done:** 2 from the job page (Take a photo or Add a file, pick); 3 from Jobs (Add to record on the row, Add a file, pick). From Import: 2 to add, then 1 per card in the Inbox to file, or 1 for all suggested cards.

**Where it could still be simpler:**
- Forwarding email and texts in needs an email service or a Twilio number set up by an owner (deferred: a provisioned address per company, improvement plan section 5).
- Once a record is attached by hand to paperwork, Undo on the Inbox is refused. For a draft it links to that pay app's Paperwork page, where Remove is. For a pay app that went out (whether the record was attached by hand or automatically) it links to the pay app: the record stays as sent, and pulling the pay app back (only while nothing is paid) is the way to change what went out.

---

## 6. Change order

**Goal:** record extra work (or a credit) with its reason and price, get it signed, and bill it.

```mermaid
flowchart TD
    A{"Where are you?"} -->|"Job page"| A1["Tap 'New change order'"]
    A -->|"Money, Change orders"| A2["Tap 'New change order'; with more than one open job, 'Which job?' and 'Open the form'"]
    A2 --> B
    A1 --> B["Card 'New change order' on the job page: Title, Reason (Owner request, Unforeseen condition, Design issue, Value engineering, Other), Description (required for Other), Contract section or original scope it changes (optional), Pricing (Lump sum or Time and materials), Amount ($), negative for a credit"]
    B --> B2["Optional: 'Add a cost line' (label and amount; their total becomes the amount), Schedule change, days, Work started on, Backup from the job record"]
    B2 --> D["Tap 'Create change order'"]
    D -->|"No reason"| D2["'Pick why the scope changed.'"]
    D2 --> B
    D --> E["The card becomes 'CO N: title', 'Saved. Now get it signed.'"]
    E --> F{"How will the client sign?"}
    F -->|"Online"| G["Tap 'Request signature'"]
    G -->|"Server can't make signing links yet"| G2["'Signing links aren't available on this server yet. Contact Duracash support.' Sign on paper meanwhile"]
    G2 --> L
    G --> H{"Client has an email for pay apps?"}
    H -->|"Yes"| I["Signing link emailed; the button becomes 'Send the signing link again'"]
    H -->|"No"| J["Link made, not emailed: 'Signing page: open it' to share it yourself"]
    J --> K
    I --> K["Signing page: Scope, Price and Schedule; tick 'I agree to the scope, price and schedule…', type 'Your full name', tap 'Sign change order'"]
    F -->|"On paper"| L["Tap 'Signed on paper…': 'Signed by (name)', 'Date signed' (no later than today)"]
    L --> M["'Take a photo of the signed copy' (added to the job record and picked), or choose it from the job record, then 'Save as signed'"]
    F -->|"Bill it now, sign later"| N["No open draft: tap 'Bill now (send waits for signature)'"]
    N --> O["The pay app can't be sent until it is signed: 'Get CO N signed'"]
    O --> F
    F -->|"Dropped"| P["Tap 'Withdraw', confirm"]
    K --> Q["Change order Signed: adds to the contract and, with a schedule of values, gets its own line"]
    M --> Q
    Q --> R["Change order page: 'Client logged it on their end' once the client has entered it"]
    E -.->|"Work began before the signature"| W["'Work started…' on the change order page; amber 'Work started before signing' on the page, the list and the job page"]
```

Code: **New change order** `new-co` static/js/views/job-cos.js:165 (a job action, static/js/jobs.js:390); card `newCoCardHtml` static/js/views/job-cos.js:74 and `coFormFields` static/js/views/job-cos.js:92 (**Add a cost line** `co-add-item` :167); **Create change order** `add-co-here` static/js/views/job-cos.js:173, checked first in `addChangeOrder`; server `_add_co` server.py:2877 and `add_change_order` domain/__init__.py:2486 (reasons `CO_REASONS` :2425; refuses a missing reason, Other without a description, more than 50 cost lines, an amount that differs from the cost lines, backup not on this job's record, a start day in the future, zero, and a credit larger than the contract total). On Money, Change orders (`views.cos`, static/js/views/changeorders.js:117) the list has no form: **New change order** `co-new` :168 opens the job's form straight away when only one job is open, else asks **Which job?** and **Open the form** `co-new-open` :175. Sign controls `coSignControls` static/js/views/job-cos.js:45 (**Request signature**, **Signed on paper…** with **Signed by (name)** and **Date signed**, **Withdraw**, **Take a photo of the signed copy**, **Save as signed** `approve-co` static/js/views/changeorders.js:182); server `request_co_signature` domain/__init__.py:2661 (without the site address it refuses with `NO_SIGNING_LINKS`, :2669, and the server log names DURACASH_URL, `_request_co_signature` server.py:2898), `mark_co_signed` domain/__init__.py:2710 (needs the signer's name and a date no later than today; signing, online or on paper, takes "(unsigned)" off the line of an open draft that billed it, so the PDF no longer says so), `withdraw_change_order` :2820; **Bill now (send waits for signature)** in `views.job` (static/js/views/job.js:349), server `include_change_order`, domain/__init__.py:2958; signing page `render_co`, payer_page.py:311 (Scope, Price and Schedule from `co_snapshot`, domain/__init__.py:2621, version 2; an older signature keeps its own snapshot and hash). The change order page `views.coDetail`, static/js/views/changeorders.js:69: Summary (reason, cost lines, pricing, schedule, work started, Waiting on), **Client logged it on their end** `co-logged` :197 (server `mark_co_logged`, domain/__init__.py:2797, only once signed), **Work started…** `co-work-started` :199 (server `set_co_work_started` :2782; the flag is `co_started_unsigned` :2756), Backup, the Contract sum card `coContractHtml` static/js/views/changeorders.js:45 ("if signed" while unsigned) and History `coHistoryHtml` :30 (created, put on the bill, signature requested, signed, withdrawn, taken off a deleted draft, work started, client logged it, each with who and when; older change orders get theirs from the job's audit rows, migrations.py `_payapps_cos_co_history`).

**Taps from start to done:** 2 to create (New change order, Create change order) after typing, then 1 for online signing (Request signature) or 3 on paper (Signed on paper…, Take a photo of the signed copy, Save as signed).

**Where it could still be simpler:**
- A change order has no PDF of its own yet (improvement plan section 5).
- A change order can't be edited once made; withdraw it and make a new one.

---

## 7. Paperwork (what the client wants with each pay app)

**Goal:** get every missing item in so the pay app can be sent.

```mermaid
flowchart TD
    A["Job page or Dashboard row: 'Fix N things before sending' or 'Add N missing documents'"] --> B["Paperwork page 'Paperwork for PA-x': 'What (client) needs to pay', then 'Required items', each ✓, ✗ or –"]
    B --> C{"Item missing?"}
    C -->|"No, all in"| D["Pay app is 'Ready to send'"]
    C -->|"Yes"| E{"Where is the document?"}
    E -->|"In your hand or on the phone"| F["'Take a photo' or 'Choose file': filed on the job record and attached in one step"]
    E -->|"Already on the job record"| G["'From the job record this period', grouped by kind: 'Attach to…', then 'Attach'"]
    E -->|"The client has it already"| H["'I have this…', 'Where is it? (optional)', 'Save'"]
    E -->|"This client never needs it"| I["'Not needed for this client'"]
    F --> C
    G --> C
    H --> C
    I --> C
    C -->|"A change order is unsigned too"| J["'Also blocking send': 'Open CO N', section 6"]
    B -.->|"Conditional waiver item"| W["'What the signed waiver must show': 'Check the signed form shows' and 'State form'"]
    T0["Job page while blocked: the Next step card, 'Before it can go:' and every blocker"] --> K{"Client sent a pay app back before?"}
    K -->|"Yes"| L["'Client sent a pay app back? Note why.': type the reason, 'Save to the client playbook'"]
```

Code: the blocker button's words `domain.blocker_button`, domain/__init__.py:273; paperwork page `views.packetDetail`, static/js/views/paperwork.js:259-315 (**What (client) needs to pay** `clientNeedsHtml` :57; **Required items**, each attached record with its kind, its files as links and photos as small pictures, and who attached it when (`attachmentRow`, `recordFileLinks` :28); **Take a photo** / **Choose file** upload with `?attach=` and, on a waiver item, `&waiver_kind=` :297-298; **I have this…** / **Save** `resolve` :321; **Not needed for this client** (closeout: **Not needed on this job**) `packet-skip` :302, :334; **Remove** on a draft's, the job's and closeout paperwork `packet-detach` :294, :333; **What the signed waiver must show** with `waiverChecklistHtml` :103 and `waiverFormHtml` :85; **Also blocking send**); **From the job record this period** `periodHtml` :194 (the records after the previous pay app's period, grouped Photos, Daily records, Tickets and time sheets, Change orders, Waivers, Other; **Attach to…** lists only the missing items that take that kind of record, from the server's `accepts_kinds`, plus **Send with the pay app** `attach-from-period` :336; a waiver's type `waiver-type` :358; a ticket's change order or line `record-work` :353). Server: the standard items `PAPERWORK_ITEMS`, domain/__init__.py:3272 (progress photos, daily tickets and time sheets, supplier invoices and receipts, insurance certificate, stored materials documents, signed contract, conditional waiver; closeout: punch list sign-off, completion certificate, final lien waivers, as-builts, warranties); a new pay app's list `_new_packet` :3171 (the client's list, or the default one, and always a conditional waiver named by its type unless this client was marked as not taking one); daily tickets when the pay app bills a change order or a unit-price line `_add_ticket_item` :3230; closeout paperwork for every job at Mark done `_ensure_closeout_packet` :3143; `attach_record_to_packet` :3546, `resolve_packet_item` :585, `skip_packet_item` :3431, `detach_packet_item` :3403, `set_waiver_type` :1038, `link_record_to_work` :4260; the note-why line folded at the bottom of the Next step card in `views.job` (static/js/views/job.js:292, `learn` :453, server `learn_rejection`, domain/__init__.py:2986). The list comes from the Client playbook, Paperwork needed with each pay app; a pay app, schedule of values or continuation sheet item is in from the start ("Produced by Duracash with the pay app").

**Taps from start to done:** 1 to open, then 2 per missing item (Take a photo, take it) or 2 to attach a record (pick, Attach).

**Where it could still be simpler:**
- The paperwork list only appears once the first pay app is drafted; the setup checklist asks for the contract and paperwork on the job record before that.

---

## 8. Draft, check and send a pay app

**Goal:** turn finished work into a pay app and email it with its PDF and paperwork.

```mermaid
flowchart TD
    A["Job page"] --> B{"Is the whole job finished?"}
    B -->|"No, bill progress"| C["Tap 'Progress pay app'"]
    C --> D{"Job has a schedule of values?"}
    D -->|"No"| E["'This draw $' (or 'Total done so far $' or '% complete'), Period to, What was completed (optional); the live line 'This pay app: $A less retainage $R = $N.'"]
    D -->|"Yes"| F["Per line: '% done' or 'Qty done (unit)', and stored materials"]
    E --> G["Tap 'Draft progress pay app'"]
    F --> G
    B -->|"Yes"| H["Tap 'Mark done': 'Anything to note? (optional)', Period to, 'Confirm done', confirm"]
    G --> J{"An open draft already?"}
    H --> J
    J -->|"Yes, not out for review"| K["Drafted again at the new total, same number and paperwork"]
    J -->|"Out for the client's review"| K2["Refused: record their answer first"]
    J -->|"No"| L["Draft made, retainage held back"]
    K --> L
    L --> M{"Paperwork missing?"}
    M -->|"Yes"| N["Paperwork, section 7"]
    N --> M
    M -->|"No"| O["Job page or Dashboard: 'Review & send'"]
    O --> P["Pay app page: card 'Pay app summary' (Contract sum, Approved change orders, Contract sum to date, Completed and stored to date, Retainage held, Earned less retainage, Less previous pay apps, Current amount due, Balance to finish, Prior payments received), the continuation sheet with Qty to date, and card 'Before you send': To, Also email (CC), Amount, Period to, Payment due, Subject, 'The email carries' (each file with 'Leave out' or 'Put back', 'Add from the job record…'), 'What (client) needs to pay'; 'Open the PDF'"]
    P -.->|"A signed unconditional waiver is attached"| PU["'An unconditional waiver is attached…': take it off, or an owner types why and taps 'Send anyway', confirm"]
    P -.->|"Company address, phone or licence number blank"| PW["'Your company … is blank on this PDF.' with 'Company details' (a warning: sending stays open)"]
    P --> P1{"Email for pay apps known and your email confirmed?"}
    P1 -->|"No email"| P2["'Email for pay apps' box, 'Save email'"]
    P2 --> P
    P1 -->|"Yes"| Q["Tap 'Approve & send to' the client's email"]
    Q --> R{"Email went out?"}
    R -->|"Yes"| S["Status Sent; Activity: 'Pay app sent'"]
    R -->|"No"| T["Dashboard row 'Didn't go out' with 'Send again'; the Outbox has it too"]
```

Code: **Progress pay app** and **Mark done** in `jobActions`, static/js/jobs.js:352-353; progress card `progressFormHtml`, static/js/views/job.js:94-118 (`progressFigures`, static/js/jobs.js:209, checks it as the server does), line-by-line card `sovProgressHtml` static/js/sov.js:91-114 (it names an open draft it replaces, which keeps its number and paperwork, M1; a lump-sum line's **% done** takes at most one decimal, `lumpDone` static/js/sov.js:34, never rounded); **Draft progress pay app** `progress` static/js/views/job.js:365 and `sov-progress` static/js/views/job-sov.js:242; Mark done card in `views.job` and its confirm static/js/views/job.js:376; server `record_progress` domain/__init__.py:323, `record_sov_progress` :3501, `mark_done` :403, `draft_invoice` :437; **Review & send** opens the pay app page at `.send-check` (static/js/jobs.js:370, static/js/today.js:26); pay app page `views.payappDetail`, static/js/views/payapps.js:70-150 (the **Pay app summary** card `payappSummaryHtml` :48, lines 1-9 as the PDF prints them, `payapp_pdf.summary` payapp_pdf.py:137, through `domain.payapp_figures` domain/__init__.py:1406, which adds the payments received on earlier pay apps; the continuation sheet `continuationHtml` static/js/sov.js:133, whose **Qty to date** column is also on the PDF sheet, payapp_pdf.py:302, and the client's page, `_sheet` payer_page.py:90; **Before you send**, **Open the PDF**, **Download for QuickBooks**, **Approve & send to …**; the blank company details warning `companyGapsHtml` static/js/setup.js:29, a warning only); the paperwork part of Before you send `beforeYouSendHtml`, static/js/views/paperwork.js:209 (**Leave out** / **Put back** / **Take off** `payapp-attach` :343, **Add from the job record…** `payapp-add` :348, **Send anyway** `send-override` :368; server `change_payapp_attachments` domain/__init__.py:4188, only while the pay app is a draft, `POST /api/jobs/<id>/payapp/<inv>/attachments` server.py:2776); server `approve_and_send`, domain/__init__.py:632 (an attached unconditional waiver is refused without an owner's reason, `unconditional_waivers_attached` :614, `_send_payapp` server.py:2943; the conditional waiver type that went with it is kept on the pay app); **Send again** `email-retry` static/js/views/communications.js:83. Mistakes: **Undo done** (before the final pay app goes out, `undo_mark_done`, domain/__init__.py:2490), **Delete draft** (`void_draft`, domain/__init__.py:2317), **Pull back this pay app…** on a sent one with nothing paid (`void_sent_pay_app`, domain/__init__.py:2367).

**Taps from start to done:** 3 for a progress bill with nothing missing (Progress pay app, Draft progress pay app, Review & send) plus Approve & send: 4.

**Where it could still be simpler:**
- **Send draft for their review** (the client checks the numbers first) is under More, not charted; it waits on **Client agreed to the draft** or **Client wants changes**.

---

## 9. Follow up on an unpaid pay app

**Goal:** send a polite chase email when the client's usual wait has passed, or note a phone call.

```mermaid
flowchart TD
    A{"Where are you?"} -->|"Dashboard"| B["Row 'Follow up': tap 'Send follow-up'"]
    A -->|"Money, Unpaid"| C["Row for the job: 'Send follow-up', or 'Email' / 'Call'; 'Recent contact (N)' under the job opens the last emails, calls and client page visits, newest first, and 'See all on the job'"]
    A -->|"Job page"| D["Main button 'Send follow-up' once due; before then 'Follow up now' under More"]
    B --> E["Confirm: 'Email a follow-up on PA-x to (who) · (email)?' (and 'It's before the follow-up date' when early)"]
    C --> E
    D --> E
    E --> G["One email naming every unpaid pay app, with the client's page link (the link needs the site address set); firmer once past their usual days to pay"]
    G --> H{"Client answers on their page?"}
    H -->|"'Mark as received'"| I["Pay app row: 'received (date)'"]
    H -->|"'Approve for payment'"| I2["Dashboard: 'Approved' row, no follow-up until the day after the date they gave"]
    H -->|"No"| J["Next 'Follow up' row after the client's days between follow-ups"]
    A -->|"They called instead"| K["'Spoke to them…': 'What did they say?', 'Check again on (optional)', 'Save'"]
    K --> J
```

Code: Dashboard row kind `follow_up` in `domain.today_rows`, domain/__init__.py:1709-1841 (with **Spoke to them…**, static/js/today.js:55); Unpaid `views.collections`, static/js/views/collections.js:25 (**Recent contact (N)** `recentContactHtml` :14, from `contact_log`, domain/contact.py:45: emails to the client with how they went, calls noted with Spoke to them, client page events and the client's emails filed to the job; the job view carries the last five only while money is owed); job page **Send follow-up** / **Follow up now** static/js/jobs.js:373; the confirm static/js/views/collections.js:73; server `record_follow_up`, domain/__init__.py:752 (not-due refusal :779, from the third follow-up it also emails the client's **Also email from the third follow-up** address); **Spoke to them…** `contactFormHtml` static/js/views/job.js:55 and `note_contact`, domain/__init__.py:824; the client's page `payer_page.render`, payer_page.py:152 (**Mark as received** `mark_received` :211, **Approve for payment** `approve` :226).

**Taps from start to done:** 2 (Send follow-up, then the confirm).

**Where it could still be simpler:**
- The confirm says who it goes to but not what the email says; the Outbox shows the text once it has gone.

---

## 10. Record a payment

**Goal:** record a check or transfer against the right pay app so the waiver step opens.

```mermaid
flowchart TD
    A{"How did the money show up?"} -->|"One check in hand"| B["Job page: 'Record a payment'"]
    B --> C["'Pay app' (with what is owed), 'Amount received ($)' starts at what is owed, 'Date received', 'Check or reference no. (optional)'"]
    C --> D["Tap 'Record'"]
    D --> E{"Amount compared with what is owed"}
    E -->|"More than owed"| F["Refused under the amount"]
    F --> C
    E -->|"Less"| G["Pay app 'Partly paid'"]
    G --> G2{"Will the rest come?"}
    G2 -->|"No"| G3["'Accept $X short pay…': 'Why it was paid short', 'Accept short pay', confirm"]
    G3 --> H
    E -->|"All of it"| H["Pay app 'Paid', waiver now due"]
    A -->|"Many, in QuickBooks"| I["Import/Export, Import: 'Payments from QuickBooks' (Money, Payments links there)"]
    I --> J["Paste the CSV and 'Read payments', or 'Choose the file' (.csv or Excel)"]
    A -->|"A payments file dropped in the Inbox"| J2["Its card: 'Read as payments (N rows)'"]
    J2 --> K
    J --> K["Table: Record, Date, Client, Number, Amount, Pay app, Why; matches ticked"]
    K --> L{"Row needs you?"}
    L -->|"'No match' or 'Pick one'"| L1["'Pick a pay app…' in the row; it ticks itself"]
    L -->|"'Too much'"| L2["'Record $owed to PA-x': the rest moves to its own line"]
    L1 --> M
    L2 --> M
    L -->|"No"| M["Tap 'Record the ticked payments'"]
    M --> N["Toast: 'N recorded.' and how many still need a pay app picked"]
    A -->|"QuickBooks connected"| O["Import: 'Read payments from QuickBooks'"]
    O --> K
```

Code: **Record a payment** card in `views.job` (`match` static/js/views/job.js:437, prefill `prefillPaymentAmount` static/js/views/job.js:209), server `match_payment`, domain/__init__.py:859 (takes `received_on`); short pay `shortPayCardHtml` static/js/views/job.js:36, `accept-short-pay` static/js/views/payapps.js:200, server `settle_short_pay`, domain/__init__.py:957; the import card `paymentsImportHtml`, static/js/views/payments.js:95-111, on Import (`views.import` static/js/views/data.js:47); Payments `views.payments` static/js/views/payments.js:115-138 lists what was recorded and links to Import (**Read payments** `qbo-read` static/js/views/payments.js:143, **Choose the file** `qbo-choose-file` static/js/views/payments.js:176, **Record the ticked payments** `qbo-apply` static/js/views/payments.js:151, the split `qbo-split` static/js/views/payments.js:163); row cells `qboTick` / `qboPayAppCell` static/js/views/payments.js:62-74 with static/js/payimport.js (`pickPayApp`, `splitTooMuch`, `afterRecord`); server `qbo_csv.parse` qbo_csv.py:163 and `propose` :309; Inbox **Read as payments** `inbox-read-payments` static/js/views/payments.js:169 (opens Import); QuickBooks card `qboCardHtml` static/js/views/payments.js:77 (**Connect QuickBooks**, **Read payments from QuickBooks**, **Check against QuickBooks**, **Disconnect**). The wireframe is `PaymentsImport`.

**Taps from start to done:** 2 by hand (Record a payment, Record); 3 for a CSV (Read payments, a pick if needed, Record the ticked payments).

**Where it could still be simpler:**
- QuickBooks is read on request, not every day (deferred until a live Intuit connection is tested, improvement plan section 5).

---

## 11. Lien waiver

**Goal:** a conditional waiver goes with each pay app; once it is paid, send the signed unconditional one.

```mermaid
flowchart TD
    A0["Before payment: each pay app's paperwork has a conditional waiver (progress, or final on the final pay app), section 7"] --> A
    A["Payment recorded: 'Payment received, waiver not sent'"] --> B{"Where are you?"}
    B -->|"Dashboard row 'Waiver due'"| C["Tap 'Review waiver'"]
    B -->|"Job page"| C2["Tap 'Choose waiver and send'"]
    B -->|"Money, Closeout"| C3["Row 'Due' (one per pay app and per retainage release): 'Choose type and send'"]
    C --> D["Waiver page 'Waivers: (job)'"]
    C2 --> D
    C3 --> D
    D --> E{"A signed waiver of that type on the job record?"}
    E -->|"No"| E1["'Add the signed unconditional waiver to the job record, then send.': 'Take a photo' or 'Choose file'; 'Approve & send' stays off"]
    E1 --> F
    E -->|"Yes"| F["Pick the type; check 'Check the signed form shows' and 'State form'; tap 'Approve & send'"]
    F --> G["Confirm names the type, pay app, amount and who gets it"]
    G --> I["Emailed with that signed waiver attached, the amount, the period and any retainage it does not release"]
```

Code: Dashboard row kind `waiver` (**Review waiver**, `open-waiver`); job page **Choose waiver and send** static/js/jobs.js:371; Closeout `views.waivers`, static/js/views/closeout.js:100 (one row per pay app and per retainage release, `waiverRows` :31; **Which type?**, **Not chosen yet**, **Choose type and send**); waiver page `views.waiverDetail`, static/js/views/closeout.js:58-96 (**Waivers due**, **Check the signed form shows**, **State form**, **By pay app** with **Before payment** and **After payment**); the confirm `waiver` :155; type picker and hint `waiverSendForm` / `WAIVER_HINTS`, static/js/jobs.js:112-149; server `send_waiver`, domain/__init__.py:1091 (refused until a signed waiver of the type is on the job record, `waiver_on_file` :1060, never one that already went out in any email, the pay app's included), both stages `waiver_stages` :1205, the type it suggests `suggested_waiver_kind` :1009 (conditional before payment, unconditional after, never final while retainage is held); state forms `helper/waiver_forms.json` (Georgia so far), `GET /api/waiver-forms` server.py:2775.

**Taps from start to done:** 2 (Review waiver, Approve & send) and the confirm, plus the type if it isn't the one picked for you.

**Where it could still be simpler:**
- Duracash does not write the waiver: sign the form your state or the client requires and add it to the job record. Only Georgia's forms are listed so far.

---

## 12. Retainage release

**Goal:** get the money held back on the job paid at the end.

```mermaid
flowchart TD
    A["Job page 'Retainage' card, or Money, Closeout: 'Held back, still owed' (Done, Times asked, Last asked, Holding it up, Next)"] --> B{"Job marked done?"}
    B -->|"No"| C["'Once the job is marked done.': no ask yet"]
    B -->|"Yes"| D{"Closeout paperwork complete?"}
    D -->|"No"| E["'Open closeout paperwork': add or mark items, section 7"]
    E --> D
    D -->|"Yes"| F["'Amount ($), blank for all', 'Ask for retainage', confirm"]
    F --> G["Email asking for the retainage, with the closeout paperwork"]
    G --> J["When paid, job page: 'Record retainage paid'"]
    J --> K["'Retainage received ($)', 'Reference', 'Record'"]
    K --> L{"More than what is held?"}
    L -->|"Yes"| M["Refused under the amount"]
    M --> K
    L -->|"No"| N["Recorded; its lien waiver is due (section 11): unconditional, final once nothing is held"]
```

Code: the job page's **Retainage** card `retainageCardHtml`, static/js/views/job-money.js:39 (rate, held, released, still held, **Job done**, **Times asked**, **Last asked**, **Next ask allowed**, **Pay apps holding it**, **Releases** with each one's waiver, **Closeout paperwork** with its files; figures `retainageFacts` static/js/jobs.js:176); Closeout's held-back table in `views.waivers`, static/js/views/closeout.js:100-150, `retainageAskHtml` :17; job page **Ask for retainage** only once the work is done and the closeout paperwork is in (static/js/jobs.js:385; the card says why otherwise, `retainageNote` static/js/jobs.js:303); after a request, whole or part, the next waits the client's **Days between follow-ups** (`retainageAskWaits` static/js/jobs.js:297, the server's wait in `chase_retainage`); the confirm static/js/views/closeout.js:167; server `chase_retainage`, domain/__init__.py:1234 (refused before the job is marked done); **Record retainage paid** card in `views.job`, `retainage-paid` static/js/views/job.js:395, server `record_retainage_payment`, domain/__init__.py:1296 (each release owes a lien waiver, `retainage_waivers`).

**Taps from start to done:** 4 (Ask for retainage and the confirm, then later Record retainage paid, Record), then the release's waiver (section 11).

**Where it could still be simpler:**
- The Dashboard has no row for "retainage ready to ask for"; the job page and Closeout show it.

---

## 13. Finish the job

**Goal:** file a fully settled job away in Job history, with its job file.

```mermaid
flowchart TD
    A["Job page"] --> B{"Anything still open?"}
    B -->|"Yes"| C["Card 'Before you can finish this job': one line per thing, with its buttons"]
    C --> D["'Review & send' / 'Delete draft', 'Get CO N signed' / 'Withdraw', 'Draft pay app', 'Record a payment' / 'Accept $X short pay…', 'Record retainage paid' / 'Ask for retainage' or 'Open closeout paperwork', 'Send waiver'"]
    D --> B
    B -->|"No"| E["Next step 'Ready to finish the job.': tap 'Finish job', confirm"]
    E --> F["The job page is its job file: 'Where this job stands', 'Job file' (contract, signed change orders, billed, received, retainage released, Started with 'Change', Finished), each pay app with its PDF, payments, change orders, waivers sent, the job record and Activity"]
    F --> G["Off Open jobs, the job pickers, the record card, Money's Pay apps, Change orders, Unpaid, Payments and Closeout, and the Calendar; listed under 'Job history', on its client's card, and under 'Finished jobs' in the Job filter of the Activity log, Outbox, Everything received and Sent pay apps"]
    F --> DL["'Download job file': one zip of the record's files, the sent pay apps' PDFs, a summary and the activity"]
    F --> H{"More work, a late payment or a mistake?"}
    H -->|"Yes, and may finish and reopen jobs"| I["'Add more work': 'What is the new work?', 'Add more work': the same job opens again"]
```

Code: the card in `views.job` from `close_blocker_items`, domain/__init__.py:188 ("A lien waiver is still due." until every paid pay app's and every retainage release's waiver is sent), buttons `closeBlockerActions`, static/js/jobs.js:409-411; **Finish job** static/js/jobs.js:380 and its confirm static/js/views/job.js:447; server `close_job`, domain/__init__.py:2612; the job file `archiveHtml` static/js/views/job.js:155 (J9) with **Change** for the start date (`save-start` :422; the server takes only the start date on a finished job, `edit_job` domain/__init__.py:3776); **Download job file** GET /api/jobs/<id>/archive/job-file.zip, `job_archive.build` job_archive.py:84 (anyone signed in to the company); **Add more work** in `views.job` (:244-254, `reopen` :416), server `reopen_job`, domain/__init__.py:3008 (the Finish and reopen jobs permission, `JOB_ACTION_PERMS` domain/profiles.py; the button shows with `may("reopen")`); the open jobs `openJobs` static/js/jobs.js:476, used by Money's lists (`views.payapps`, `views.cos`, `views.collections`, `views.payments`, `views.waivers`) and `views.calendar`; the job filters on pages of records `jobFilterOptions` static/js/jobs.js:481 (open jobs, then "Finished jobs"); a job with only drafts deleted before they were sent and no payments can be deleted instead (**Delete job** under Edit job, `can_delete_job` :3353).

**Taps from start to done:** 2 (Finish job, the confirm).

**Where it could still be simpler:**
- The Job filters on the pages of records offer only jobs finished in the last 90 days; an older job's records are in its job file under Job history.

---

## 14. The Dashboard routine

**Goal:** open one page, see everything waiting, who has it and when it is due, and handle it top to bottom. Each person gets their own rows by email each morning there is something due: an owner gets what is assigned to them and what nobody has; a member gets what is assigned to them.

```mermaid
flowchart TD
    A["Open Duracash: Dashboard"] --> T["Card 'To Do': Activity, Job, Ball in court (Us or Client), Assignee, Due"]
    T --> T1["Filters: 'Mine' or 'Everyone'; 'Today', 'This week' (first), 'This month', 'Date range'. Overdue rows always show"]
    T --> T2["'Add task': what needs doing, Job, Assignee, Due, Ball in court"]
    T --> T3["Pick a name in a row's Assignee box: saved at once ('Assigned.')"]
    T1 --> C["Rows most urgent first, three at a time ('and N more'); a task added by hand has 'Done'"]
    C -.- B["Below the card: Done, not billed · Outstanding · Held back, still owed · Billed this month"]
    C --> D{"Row"}
    D -->|"'Didn't go out'"| D1["Tap 'Send again'"]
    D -->|"'Job's done?'"| E["Tap 'Mark done and draft' (or 'Just file the message')"]
    D -->|"'Ready to send'"| F["Tap 'Review & send': Before you send, section 8"]
    D -->|"'Waiver due'"| H["Tap 'Review waiver', section 11"]
    D -->|"'Due soon' (the client's cutoff within 5 days)"| I["Tap 'Bill progress': the job's Next step"]
    D -->|"'Needs paperwork' or 'Blocked'"| G["Tap 'Add N missing documents' / 'Fix N things before sending' / 'Get CO N signed', section 7"]
    D -->|"'Follow up'"| J["Tap 'Send follow-up', or 'Spoke to them…'"]
    D -->|"'Approved'"| J2["No button: waiting for the date the client gave"]
    D -->|"'Not billed' or 'Not billed yet'"| K["Tap 'Draft pay app' or 'Bill progress'"]
    D -->|"'Inbox'"| L["Check the kind after the button ('as Lien waiver'; change it if wrong), then tap 'Add to this job'"]
    D1 --> M{"More rows?"}
    E --> M
    F --> M
    G --> M
    H --> M
    I --> M
    J --> M
    K --> M
    L --> M
    M -->|"Yes"| C
    M -->|"No"| N["'Nothing is waiting on you.'"]
```

Code: the rows and their order `domain.today_rows`, domain/__init__.py:1710-1834 (one rule set for the Dashboard and the daily email `daily_digest` :1845, sent per person by `_digests`, monthly.py:75); each row's key, due date, ball in court and assignee `decorate_rows`, domain/tasks.py:47 (assigned with `assign_row` :204, tasks added by hand `add_task` :136, `edit_task` :155, `set_task_done` :192; routes `/api/tasks…`, server.py:2761-2765, handlers `_task_change` server.py:1881; a team member removed: `reassign_open_tasks` domain/tasks.py:241, called from `_p_team_remove` server.py:2007); one list and the period filter `todoItems` / `periodRange` / `inPeriod`, static/js/tasks.js:35, :20, :35; drawn by `todoHtml` / `rowButtons` / `assignSelect`, static/js/today.js:69, :52, :69; the card `todoCardHtml` static/js/views/today.js:77 with **Add task** `taskFormHtml` :61 (what each row's button does: `ACTIONS` :22-33; an Inbox row's kind select, which starts at the kind the Inbox guessed, `inboxKindSelect` :44, sent as the row's `item_kind`); Dashboard page `views.dashboard`, static/js/views/today.js:251-310 (metrics and the jobs table under the card); "Mark done and draft" confirm static/js/views/inbox.js:509. The daily email is turned off in Settings, Company: **Daily email of what's due**.

**Taps from start to done:** 1 to 2 per row. A typical week with one send, one follow-up and two filings is about 6 taps with the confirms.

**Where it could still be simpler:**
- No row for retainage ready to ask for (section 12).
- A pay app ready to send is due on the client's next cutoff, so with a cutoff weeks away it shows under **This month**, not **This week**.

The Calendar starts with **Tasks** (T6): the same items and the calendar's dates in **This week**, **Next week**, **This month** or **Custom**, each opening its day, with **Add task** (`tasksBlockHtml`, static/js/views/today.js:100).

---

## 15. Client playbook

**Goal:** keep each client's rules (who signs off, where pay apps go, follow-up timing, the paperwork list) right, since they drive every pay app and follow-up.

```mermaid
flowchart TD
    A["Jobs: 'Client playbook'"] --> B{"Any clients yet?"}
    B -->|"No"| C["'No clients yet': 'Add client', or one is added with its first job"]
    B -->|"Yes"| D["Open a client card"]
    A --> AC["'Add client' ('Import a contract' fills it): Client name, Client type, Who signs off on your bills, Email for pay apps (can add later), Phone, Pay apps due by (day of the month), Payment terms, days, the paperwork checkboxes"]
    AC --> AN{"Nearly the same name as a saved client?"}
    AN -->|"No"| AD["Client added"]
    AN -->|"Yes"| AQ["'Same as ABC Builders?': 'Use ABC Builders' opens that client's card; 'Keep “ABC Builders Inc” as a new client' adds it"]
    D --> JB["Open jobs and Finished jobs: each job with its code, contract number, contract total, a 'Contract' link to its newest contract file and 'Add contract'"]
    D --> E["Edit Client name, Client type, Who signs off on your bills, Email for pay apps, Phone, Days between follow-ups, Pay apps due by (day of the month), Days they usually take to pay, Payment terms, days, Paperwork needed with each pay app (a box per usual item, each pay app and at closeout, Other, Nothing needed), They send pay apps back when, Also email (CC), Also email from the third follow-up"]
    E --> F["Tap 'Save client'"]
    F --> G{"Days 1-365, due day 1-31, real emails and phone, something ticked or Nothing needed?"}
    G -->|"No"| H["Red note under the box"]
    H --> E
    G -->|"Yes"| I["Saved; a blank box clears it"]
    D --> J{"Duracash learned something?"}
    J -->|"Suggested paperwork item"| K["'Add to the paperwork?': 'Add to paperwork' or 'Dismiss'"]
    J -->|"Measured days to pay differ by 2+"| L["'Use N days' (only for 1 to 365 days)"]
    D --> MG["'Same client as another one?': 'Merge into', 'Merge', confirm"]
    D --> DL{"An owner, and no job uses this client, finished jobs included?"}
    DL -->|"Yes"| DY["'Delete client', confirm 'Delete client'; the Activity log notes it"]
    DL -->|"It has jobs"| DN["'A client with jobs can't be deleted, finished jobs included. To remove ABC Builders, merge it into another client.' (With no other client: 'Its jobs need it.')"]
```

Code: page `views.playbook`, static/js/views/clients.js:101-185 (**Add client** `add-payer` static/js/views/clients.js:214, server `add_payer` domain/__init__.py:2162; **Save client** `save-playbook` static/js/views/clients.js:192, server `update_payer`, domain/__init__.py:2005; the jobs `clientJobsHtml` :40, asked for when the card opens from GET /api/payers/<id>/jobs, `payer_jobs` domain/jobinfo.py:169, finished jobs included (S10); the paperwork boxes `paperworkHtml` :54, built by `paperworkChoices` static/js/jobs.js:514 from the server's standard catalog `PAPERWORK_ITEMS` domain/__init__.py:3277 (sent as `paperwork_catalog` by `light_snapshot` domain/__init__.py:1606; a new client's list `DEFAULT_PAPERWORK` uses its labels, and `_followups_paperwork_catalog_labels` migrations.py:165 moved older saved labels to them), saved as the comma list the server reads, `parse_packet_text` domain/__init__.py:3104, read back from `packet_items` (S11); **Import a contract** as on New job (S12); **Add to paperwork** / **Dismiss** `sug-add` / `sug-dismiss` static/js/views/clients.js:141-155; **Use N days** `use-approval-days` static/js/views/clients.js:149; **Merge** `merge-payer` static/js/views/clients.js:171 (shown when there is another client), server `merge_payer`, domain/__init__.py:2147; **Use ABC Builders** / **Keep “ABC Builders Inc” as a new client** `ap-same-use` / `ap-same-keep` static/js/views/clients.js:127-131, server `payer_near` domain/__init__.py:2118; **Delete client** `delete-payer` static/js/views/clients.js:132, shown by `deletePayerHtml` static/js/views/jobs.js:129 to those whose profile may edit client settings (`can("company")`) when `job_count` is 0, server `delete_payer` domain/__init__.py:2169, a route with perm="company", server.py:2868, refused with the reason while any job, open or finished, uses the client; it says "counting finished jobs" only when one is finished, and points at Merge only when another client exists. The card's "N jobs", the Merge question and Delete client all count with `payerJobCount` static/js/views/jobs.js:121, which uses `job_count` so older finished jobs count too). Every label has an (i) with the trade term (`FIELD_HELP`, static/js/help.js).

**Taps from start to done:** 2 (open the card, Save client), plus 1 per suggestion.

---

## 16. Team, permissions and signing in

**Goal:** bring people in with the right permissions, keep the company's deletion with one person, and sign in safely. The screens are the wireframes `Settings` and `TwoStep`.

```mermaid
flowchart TD
    A["Settings, Team"] --> B{"Your profile includes 'Manage the team and permissions'?"}
    B -->|"No"| B1["The team list and the profiles table, read-only"]
    B -->|"Yes"| C["'Their email', 'Profile', 'Invite'"]
    C --> D["They open the link, choose a password: they join with that profile"]
    B -->|"Yes"| E["Permission profiles: 'New profile' or 'Edit'"]
    E --> F["Name it, tick what it can do, 'Save profile'"]
    F --> G["Pick it next to a person, 'Change'"]
    G --> H{"Would the company lose its last Owner, or the Company Admin stop being one?"}
    H -->|"Yes"| H1["Refused with the reason"]
    H -->|"No"| H2["Their rights change at once; the Activity log notes it"]
    A --> K{"You are the Company Admin?"}
    K -->|"Yes"| K1["'Hand it to' another owner, your password, 'Hand over'"]
    K -->|"Yes"| K2["Settings, Company, Your data: 'delete the company' opens its own page: password, company name, 'Delete everything'"]
    S["Sign in: email and password"] --> T{"Email set up on the server?"}
    T -->|"Yes, new device"| T1["'Code from the email'; 'Don't ask again on this device for 30 days'"]
    T -->|"Yes, authenticator app set up"| T2["'Code from your authenticator app'"]
    T -->|"No"| T3["Signed in; My account opens with 'One more step: two-step sign-in' (or 'Not now')"]
    T1 --> U["Signed in"]
    T2 --> U
    U --> V["An hour without use: 'Signing you out in 2 minutes' with 'Stay signed in', then Sign in says why"]
```

Code: Team page `teamView`, static/js/views/settings.js:246, with `teamCardHtml` static/js/views/settings.js:76 (**Change** `team-profile` static/js/views/settings.js:612), `profilesCardHtml` static/js/views/settings.js:113 and `profileFormHtml` static/js/views/settings.js:127 (**New profile**, **Edit**, **Save profile** `profile-save` static/js/views/settings.js:624, **Delete** `profile-delete` static/js/views/settings.js:635), `adminCardHtml` static/js/views/settings.js:145 (**Hand over** `admin-hand-over` static/js/views/settings.js:639). The permissions and the job action each needs are `PERMISSIONS` and `JOB_ACTION_PERMS`, domain/profiles.py:24 and domain/profiles.py:52; the server checks `has_perm` on every route flagged perm= (`_run`, server.py:925, the check at server.py:953) and `may_take` on every job action (`_m_job_action`, server.py:2672), and the page hides the same through `mayTake` and `ACTION_PERM`, static/js/jobs.js:330-344, and `can`, static/js/app-core.js:462. Profiles are saved by `_p_profile_save`, server.py:2034 (`save_profile`, domain/profiles.py:147); a person's profile by `set_role`, auth.py:857; the Company Admin is `_admin`, auth.py:721, handed on by `hand_over_admin`, auth.py:929, and the only one `_p_account_delete` lets through, server.py:2024; the delete page is `deleteCompanyView`, static/js/views/settings.js:257. Signing in: `_p_login`, server.py:1449, asks for an emailed code when `_email_code_due`, server.py:1500 (`login_code_check` and `remember_device`, auth.py:1091-1109); the code step on the landing card, static/js/views/auth.js:79-88; with no mail on the server the setup card comes first (`twoStepCardHtml`, static/js/views/settings.js:318, **Not now** `2fa-later` static/js/views/settings.js:679). **Change password** opens `passwordView`, static/js/views/settings.js:352 (**Email me a code** `pw-code`, server `_p_password_code`, server.py:1725). Appearance is `appearanceCardHtml`, static/js/views/settings.js:302, saved with My account's one **Save** (`save-account`, static/js/views/settings.js:694) through `_p_appearance`, server.py:2072, and applied by `applyAppearance`, static/js/theme.js:24, to the dark palette and accents in static/styles.css:748. The idle sign-out is `session_check`, auth.py:403, and static/js/idle.js.

**Taps from start to done:** a new profile and a person on it: 4 (New profile, Save profile after ticking, pick it, Change). Sign in from a new device with email codes: 2 (Sign in, then Sign in with the code).

---

## Flows not charted, or charted with less confidence

- **Billing** (Settings, Billing: **Subscribe** / **Manage billing** through Stripe, `billingView` static/js/views/settings.js:370): shows only when Stripe is set up on the server; people whose profile doesn't include billing see it read-only.
- **The client's page** (`payer_page.render`, payer_page.py:152; wireframe `PayerPage`): This job (the pay app's lines 1-9 and the prior payments received, `_job_card` :75), the continuation sheet with Qty to date (`_sheet` :90), Mark as received, Approve for payment with a date, Send a question (an Inbox item for the contractor). Charted as branches in section 9.
- **QuickBooks connection** (**Connect QuickBooks**, Settings, Company and Import/Export, Import): the Intuit sign-in in the middle happens off our site. Charted as one branch in section 10.
- **Email forwarding, text messages and sharing from the phone into the Inbox** (`views.inbox`, **Bring emails and texts in automatically**): need an owner to set up an email service or Twilio. Shared while signed out, files wait 30 minutes for sign-in (**Finish adding N shared items**, `heldShareHtml` static/js/views/auth.js:385).
- **Export** (Import/Export, Export: `views.export` static/js/views/data.js:68-106): **Download everything** (owners only; the server refuses members, `_g_export`), **Sent pay apps** with each one's **PDF** and **Download for QuickBooks**, filtered by job (`GET /api/payapps/sent`, `_g_sent_payapps` server.py:1199, `domain.sent_payapps_page` domain/comms.py:99; closed jobs' too), the **Calendar feed** with **Copy** (`calendarFeedHtml` static/js/views/inbox.js:136), and links to the Outbox and the Activity log. Settings, Company, **Your data** links here and keeps Reset and Delete.
- **Outbox** (Communications, Outbox: `views.outbox` static/js/views/communications.js:58-78): each email lists what it carried, **Pay app PDF** and each file by name, opening it, or "A file no longer on the job record" (`attachmentsHtml` :18); **Print** (`print-email` :84) prints that one email (To, When, Status, Subject, the message and its attachments) through `printSheet` :39 and the `@media print` rule in static/styles.css (comms-data block). **Print** on an Everything received row does the same for something that came in.
- **Daily record** (Jobs, Daily record; **New daily record** on a job page opens it with that job picked; `views.daily` static/js/views/daily.js:134): Job, **Crew members** and names, **Weather** and a note, **Materials used or installed**, **Deliveries**, **Diary**; boxes **Progress photos**, **Quantities** (only on a job with a schedule of values), **Change order work**, **Inspections or major discussions**, **New punchlist items**, each opening its fields; **Attach files**; **Save to job record**. Files go up first to the job record (`uploadDailyFiles` static/js/views/daily.js:195) and the record keeps their ids. The server checks every field (`clean_daily_details`, domain/daily.py:92) and reads them out in the job record's note (`daily_record_note` :222). Quantities put in place today add to each line's quantity done, refused past the scheduled quantity, and update the draft pay app (`add_daily_record` :256 through `record_sov_progress`, domain/__init__.py:4014); the schedule card then says **Updated from daily records on …** (static/js/views/job-sov.js:146). **Recent records** list each record's crew, weather, materials, deliveries, what each ticked section held, and links to its files (`recordMeta` static/js/views/daily.js:102).
- **Draft for the client's review** (**Send draft for their review**, **Client agreed to the draft**, **Client wants changes**, static/js/jobs.js:387-389): an optional side path before send.
- **Tap counts** are the fewest taps on the happy path with fields already filled, counted from the code, not timed with a user. Confirms count as a tap.
