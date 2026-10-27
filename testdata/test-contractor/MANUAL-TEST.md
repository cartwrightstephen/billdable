# Manual test: Brightwater Tile & Stone in Duracash

A step-by-step script for testing Duracash by hand with the fictional contractor in
`company.md`. Each step has a checkbox and the result you should see. Button and field names are
copied from the app (`static/app.js`). Every expected result below was checked against a running
server on 2026-10-22 through the same API calls the buttons make.

When a result differs, note the step number and what you saw. Known app problems are in
`APP-ISSUES.md`; don't re-report those.

## 0. Set up (5 minutes)

- [ ] 0.1 Start a server with throwaway data, from the repo folder:
      `DURACASH_DATA_DIR=/tmp/bd-test PORT=8791 python3 server.py`
- [ ] 0.2 In a second terminal: `python3 scripts/load_test_contractor.py --base http://127.0.0.1:8791 --only-setup`
      Expected: it ends with `55 of 55 checks passed.` and prints an email and password.
      They are also saved in `testdata/test-contractor/.login` (git-ignored).
      `--only-setup` stops before the imports, so you do the imports by hand in part 3.
- [ ] 0.3 Open http://127.0.0.1:8791, choose **Sign in** (a browser that has signed in before opens there), and use the email and password from `.login`.
      Expected: Today opens for "Brightwater Tile & Stone LLC".
      Before signing in, the landing shows **Look around a sample company** under the pitch (the test server has the demo company). It opens the demo's Today without a password; **Log out** brings you back. Don't change anything there.

Email is not set up on a test server, so every send ends with a toast like
"Saved. The email to ap@... was not sent: email delivery is not set up." That is expected: the
pay app is still marked sent, and the Outbox lists the email. Today lists each of those that still matters (job open, pay app unpaid) at the top
as **Didn't go out** with **Send again**, the Today menu item shows how many, and the pay app's row
on its job page reads "sent <date> · email failed".

## 1. Look around (what the loader built)

- [ ] 1.1 **Jobs > Open jobs** lists 8 jobs: ASHB, BROO, DECA, GLEN, KENN, MILL, VEST, WHIT. Under the list: "1 closed job is under Job history." (PEAC).
- [ ] 1.2 Open **Mill Creek Clubhouse**. Status pill **Needs paperwork** (amber, not red), primary button **Add 2 missing documents**. The Schedule of values card shows 5 lines and a $80,300.00 contract. PA-MILL-01 is a Draft for $33,042.90.
- [ ] 1.3 Open **Decatur Library Annex**. Money shows $6,400.00 retainage open; PA-DECA-01 is Paid.
- [ ] 1.4 **Settings > Customer settings** shows 6 customers. Open **Tallow Creek Builders**: **Days between follow-ups** 5, **Pay apps due by** 25, **Days they usually take to pay** 18, **Also email (CC)** pm@tallowcreekbuilders.example. On a phone the labels have no (i); the card ends with **What do these mean?**, which lists each box with its explanation.
- [ ] 1.5 **Today** > Needs you: Mill Creek Clubhouse shows its first blocker and **Add 2 missing documents**. Within 5 days before the 25th (the cutoff), Kennesaw Ortho Clinic shows "Due soon" with **Bill progress** and the cutoff date, and Mill Creek's blocked row ends with "Pay app due to Tallow Creek Builders by <the 25th>." and moves up: its PA-MILL-01 is only a draft, so this cycle is not billed yet. Above them, each unpaid pay app and each waiver the loader sent shows **Didn't go out** ("<subject> to <email>: not sent: email delivery is not set up") with **Send again**. Needs you shows three rows, then **and N more**; tapping it shows the rest until you leave Today.

## 2. Enter one job fully by hand (new customer)

Kirkwood Dental Suite was just awarded. Its customer is new, so this job also creates the payer.

- [ ] 2.1 **Jobs > Open jobs > New job**. Five boxes show: Job name, Address, Customer, Who pays you?, Contract amount. The rest are folded under **More details (optional)**. Type exactly:

  | Field | Value |
  | --- | --- |
  | Job name * | `Kirkwood Dental Suite` |
  | Address * | `1910 Hosea L Williams Dr NE, Atlanta, GA 30317` |
  | Customer * | `Lindell Dental Partners` (the box suggests customers already saved; this one is new) |
  | Who pays you? | `The homeowner or property owner` (hint under it: "Sets the paperwork they usually need and when to follow up. You can change both later.") |
  | Contract amount ($) * | `27,450` (commas and a `$` are fine; leaving the box shows `$27,450.00`) |

  Open **More details (optional)** and type:

  | Field | Value |
  | --- | --- |
  | Contract number | `LDP-0412` |
  | Retainage % | `0` |
  | Phone | `404-555-0127` |
  | Who signs off on your bills | `Dr. Maya Lindell` (leaving the Customer box fills it with the customer; replace it) |
  | Email for pay apps (can add later) | `billing@lindelldental.example` |
  | Notes (kept on the job record) | leave empty (see 2.4) |

- [ ] 2.2 **Save job**. Expected: toast "Job saved", the job page opens. Its code is **KIRK**. The Job record is empty: the contract number is kept on the job (it prints on every pay app, and shows in **Edit job**), not as a record. **Today** > Needs you (tap **and N more**) has a **Not billed yet** row: "Kirkwood Dental Suite has no pay app yet. Bill the work done so far, or mark it done." Its **Bill progress** opens the job at its next step.
- [ ] 2.3 **Settings > Customer settings** now has 7 customers. **Lindell Dental Partners**: **Who signs off on your bills** Dr. Maya Lindell, **Email for pay apps** billing@lindelldental.example, **Days between follow-ups** 7, **Days they usually take to pay** 14, **Paperwork needed with each pay app** "Signed contract, conditional waiver, completion photos, and pay-app backup."
- [ ] 2.4 Notes with commas stay notes: create a throwaway job for another new customer (`Ruiz Residence`) and type `Gate code 1234, call before 8am` in **Notes (kept on the job record)**. Expected: the job record holds a Note "Job notes" with that text, and the new customer's **Paperwork needed with each pay app** in **Settings > Customer settings** is the default list ("Signed contract, conditional waiver, completion photos, and pay-app backup."), not the note. Keep the job for 2.7.
- [ ] 2.5 Validation: open **New job** again, leave everything empty, **Save job**. Expected inline errors under each required field: "Enter the job name.", "Enter the job address.", "Enter the customer: who pays for this job.", "Enter the contract amount, like 48,500." Nothing under **Who signs off on your bills** or **Email for pay apps**: both are optional.
- [ ] 2.6 Nothing typed is lost: type a Job name and `48,500` in **Contract amount ($)**, fill the other required fields, open **More details (optional)** and type `kim at oak` as **Email for pay apps**, fold **More details** again, **Save job**. Expected: More details opens by itself and only that box turns red ("Enter the email pay apps go to, like ap@builder.com, or leave it blank for now."); the job name and `$48,500.00` are still there. Then **Cancel**.
- [ ] 2.7 Repeat customer: **New job**, type `Lind` in **Customer** and pick `Lindell Dental Partners` from the suggestions. Expected: "Pay apps go to Dr. Maya Lindell · billing@lindelldental.example. Change in Customer settings" shows under the five boxes, and under **More details** the two contact boxes are gone. Then type `Ironleaf General Contractors` as **Customer** and leave the box: **Who pays you?** turns to "A general contractor (you are the sub)" and **Retainage %** under More details shows the retainage on Ironleaf's last job. **Cancel**.
- [ ] 2.8 Homeowner job with no email: on the `Ruiz Residence` job from 2.4 (saved with both contact boxes empty; who signs off became "Ruiz Residence"), **Mark done**, **Confirm done**, then **Review & send** (beside the main **Add 4 missing documents** button) opens the pay app at **Before you send**: To shows "no email yet", an **Email for pay apps** box with **Save email**, and **Approve & send** is off ("Add the email pay apps go to, then send."). Type `ruiz@example.com`, **Save email**: Approve & send turns on (it still waits for the paperwork). Then **More > Delete draft** (confirm) and **Edit job > Delete job**: it can go, since its only pay app is a deleted draft (the confirm says "Nothing was billed: its pay apps are deleted drafts."); the customer stays.

## 3. Imports

Files are in `testdata/test-contractor/import/` and `bad/`. Upload in **Inbox** with **choose files**
(or drag the file onto the page). After each upload the card appears under **To file (N)** with
"Suggested because it ..." and a preselected job and kind. Don't file yet unless the step says so.

### 3a. Files into the Inbox

| # | Step | File | Expected toast | Expected card |
| --- | --- | --- | --- | --- |
| [ ] 3.1 | Upload | `05_email_co-request-from-gc-super.eml` | "1 file in the Inbox." | Suggested **Brookhaven Townhomes Bldg C** ("mentions the job name"), kind **Change order**, from Travis Mendez, Found: $2,340.00 |
| [ ] 3.2 | Upload | `06_email_signed-waiver-pdf-attached.eml` | same | **Decatur Library Annex** ("mentions pay app PA-DECA"), kind **Lien waiver**, two files: the .eml and `Conditional-Waiver-PA-DECA-01-signed.pdf` (opens as a PDF) |
| [ ] 3.3 | Upload | `07_email_payer-says-backup-missing.eml` | same | **Vesta Senior Living Phase 2** ("mentions pay app PA-VEST"), kind **Pay app / invoice backup** |
| [ ] 3.4 | Upload | `08_email_photo-attachment-from-foreman.eml` | same | **Mill Creek Clubhouse** ("mentions job code MILL"), kind **Note** (not Photo: see APP-ISSUES 3), files: .eml and `IMG_2240.jpg` |
| [ ] 3.5 | Upload | `09_email_matches-no-job.eml` | same | No job: "No job name, code, address or customer found.", kind **Note**, Found: $24.10, $18.75 |
| [ ] 3.6 | Upload | `10_email_portal-notice-html-only.eml` | same | **Ashby Lofts Amenity Deck** ("mentions pay app PA-ASHB"), kind **Pay app / invoice backup**; the text is readable (HTML turned into text) |
| [ ] 3.7 | Upload | `13 Subcontract KENN Kennesaw Ortho Clinic.pdf` | same | **Kennesaw Ortho Clinic** (matched by the file name only; PDFs are not read), kind **Contract** |
| [ ] 3.8 | Upload | `14 photo MILL lobby floor IMG_2231.jpg` | same | **Mill Creek Clubhouse** ("mentions job code MILL"), kind **Photo** |

- [ ] 3.9 File one: on the 3.8 card keep the job and kind, tap **File to job record**. Expected: toast "Saved"; the card moves to **Recently filed or dismissed** as "Filed to Mill Creek Clubhouse as Photo"; MILL's Job record shows it with the file link.
- [ ] 3.10 Undo it: open **Recently filed or dismissed**, tap **Undo** on that row. Expected: the card is back under To file; MILL's record no longer lists it. File it again (you need a MILL record in part 4).
- [ ] 3.11 Straight onto a job: open **Kennesaw Ortho Clinic**, in the Job record card tap **choose files** and pick `13 Subcontract KENN Kennesaw Ortho Clinic.pdf`. Expected: toast "1 file added to the job record."; a **Contract** record with the PDF. Nothing new under **To file** (the upload is listed, already filed, under **Recently filed or dismissed** as "Filed to Kennesaw Ortho Clinic as Contract").

### 3b. Pasted text (Inbox > "Paste an email, a text thread, or notes")

- [ ] 3.12 Set **What are you pasting?** to **Text messages**, paste all of `11_text-thread_whitfield-done.txt`, **Add to Inbox**. Expected: "Added to the Inbox."; card suggested **Whitfield Kitchen and Baths** ("mentions the job name"), and a notice "This says the job is done." with the button **Mark Whitfield Kitchen and Baths done and draft the pay app**. Today shows the same item tagged "Job's done?" with **Mark done and draft**. Don't press it yet (part 5.3).
- [ ] 3.13 Set it to **Notes: one daily record per date**, paste all of `12_field-notes_split-into-daily-records.txt`, **Add to Inbox**. Expected: **8** cards, all kind **Daily record**:

  | Card starts with | Note date | Suggested job |
  | --- | --- | --- |
  | Field notes - Nadia's phone | none | no job |
  | Mon 10/5 - Started layout at MILL ... (with the "used 3/4 inch" line) | Oct 5 | Mill Creek Clubhouse |
  | 10/6: KENN walkthrough ... | Oct 6 | Kennesaw Ortho Clinic |
  | Oct 8, 2026: GLEN storefront ... | Oct 8 | Glenwood Retail Shell |
  | 10/9 ASHB travertine pavers delivered (with the "May 2 guys out sick" line) | Oct 9 | Ashby Lofts Amenity Deck |
  | 2026-10-12 ASHB pavers 600 sf down | Oct 12 | Ashby Lofts Amenity Deck |
  | - 10/14 BROO bathroom floor tile ... | Oct 14 | Brookhaven Townhomes Bldg C |
  | 10/19: VEST corridor tile 90% complete ... | Oct 19 | Vesta Senior Living Phase 2 |

  "3/4 inch" and "May 2 guys" must NOT start new records.
- [ ] 3.14 File the Oct 5 card to Mill Creek Clubhouse. Expected: MILL's Job record gets "Daily record 2026-10-05" (Daily record): the label carries the note's date; the line under it reads "Notes import · " and the day you filed it. **Today > Daily record** lists it dated Oct 5, 2026, not today.
- [ ] 3.15 Set it to **An email**, paste all of `15_pasted-email_wrenfield-punch-list.txt`, **Add to Inbox**. Expected: suggested **Decatur Library Annex**, "from Joan Pettis <ap@wrenfieldcg.example>", subject "Decatur Library Annex - punch list before retainage release".
- [ ] 3.16 Inbound email (the address a mail provider would post to): in the Inbox open **Bring emails and texts in automatically**, copy the email web address, then in a terminal:
      `curl -s --data-binary @"testdata/test-contractor/import/16_inbound-webhook_crew-says-glenwood-finished.eml" -H "Content-Type: message/rfc822" <the address>`
      Expected: `{"ok": true}`; after a reload, a card from Luis Ortega "Glenwood wrapped", source pill Email, suggested **Glenwood Retail Shell**, with **Mark Glenwood Retail Shell done and draft the pay app**.

### 3c. QuickBooks payments CSV (Money > Payments)

- [ ] 3.17 Open **Import payments from QuickBooks (CSV)**, paste all of `01_qbo-payments_partial-nomatch-odd-formats.csv` (an unfiltered Transaction List: it also has a refund and an invoice), tap **Read payments**. Expected three warnings at the top: "Line 12: skipped, a Refund is not money received.", "Line 13: skipped, the amount 'pending' isn't a number." and "Line 14: skipped, an Invoice is not money received." and 6 rows (the invoice 1043 for PA-VEST-01 is not one of them):

  | Number | Customer | Amount | Date shown | Pay app / pill | Ticked |
  | --- | --- | --- | --- | --- | --- |
  | ACH-30211 | Corbel Street Properties | $36,020.00 | Oct 14, 2026 | PA-ASHB-01 · Ashby Lofts Amenity Deck ("The memo or number names PA-ASHB-01.") | yes |
  | 4471 | Ironleaf General Contractors | $28,750.00 | Oct 15, 2026 (from `2026-10-15`) | PA-GLEN-01 · Glenwood Retail Shell | yes |
  | 9902 | Harmon & Vale Construction | $42,000.00 | Oct 16, 2026 (from `10/16/26`) | "Too much": $42,000.00 is more than the $37,800.00 owed on PA-VEST-01 | no |
  | CHK 40871 | Wrenfield Construction Group | $57,600.00 | Oct 17, 2026 (from `17-Oct-2026`) | "Already recorded" ("Payment CHK 40871 from Wrenfield Construction Group for $57,600.00 is already recorded on Decatur Library Annex.") | no |
  | 1187 | Priya Whitfield | $8,412.50 | Oct 20, 2026 (from `"Oct 20, 2026"`) | PA-WHIT-01 ("Same customer and exactly the $8,412.50 still owed.") | yes |
  | 2210 | Lakeside Fellowship Church | $1,850.00 | Oct 21, 2026 | "No match" | no |

  The TOTAL row and the report title lines are not shown.
- [ ] 3.18 **Record the ticked payments**. Expected: "3 payments recorded." Then: Ashby Lofts PA-ASHB-01 **Paid**, Whitfield PA-WHIT-01 **Paid**, Glenwood PA-GLEN-01 **Partly paid** with $1,250.00 outstanding.
- [ ] 3.19 Paste the same CSV again and **Read payments**. Expected: ACH-30211, 4471, 1187 and CHK 40871 now say "Already recorded"; nothing is ticked. **Cancel**.

### 3d. Schedule of values paste (read only, nothing saved)

- [ ] 3.20 Open **Kennesaw Ortho Clinic**, **Set up schedule of values**, paste all of `02_sov_MILL_excel-tab-with-header.txt` into the paste box. Expected: "Read 5 lines. Check them below, then save.", "Row 7 (Overhead and profit 10%) read as overhead and profit of 10%; check it below.", "Row 8 (Total) looks like a total or markup row and was skipped."; Overhead and profit 10; Schedule total $80,300.00, shown as different from the $92,400.00 contract.
- [ ] 3.21 Tap **Save schedule**. Expected: refused, "The schedule totals $80,300.00, but the contract is $92,400.00." Then **Cancel** (do not use "Save and change the contract").
- [ ] 3.22 Repeat 3.20 with `03_sov_ASHB_pdf-copy-spaces.txt` (expect 4 lines, the title row "has no numbers; skipped", the Total row skipped, $74,850.00) and `04_sov_BROO_typed-commas-headerless.txt` (expect 4 lines incl. "Tub surrounds" 18 ea at 1150.00, O&P 10%, $46,068.00). **Cancel** each time.

### 3e. Bad files (each must be refused or warned about, never half-imported)

- [ ] 3.23 Upload `bad/bad_01_page.html`. Expected toast: "bad_01_page.html: this file type can't be imported. Allowed: csv, doc, docx, eml, gif, heic, jpeg, jpg, pdf, png, txt, webp, xls, xlsx."
- [ ] 3.24 Upload `bad/bad_03_empty-file.txt`. Expected toast: "bad_03_empty-file.txt is empty."
- [ ] 3.25 Money > Payments CSV: paste `bad/bad_02_payments-wrong-headers.csv`, **Read payments**. Expected: "No header row with an Amount column and a Customer or Num column was found." and no rows.
- [ ] 3.26 SOV paste box (on KENN, then Cancel): `bad/bad_04_sov-quantity-without-rate.txt`. Expected: "Read 0 lines." and three "has a quantity but no rate or amount; skipped." lines.
- [ ] 3.27 Upload `bad/bad_05_email-with-zip-attachment.eml`. Expected: imported (suggested **Ashby Lofts Amenity Deck**) with a red line "Ashby-paver-drawings-rev3.zip: this file type can't be imported. Allowed: ..."; the PDF attachment is kept.

## 4. Money loop 1: Kirkwood Dental Suite, start to closed (no SOV, no retainage)

- [ ] 4.1 Open **Kirkwood Dental Suite** > **Progress pay app**. The card reads "Billed so far $0.00 of $27,450.00 contract (0%)." with **This draw $** picked (beside **Total done so far $** and **% complete**). **This draw ($)** `13,725`: the line under it reads "This pay app: $13,725.00." (no retainage on this job). **What was completed** `Operatories 1-3 floors set`, **Draft progress pay app**. Expected: PA-KIRK-01 Draft $13,725.00; pill **Needs paperwork**; next step "Attach the paperwork, then send: Signed contract, Conditional waiver, Completion photos, Pay-app backup"; primary button **Add 4 missing documents**; blockers "Signed contract is missing.", "Conditional waiver is missing.", "Completion photos is missing.", "Pay-app backup is missing." ("Contract LDP-0412" is only the number from the form, so it is not attached by itself.)
- [ ] 4.2 **Add 4 missing documents**. Each missing item has **Take a photo**, **Choose file**, a record list starting on "Choose a record…" with **Attach**, **I have this…** and **Not needed for this customer**. Tap **Attach** with nothing chosen: toast "Choose a record first.". On **Signed contract** pick "Contract LDP-0412" and tap **Attach**. Expected: ✓, "Attached: Contract LDP-0412" and **Remove**. Tap **Remove**: it is missing again and the job's blockers list it; attach it again. On a phone, **Take a photo** on an item opens the camera, and the photo is filed to the job record and attached to that item in one step (toast "1 file added to the job record and attached.").
- [ ] 4.3 For the other three: **I have this…**, type `Emailed to Dr. Lindell on 10/20` in **Where is it? (optional)**, **Save**. Expected: each shows "Marked as in hand: Emailed to Dr. Lindell on 10/20" and **Remove**; the Activity log has "… marked <item> as in hand: Emailed to Dr. Lindell on 10/20." (Leaving the box empty works too: "Marked as in hand.") The job's status reads "Pay app ready for review", and Today tags it "Ready to send" with **Review & send**.
- [ ] 4.4 Tap **Review & send** on Today's row: it opens the pay app (PA-KIRK-01) at **Before you send**. **Open the PDF**: "APPLICATION FOR PAYMENT", FROM block = Brightwater Tile & Stone LLC, 2480 Cobb Industrial Ct, Smyrna, GA 30080. **Before you send** shows To "Dr. Maya Lindell · billing@lindelldental.example" and the first-send notice (no "Your company address is blank on this PDF" notice: the loader filled the address in). Tap **Approve & send to billing@lindelldental.example**. Expected: PA-KIRK-01 Sent; status "Submitted to Lindell Dental Partners, awaiting Dr. Maya Lindell". The job page's Pay apps row reads "sent <today> · email failed", and Today's first row is **Didn't go out** for Kirkwood Dental Suite: "Pay application PA-KIRK-01 … to billing@lindelldental.example: not sent: email delivery is not set up". **Send again** keeps it there (mail is still not set up); with mail set up it would go out and the row would leave.
- [ ] 4.5 Job page: there is no main button while Lindell has the pay app. Next step reads "Waiting on Dr. Maya Lindell. Sent <today>; follow-up due <today + 5 days>." with **Record a payment** and **Follow up now** beside it (each with a second line saying why). **Record a payment**: pay app PA-KIRK-01 ("PA-KIRK-01 · $13,725.00 · Sent"), **Amount received ($)** already `$13,725.00` (what is still owed), **Date received** today; set it to yesterday, **Check or reference no.** `CHK 2207`, **Record**. Expected: the card closes; Money > Payments lists CHK 2207 with yesterday's date under **Received**; the pay app's **Payments received** card the same. Paid; status "Payment received, waiver not sent"; primary **Choose waiver and send**.
- [ ] 4.6 **Choose waiver and send** opens **Waivers: Kirkwood Dental Suite** with the type preselected (Unconditional, progress). With no Lien waiver on the job record it first shows "No signed waiver on the job yet" with **Take a photo** and **Choose file** (a file picked there is filed to the job as a Lien waiver). **Approve & send**. Expected: the email in the Outbox is "Unconditional waiver on progress payment for PA-KIRK-01 — Kirkwood Dental Suite".
- [ ] 4.6a Second draw, then correct it (before 4.7): **Progress pay app** reads "Billed so far $13,725.00 of $27,450.00 contract (50%)." **This draw ($)** `6,275`, **Draft progress pay app**: PA-KIRK-02 Draft $6,275.00. Open the form again: it says "This replaces PA-KIRK-02 ($6,275.00). Its number and paperwork stay." Tap **Total done so far $**, enter `18,000` (the line reads "This pay app: $4,275.00."): still **PA-KIRK-02**, now $4,275.00 (the first one is voided and kept out of the list), with the same paperwork. Enter `13,000`: refused under the box with "Enter more than the $13,725.00 already billed. …" before anything is sent. **% complete** `150` is refused with "Enter a percent from 1 to 100." **Delete draft** (under **More**): no draft left, and the progress goes back to $13,725.00. The form's **All of it: mark the job done** link closes it and opens **Mark done**.
- [ ] 4.7 **Mark done**, **Anything to note? (optional)** `All rooms complete`, **Confirm done**: a dialog asks "Mark Kirkwood Dental Suite done? This bills the rest of the contract on a final pay app."; **Cancel** leaves the job in progress; do it again and tap **Mark done**. Expected: the note is on the Job record as "Notes when marked done" and in History, not on the line under the job's name. PA-KIRK-02 Draft $13,725.00 (the voided draft's number is used again) with the same 4 items missing: a lien waiver that went out in 4.6 is not reused for this pay app, and "Contract LDP-0412" is only the number. On **Completion photos** tap **Not needed for this customer**: it shows a dash and "Not needed for this customer", the count drops by one, and Customer settings for Lindell Dental Partners no longer lists completion photos. The pay-app page (tap PA-KIRK-02) lists it with a dash and "not needed", not as missing.
- [ ] 4.8 Clear the rest with **I have this…** (`Sent with PA-KIRK-01`), send it (**Review & send**, then **Approve & send**), **Record a payment** (amount already $13,725.00) / `CHK 2291`, **Choose waiver and send** (this one is "Unconditional waiver on final payment").
- [ ] 4.9 **Finish job**: a dialog asks "Finish Kirkwood Dental Suite? It moves to Job history and no more payments can be recorded." **Cancel** changes nothing; tap **Finish job** again and confirm. Expected: "This job is closed. Its money record is read-only..."; it moves to **Job history** (2 closed jobs, 3 after 6.2).

## 5. Money loop 2: Mill Creek Clubhouse (SOV, stored materials, 5% retainage)

- [ ] 5.1 Open **Mill Creek Clubhouse** > **Add 2 missing documents**. Items: AIA-style pay app and Schedule of values with a ✓ and "Produced by Duracash with the pay app"; Conditional lien waiver and Insurance certificate missing.
- [ ] 5.2 On the job page, **Add a note to the record**: Label `COI Brightwater 2026-27`, Kind **Other**, **Add to record**. Back on the packet, attach it to **Insurance certificate**. On the waiver tap **I have this…** with `Emailed to Renee Calloway 10/22`.
- [ ] 5.3 Open PA-MILL-01: the continuation sheet lists all 5 lines; Restroom wall tile shows $4,000.00 stored. Amount $33,042.90 (lines $31,620.00 + O&P $3,162.00 - retainage $1,739.10). Copy shows pm@tallowcreekbuilders.example. **Approve & send to ap@tallowcreekbuilders.example**.
- [ ] 5.4 **Record a payment**, change the amount to `20000`, `CHK 51002`. Expected: **Partly paid**, outstanding $13,042.90, no waiver step yet. Money > Collections shows "PA-MILL-01 $13,042.90 of $33,042.90" and, inside Tallow Creek's 5 days, a greyed **Next follow-up <date>** instead of Send follow-up; Money > Pay apps > **Unpaid** lists PA-MILL-01 (Partly paid, $13,042.90 of $33,042.90); Today's **Unpaid** stage lists Mill Creek.
- [ ] 5.5 **Record a payment**: the amount is already $13,042.90; `CHK 51003`. Expected: **Paid**; **Choose waiver and send**; after it, retainage open $1,739.10 (Money > Retainage lists MILL $1,739.10, holding it up "Closeout / punch-list signoff", next "Mark the job done first").
- [ ] 5.6 The **Record a payment** button is gone (no pay app is open for payment), and **Follow up now** is gone too. There is no **Ask for retainage** on the job page; the Money card says "Released at closeout." under Retainage open.

## 6. Other stages

- [ ] 6.1 Retainage waits for the closeout paperwork: on Decatur Library Annex the job page has no **Ask for retainage** (not as a button, not under **More**); the Money card says "Closeout paperwork: 1 item missing." under Retainage open, and tapping it opens the closeout packet. The buttons sit as one main button and two beside it in the Next step card, with the rest under **More (N)** headed Bill, Get paid and Close out. Below it, **Before you can finish this job** lists "$6,400.00 held back (retainage) is still owed." with **Record retainage paid** and **Open closeout paperwork**, and there is no **Finish job** yet.
- [ ] 6.2 Retainage release (DECA): **Money > Retainage** shows Decatur Library Annex $6,400.00, holding it up "Punch-list signoff". **Open closeout packet**, **I have this…** `Punch walk signed 10/23 by Joan Pettis`, **Save**. (The job page now has **Ask for retainage**.) Back on Retainage tap **Ask for retainage**, confirm "Email a request for the $6,400.00 retainage to Joan Pettis · …?" (toast with the email outcome; status "Retainage follow-up sent to Joan Pettis"). On the job page **Record retainage paid**: **Retainage received ($)** `6,400`, **Reference** `CHK 41990`, **Record**. Then **Finish job** and confirm.
- [ ] 6.3 Job's done from a text (WHIT): on Today tap **Mark done and draft** on the Whitfield row. A dialog asks "Mark Whitfield Kitchen and Baths done? …" with **Cancel**, **Just file the message** (files the text to the job and does nothing else) and **Mark done and draft**; tap **Mark done and draft**. Expected: the job opens with PA-WHIT-02 Draft $8,412.50 and the homeowner packet's 4 blockers; PA-WHIT-01 stays Paid.
- [ ] 6.4 Slow payer (VEST): the job page has no main button; Next step reads "Waiting on Dwight Easley. Sent <sent date>; follow-up due <sent date + 7 days>.", and **Follow up now** says "Before the follow-up date, <date>." Tap it: the dialog asks "Email a follow-up on PA-VEST-01 to Dwight Easley · …? It's before the follow-up date, <date>."; **Cancel** sends nothing. (Today and Collections keep the follow-up for its date; once the date has passed the job page's main button is **Send follow-up**.)
- [ ] 6.5 Short pay (GLEN): PA-GLEN-01 stays **Partly paid** with $1,250.00 outstanding. There is no way to accept the short pay (known gap, see APP-ISSUES 5).
- [ ] 6.6 Change order (BROO): on the CO, **Request signature** is refused on a test server: "Signing links need the site's address (DURACASH_URL) set on the server." File the 3.1 Inbox card to Brookhaven as **Change order**, then on CO 1 **Signed on paper…**: the record list already shows "Brookhaven Townhomes Bldg C - change order request, shower niches" (the newest change-order record), beside **Take a photo of the signed copy**; **Save as signed**. Expected: CO 1 approved, contract total $52,918.00, the schedule gets a fifth line for the change order.
- [ ] 6.6a Change order without leaving the job (ASHB): job page **More** > **New change order**. Boxes: Title, **Amount ($), negative for a credit**, What changed (optional), **Schedule change, days (optional)**. Title `Owner supplies the pavers`, amount `-1,200`, days `-2`, **Create change order**. Expected: the card turns into "CO <n>: Owner supplies the pavers" with "A credit of $1,200.00 · -2 days on the schedule. Saved. Now get it signed." and **Request signature**, **Signed on paper…** and **Withdraw** in place; the Change orders list shows it at -$1,200.00 with no **Bill now (send waits for signature)** (that button is only on charges). **Withdraw** it and tap **Done** so later totals are unchanged. An amount of `0` is refused under the box.
- [ ] 6.7 Payer says backup is missing (VEST): file the 3.3 card to Vesta Senior Living Phase 2 as **Pay app / invoice backup**. Expected: the record "RE: Pay application PA-VEST-01 — Vesta Senior Living Phase 2" with the .eml file. Then in **Settings > Customer settings > Harmon & Vale Construction** add `; missing certified payroll backup` to **They send pay apps back when** and **Save customer** (toast "Saved").
- [ ] 6.7a Fix a customer typo: **Settings > Customer settings > Add customer**, **Customer name** `ruiz residence`, **Add customer**: refused under the name, "Another customer is already called ruiz residence." Change it to `Ruiz Residance` and add it. Open it and set **Customer name** to `Ruiz Residence`, **Save customer**: refused the same way. Under **Same customer as another one?** pick Ruiz Residence and tap **Merge**: a dialog asks "Merge Ruiz Residance into Ruiz Residence? It has no jobs. …"; confirm. Expected: Ruiz Residance is gone, Ruiz Residence keeps its own settings, and the Activity log has "… merged Ruiz Residance into Ruiz Residence (no jobs); kept Ruiz Residence's settings." (A customer with jobs moves them all to the one you pick.) On Ruiz Residence empty **They send pay apps back when** and **Days between follow-ups**, **Save customer**: the first stays empty and the days go back to 7.
- [ ] 6.8 Daily record: **Today > Daily record**, tap **Save to job record** with nothing picked. Expected: toast "Pick a job.". Pick Mill Creek Clubhouse, tap again with the box empty: "Write what happened today.". Type `Tile set in the locker rooms, 3 crew`, save. Leave the page and come back: Mill Creek Clubhouse is already picked.
