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
- [ ] 0.3 Open http://127.0.0.1:8791, choose **Sign in**, and use the email and password from `.login`.
      Expected: Today opens for "Brightwater Tile & Stone LLC".

Email is not set up on a test server, so every send ends with a toast like
"Saved. The email to ap@... was not sent: email delivery is not set up." That is expected: the
pay app is still marked sent, and the Outbox lists the email.

## 1. Look around (what the loader built)

- [ ] 1.1 **Jobs > Open jobs** lists 8 jobs: ASHB, BROO, DECA, GLEN, KENN, MILL, VEST, WHIT. Under the list: "1 closed job is under Job history." (PEAC).
- [ ] 1.2 Open **Mill Creek Clubhouse**. Primary button **Clear 4 blockers**. The Schedule of values card shows 5 lines and a $80,300.00 contract. PA-MILL-01 is a Draft for $33,042.90.
- [ ] 1.3 Open **Decatur Library Annex**. Money shows $6,400.00 retainage open; PA-DECA-01 is Paid.
- [ ] 1.4 **Settings > Payer playbook** shows 6 payers. Open **Tallow Creek Builders**: Follow-up days 5, Pay apps due by 25, Usual approval days 18, Copy pm@tallowcreekbuilders.example.
- [ ] 1.5 **Today** > Needs you: Mill Creek Clubhouse shows its first blocker and **Clear 4 blockers**. Within 5 days before the 25th (Wrenfield's cutoff), Kennesaw Ortho Clinic shows "Due soon" with **Bill progress** and the cutoff date. (MILL gets no cutoff row: it already has a pay app this cycle.)

## 2. Enter one job fully by hand (new customer)

Kirkwood Dental Suite was just awarded. Its customer is new, so this job also creates the payer.

- [ ] 2.1 **Jobs > Open jobs > New job**. Type exactly:

  | Field | Value |
  | --- | --- |
  | Job name * | `Kirkwood Dental Suite` |
  | Address * | `1910 Hosea L Williams Dr NE, Atlanta, GA 30317` |
  | Contract number | `LDP-0412` |
  | Contract amount ($) * | `27,450` (commas and a `$` are fine; leaving the box shows `$27,450.00`) |
  | Retainage % | `0` |
  | Customer * | `Lindell Dental Partners` |
  | Role | `Owner` |
  | Approver * | `Dr. Maya Lindell` |
  | AP email * | `billing@lindelldental.example` |
  | Phone | `404-555-0127` |
  | Notes for the job record | leave empty (see 2.4) |

- [ ] 2.2 **Save job**. Expected: toast "Job saved", the job page opens. Its code is **KIRK**. The Job record holds one record, "Contract LDP-0412" (Contract).
- [ ] 2.3 **Settings > Payer playbook** now has 7 payers. **Lindell Dental Partners**: Approver Dr. Maya Lindell, AP email billing@lindelldental.example, Follow-up days 7, Usual approval days 14, Packet "Signed contract, conditional waiver, completion photos, and pay-app backup."
- [ ] 2.4 Optional, to see a known trap: create a throwaway job for another new customer and type `Gate code 1234, call before 8am` in **Notes for the job record**. That text becomes the new payer's whole Packet (two items: "Gate code 1234", "Call before 8am"). Delete the job afterwards with **Edit job > Delete job** (the payer stays).
- [ ] 2.5 Validation: open **New job** again, leave everything empty, **Save job**. Expected inline errors under each field: "Enter the job name.", "Enter the job address.", "Enter the customer: who pays for this job.", "Enter who signs off on your bills.", "Enter the email pay apps go to, like ap@builder.com.", "Enter the contract amount, like 48,500."
- [ ] 2.6 Nothing typed is lost: type a Job name and `48,500` in **Contract amount ($)**, fill the other required fields but type `kim at oak` as **AP email**, **Save job**. Expected: only AP email turns red with its message; the job name and `$48,500.00` are still there. Then **Cancel**.

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

- [ ] 3.17 Open **Import payments from QuickBooks (CSV)**, paste all of `01_qbo-payments_partial-nomatch-odd-formats.csv`, tap **Read payments**. Expected two warnings at the top: "Line 12: skipped, (150.00) is not money received." and "Line 13: skipped, the amount 'pending' isn't a number." and 6 rows:

  | Number | Customer | Amount | Date shown | Pay app / pill | Ticked |
  | --- | --- | --- | --- | --- | --- |
  | ACH-30211 | Corbel Street Properties | $36,020.00 | Oct 14, 2026 | PA-ASHB-01 · Ashby Lofts Amenity Deck ("The memo or number names PA-ASHB-01.") | yes |
  | 4471 | Ironleaf General Contractors | $28,750.00 | Oct 15, 2026 (from `2026-10-15`) | PA-GLEN-01 · Glenwood Retail Shell | yes |
  | 9902 | Harmon & Vale Construction | $42,000.00 | Oct 16, 2026 (from `10/16/26`) | "Too much": $42,000.00 is more than the $37,800.00 owed on PA-VEST-01 | no |
  | CHK 40871 | Wrenfield Construction Group | $57,600.00 | Oct 17, 2026 (from `17-Oct-2026`) | "Already recorded" | no |
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

- [ ] 4.1 Open **Kirkwood Dental Suite** > **Progress pay app**. **Total done so far ($)** `13,725`, **What was completed** `Operatories 1-3 floors set`, **Draft progress pay app**. Expected: PA-KIRK-01 Draft $13,725.00; primary button **Clear 4 blockers**; blockers "Signed contract is missing.", "Conditional waiver is missing.", "Completion photos is missing.", "Pay-app backup is missing."
- [ ] 4.2 **Clear 4 blockers**. On **Signed contract** pick "Contract LDP-0412" and tap **Attach**. Expected: ✓ and "Attached: Contract LDP-0412".
- [ ] 4.3 For the other three: **Mark present…**, type `Emailed to Dr. Lindell on 10/20`, **Save reason**. Expected: each shows "Marked present: Emailed to Dr. Lindell on 10/20". The job's status reads "Pay app ready for review", and Today tags it "Ready to send" with **Review & send**.
- [ ] 4.4 Open the pay app (Money > Pay apps > PA-KIRK-01). **Open the PDF**: "APPLICATION FOR PAYMENT", FROM block = Brightwater Tile & Stone LLC, 2480 Cobb Industrial Ct, Smyrna, GA 30080. **Before you send** shows To "Dr. Maya Lindell · billing@lindelldental.example" and the first-send notice. Tap **Approve & send to billing@lindelldental.example**. Expected: PA-KIRK-01 Sent; status "Submitted to Lindell Dental Partners, awaiting Dr. Maya Lindell".
- [ ] 4.5 Job page > **Match payment**: pay app PA-KIRK-01, **Amount received ($)** `13,725`, **Reference** `CHK 2207`, **Match**. Expected: Paid; status "Payment received, waiver not sent"; primary **Send waiver**.
- [ ] 4.6 **Send waiver**. Expected: the email in the Outbox is "Unconditional waiver on progress payment for PA-KIRK-01 — Kirkwood Dental Suite".
- [ ] 4.7 **Mark done**, **Anything to note? (optional)** `All rooms complete`, **Confirm done**. Expected: PA-KIRK-02 Draft $13,725.00 with the same 4 blockers (each pay app gets its own packet).
- [ ] 4.8 Clear the 4 blockers with **Mark present…** (`Sent with PA-KIRK-01`), send it, **Match payment** `13725` / `CHK 2291`, **Send waiver** (this one is "Unconditional waiver on final payment").
- [ ] 4.9 **Close for record**. Expected: "This job is closed. Its money record is read-only..."; it moves to **Job history** (2 closed jobs, 3 after 6.2).

## 5. Money loop 2: Mill Creek Clubhouse (SOV, stored materials, 5% retainage)

- [ ] 5.1 Open **Mill Creek Clubhouse** > **Clear 4 blockers**. Items: AIA-style pay app, Schedule of values, Conditional lien waiver, Insurance certificate.
- [ ] 5.2 On the job page, **Add a note to the record**: Label `COI Brightwater 2026-27`, Kind **Other**, **Add to record**. Back on the packet, attach it to **Insurance certificate**. Mark the other three present with `Emailed to Renee Calloway 10/22`.
- [ ] 5.3 Open PA-MILL-01: the continuation sheet lists all 5 lines; Restroom wall tile shows $4,000.00 stored. Amount $33,042.90 (lines $31,620.00 + O&P $3,162.00 - retainage $1,739.10). Copy shows pm@tallowcreekbuilders.example. **Approve & send to ap@tallowcreekbuilders.example**.
- [ ] 5.4 **Match payment** `20000` / `CHK 51002`. Expected: **Partly paid**, outstanding $13,042.90, no waiver step yet.
- [ ] 5.5 **Match payment** `13042.90` / `CHK 51003`. Expected: **Paid**; **Send waiver**; after it, retainage open $1,739.10 (Money > Retainage lists MILL $1,739.10, holding it up "Closeout / punch-list signoff", next "Mark the job done first").
- [ ] 5.6 The **Match payment** button is gone (no pay app is open for payment), and **Follow up** is gone too.

## 6. Other stages

- [ ] 6.1 Ask for retainage too early: on Decatur Library Annex, the job page's **Ask for retainage** is refused in a red card right under the buttons (not a toast) with "Closeout packet incomplete." and "Punch-list signoff is missing."
- [ ] 6.2 Retainage release (DECA): **Money > Retainage** shows Decatur Library Annex $6,400.00, holding it up "Punch-list signoff". **Open closeout packet**, **Mark present…** `Punch walk signed 10/23 by Joan Pettis`, **Save reason**. Back on Retainage tap **Ask for retainage** (toast with the email outcome; status "Retainage follow-up sent to Joan Pettis"). On the job page **Record retainage paid**: **Retainage received ($)** `6,400`, **Reference** `CHK 41990`, **Record**. Then **Close for record**.
- [ ] 6.3 Job's done from a text (WHIT): on Today tap **Mark done and draft** on the Whitfield row. Expected: the job opens with PA-WHIT-02 Draft $8,412.50 and the homeowner packet's 4 blockers; PA-WHIT-01 stays Paid.
- [ ] 6.4 Slow payer (VEST): job page **Follow up**. Expected: refused, in a card under the buttons, "Follow-up on PA-VEST-01 is not due until <sent date + 7 days> (Harmon & Vale Construction expects 7 days between contacts)."
- [ ] 6.5 Short pay (GLEN): PA-GLEN-01 stays **Partly paid** with $1,250.00 outstanding. There is no way to accept the short pay (known gap, see APP-ISSUES 5).
- [ ] 6.6 Change order (BROO): on the CO, **Request signature** is refused on a test server: "Signing links need the site's address (DURACASH_URL) set on the server." File the 3.1 Inbox card to Brookhaven as **Change order**, then on CO 1 **Signed on paper…**, pick "Brookhaven Townhomes Bldg C - change order request, shower niches", **Save as signed**. Expected: CO 1 approved, contract total $52,918.00, the schedule gets a fifth line for the change order.
- [ ] 6.7 Payer says backup is missing (VEST): file the 3.3 card to Vesta Senior Living Phase 2 as **Pay app / invoice backup**. Expected: the record "RE: Pay application PA-VEST-01 — Vesta Senior Living Phase 2" with the .eml file. Then in **Settings > Payer playbook > Harmon & Vale Construction** add `; missing certified payroll backup` to **Rejects when** and **Save playbook** (toast "Saved").
