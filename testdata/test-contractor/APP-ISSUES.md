# App issues found while building the test contractor

Found on 2026-10-22 by running `scripts/load_test_contractor.py` and the steps in
`MANUAL-TEST.md` against a throwaway server. No app code was changed. Each issue has the code
location and a repro that runs from the repo folder (`DURACASH_DATA_DIR` points at a scratch
folder so nothing real is touched).

## 1. Notes lines starting with dates like 10/16, 9/16, 1/2 or 3/4 are read as fractions, not dates (medium)

**Where:** `importer.py:266-269`, in `leading_date`. A date without a year and without a
separator after it is refused when the first number is smaller than the second and the second is
2, 4, 8, 16, 32 or 64. That guard is meant for "3/4 inch pipe" and "1/2 bath", but it also catches
real dates: Oct 16 (10/16), Sep 16, Nov 16, Jan 2, Jan 4, Mar 4, Mar 8, May 8 and others.

**Docs it contradicts:** the Inbox paste card says "Notes split at every line that starts with a
date (10/12, Oct 12, 2026-10-12)" (`static/app.js:1988`); its own placeholder uses the same
"10/12 Framing done ..." style with no separator.

**Repro:** `testdata/test-contractor/app-issues/notes_date-read-as-fraction.txt`

```
DURACASH_DATA_DIR=/tmp/bd-scratch python3 -c "import importer; print([c['date'] for c in importer.split_notes(open('testdata/test-contractor/app-issues/notes_date-read-as-fraction.txt').read(), 2026)])"
```

Expected `[None, '2026-10-15', '2026-10-16', '2026-01-02']`; actual `[None, '2026-10-15']`. The
10/16 VEST line and the 1/2 MILL line are merged into the Oct 15 record, so the Oct 16 work is
filed under the wrong day and the wrong job's suggestion. Writing `10/16:` or `10/16 -` works,
which is why the main notes file in `import/` uses separators.

**Fix idea:** only treat it as a fraction when a unit word or `"` follows (the `UNIT_WORDS` check
already does that), or when the numerator is not a valid month.

## 2. Underscores in file names stop job codes and kinds from matching (medium)

**Where:** `importer.py:326` finds job codes with `\b[A-Z0-9]{3,8}\b`, and the kind rules at
`importer.py:72-79` (used by `guess_kind`, `importer.py:299-306`) use `\b...\b`. In regular
expressions `_` is a word character, so `KENN_subcontract.pdf` contains neither the token `KENN`
nor the word `subcontract`. For PDFs, photos and Office files the file name is the only thing
matched (only .txt and .csv contents are read, `server.py:1730-1732`), and scanners and phones
name files with underscores (`IMG_2231.jpg`, `Scan_KENN_contract.pdf`).

**Repro:**

```
DURACASH_DATA_DIR=/tmp/bd-scratch python3 -c "
import importer
st={'jobs':[{'id':'j1','code':'KENN','name':'Kennesaw Ortho Clinic','address':'3895 Cherokee St NW','payer_id':'p'}],'payers':[]}
for n in ['KENN_subcontract.pdf','KENN subcontract.pdf']: print(n, importer.match_job(st, n)['job_id'], importer.guess_kind(n,'','.pdf'))"
```

Actual: `KENN_subcontract.pdf None note`, `KENN subcontract.pdf j1 contract`. Job-name matching
is not affected (it normalizes punctuation first, `importer.py:311-312`). The test files in
`import/` use spaces in the two names that are matched by file name (13, 14) for this reason.

**Fix idea:** run the code and kind patterns on the file name with `_` replaced by a space.

## 3. An email whose attachment is a photo is filed as a Note, not a Photo (low)

**Where:** `importer.py:393` takes the kind's file extension from the item's first file. For an
uploaded `.eml` the first stored file is the email itself (`server.py:1746`), so the extension is
`.eml` and the image rule in `guess_kind` (`importer.py:304-305`) never applies.

**Repro:** upload `import/08_email_photo-attachment-from-foreman.eml` in the Inbox. The card's
kind is **Note**; its only attachment is `IMG_2240.jpg`. (`14 photo MILL lobby floor IMG_2231.jpg`
uploaded on its own is a **Photo**.)

**Fix idea:** guess from the attachments' extensions when the email text gives no kind.

## 4. A blocked, unsent draft hides the payer's cutoff on Today (medium)

**Where:** `static/app.js:590-594`. `billedThisCycle` counts any non-void pay app created since
the last cutoff, drafts included. So once a progress pay app is drafted, the "Pay app due to ..."
cutoff text and its higher rank (1.5, `static/app.js:610`) disappear, even while the draft is
blocked and has not gone out.

**Repro:** after the loader, Mill Creek Clubhouse (Tallow Creek, pay apps due by the 25th) has
PA-MILL-01 drafted and blocked. From Oct 20 to 25 its Today row is the plain blocked row (rank 3)
with no cutoff date. Kennesaw Ortho Clinic, with no pay app, does get "Due soon" for the
Wrenfield cutoff on the 25th. The job closest to missing a cutoff is the one not warned.

**Fix idea:** count only sent pay apps (status sent, approved, partial or paid) as billed this
cycle, or keep the cutoff text on a blocked or ready draft.

## 5. Short pays can't be settled (product gap, already known) — fixed in R4-01

Fixed: **Accept $1,250.00 short pay…** on the pay app, the Record a payment card, Collections and
the "Before you can finish this job" list settles it with a reason (MANUAL-TEST 6.5). The report
as it was:

Ironleaf's PA-GLEN-01 ($30,000.00) gets $28,750.00 from the CSV import. It stays **Partly paid**
with $1,250.00 outstanding, no waiver comes due, and Close for record is blocked by
"$1,250.00 is still outstanding." (`domain.py:98`). There is no write-off or "accept short
pay" action (`server.py:2213-2246` lists every job action). Already in the friction maps; listed
here so testers don't report it again.

## Notes (not bugs)

* Payments read from the CSV are dated as QuickBooks shows them (Oct 14 to 21, 2026). The loader
  sends the pay apps on the day it runs, so those payments predate the send and are left out of
  "Pays in about N days" (`domain.py:1016` skips negative gaps). Expected with test data.
* An uploaded `.eml` reply to a pay app is never auto-filed; only the inbound webhook runs
  `capture_reply` (`server.py:1807`), and only for an email whose Outbox status is `sent`,
  which a server without email never has (`importer.py:513`).
