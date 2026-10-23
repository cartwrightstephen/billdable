# Brightwater Tile & Stone LLC (fictional test contractor)

Everything here is made up for testing Duracash: the company, the people, the general
contractors, the owners and the addresses' house numbers. Email domains are `*.example`
(reserved, never deliverable) and phones are `404-555-01xx`. If a name happens to match a real
business, that is a coincidence; nothing here describes one.

`scripts/load_test_contractor.py` enters all of this through Duracash's own HTTP API.
`MANUAL-TEST.md` is the step-by-step human test. `APP-ISSUES.md` lists app problems the data found.

## Profile

| | |
| --- | --- |
| Company | Brightwater Tile & Stone LLC |
| Trade | Commercial and residential tile and stone: porcelain floors, restroom and shower walls, travertine pavers, stone sills and thresholds |
| Office | 2480 Cobb Industrial Ct, Smyrna, GA 30080 |
| Phone | 404-555-0110 |
| Business licence (Duracash's "Contractor licence number") | Cobb County BL-2026-04417 |
| Revenue | about $1.5M a year, 30 to 40 jobs, mostly $15k to $130k |
| How they bill | Pay apps to GCs and property owners (AIA-style, many with a schedule of values); homeowners get a pay app per milestone |
| Books | QuickBooks Online; the office manager exports payments as CSV |

## Crew (6 people)

| Person | Role | In Duracash |
| --- | --- | --- |
| Nadia Brooks | Owner, estimator, signs waivers | The account owner the loader signs up (`nadia.brooks+xxxxxx@brightwatertile.example`) |
| Carla Jennings | Office manager: billing, payments, the QuickBooks export | Can be invited as a Member (office@brightwatertile.example) |
| Luis Ortega | Foreman; texts and emails photos from the field | Sender of the photo email, the text thread and the "Glenwood is finished" email |
| Dre Watkins | Lead setter | Field notes only |
| Sam Huynh | Setter | Field notes only |
| Eli Brandt | Helper / labor | Field notes only |

## Payers (7, each behaves differently)

The first job for a customer creates the payer (Duracash has no "add payer" screen). The
loader then sets each playbook through Settings > Payer playbook's API.

| Payer | Type (Duracash role) | Approver / AP email | Behavior to test | Playbook set by the loader |
| --- | --- | --- | --- | --- |
| Tallow Creek Builders | GC | Renee Calloway, ap@tallowcreekbuilders.example, 404-555-0141, copy pm@tallowcreekbuilders.example | **Fast payer** (about 18 days). Pay apps are due by the 25th | Follow-up 5 days, usual approval 18, due day 25. Packet: AIA-style pay app, schedule of values, conditional lien waiver, insurance certificate |
| Harmon & Vale Construction | GC | Dwight Easley, payables@harmonvale.example, 404-555-0152 (super Travis Mendez, tmendez@harmonvale.example) | **Slow payer** (45+ days) that needs follow-ups; sends pay apps back for missing certified payroll | Follow-up 7, usual approval 45, due day 20. Packet adds certified payroll. Rejects when: missing certified payroll; pay app not notarized |
| Corbel Street Properties | Owner (property manager) | Alana Grant, invoices@corbelstreet.example, 404-555-0163, copy alana.grant@corbelstreet.example | **Portal payer**: pay apps must also be uploaded to their vendor portal; the portal sends an HTML-only "approved" email | Follow-up 10, usual approval 30. Packet: pay app, conditional lien waiver, vendor portal upload confirmation |
| Ironleaf General Contractors | GC | Kevin Strand, ap@ironleafgc.example, 404-555-0174 | **Short-pays**: backcharges $1,250 "cleanup" and pays the rest | Follow-up 7, usual approval 30. Packet: AIA-style pay app, conditional lien waiver, cleanup log |
| Wrenfield Construction Group | GC | Joan Pettis, ap@wrenfieldcg.example, 404-555-0185 | **Holds 10% retainage** until a signed punch list | Follow-up 10, usual approval 30, due day 25. Packet: AIA-style pay app, conditional lien waiver, insurance certificate, punch-list signoff (the last one becomes the closeout packet) |
| Tom & Priya Whitfield | Owner (homeowner) | Priya Whitfield, priya.whitfield@mailbox.example, 404-555-0196 | **Homeowner**: pays fast by bank transfer, shows in QuickBooks as "Priya Whitfield" | Follow-up 3, usual approval 7. Packet: Duracash's homeowner default |
| Lindell Dental Partners | Owner (tenant) | Dr. Maya Lindell, billing@lindelldental.example, 404-555-0127 | **New customer**: not loaded. Stephen enters it by hand (MANUAL-TEST.md part 2) | Created by the hand-entered job |

## Jobs (10 at different stages)

Job codes are what Duracash makes from the first four letters of the name. Pay app numbers are
`PA-<code>-NN`. Amounts are exact; the loader checks every one.

| Code | Job | Payer | Contract | Retainage | Stage after the loader runs |
| --- | --- | --- | --- | --- | --- |
| KIRK | Kirkwood Dental Suite, 1910 Hosea L Williams Dr NE, Atlanta | Lindell Dental Partners | $27,450.00 | 0% | **Bidding / just awarded**: not in Duracash. Entered by hand in MANUAL-TEST.md |
| KENN | Kennesaw Ortho Clinic, 3895 Cherokee St NW, Kennesaw | Wrenfield | $92,400.00 | 10% | **Active, not billed yet**. Subcontract WCG-SUB-2291 (PDF in import/) |
| MILL | Mill Creek Clubhouse, 2150 Mill Creek Pkwy, Marietta | Tallow Creek | $80,300.00 (SOV: $73,000.00 + 10% O&P) | 5% | **Active with SOV, pay app due**: PA-MILL-01 drafted for $33,042.90 (includes $4,000 stored wall tile), blocked by 4 packet items |
| ASHB | Ashby Lofts Amenity Deck, 620 Ashby Lofts Way NW, Atlanta | Corbel Street | $74,850.00 (SOV, no O&P) | 0% | **Active with SOV, sent**: PA-ASHB-01 $36,020.00 sent; paid in full by the CSV import (portal payment) |
| BROO | Brookhaven Townhomes Bldg C, 1475 Dresden Dr NE, Brookhaven | Harmon & Vale | $46,068.00 (SOV: $41,880.00 + 10% O&P) | 10% | **Change order pending**: CO 1 "Upgrade to large-format porcelain, units C4-C6" $6,850.00 not signed; the super's email asks for CO 2 |
| VEST | Vesta Senior Living Phase 2, 3300 Chamblee Dunwoody Rd, Chamblee | Harmon & Vale | $128,600.00 | 10% | **Sent and unpaid**: PA-VEST-01 $37,800.00 ($42,000 less $4,200 retainage). Payer says backup is missing |
| GLEN | Glenwood Retail Shell, 1020 Glenwood Ave SE, Atlanta | Ironleaf | $52,300.00 | 0% | **Partly paid (short pay)**: PA-GLEN-01 $30,000.00, CSV pays $28,750.00, $1,250.00 stays outstanding |
| WHIT | Whitfield Kitchen and Baths, 77 Willow Bend Ct, Tucker | Whitfields | $16,825.00 | 0% | **Homeowner, paid, waiver due**: PA-WHIT-01 $8,412.50 paid by the CSV import; foreman's text says the job is done |
| DECA | Decatur Library Annex, 215 Sycamore St, Decatur | Wrenfield | $64,000.00 | 10% | **Retainage held**: done, PA-DECA-01 $57,600.00 paid (CHK 40871), waiver sent, $6,400.00 retainage open until the punch list is signed |
| PEAC | Peachtree Hills Condo Baths, 418 Lindbergh Way NE, Atlanta | Tallow Creek | $38,500.00 | 0% | **Closed**: done, PA-PEAC-01 paid (ACH 7781), waiver sent, closed for the record |

With `--only-setup` the stages are the same except ASHB, GLEN and WHIT are still "sent, unpaid"
(the CSV import pays them) and the Inbox is empty.

### Schedules of values (pasted, not typed)

| Job | File | Format it tests | Lines |
| --- | --- | --- | --- |
| MILL | `import/02_sov_MILL_excel-tab-with-header.txt` | Excel/Sheets copy (tabs) with a header row, an "Overhead and profit 10%" row and a Total row | Floor prep 4,200 sf x $1.85; porcelain floor 3,600 sf x $9.75; restroom wall tile 1,450 sf x $14.20; shower pans 6 ea x $1,150.00; thresholds 1 LS $2,640.00 |
| ASHB | `import/03_sov_ASHB_pdf-copy-spaces.txt` | Copied from a PDF (columns are runs of spaces), no header, title line, "2,800 sf" quantity+unit cells, a lump-sum row | Pavers 2,800 sf x $16.50; coping 340 lf x $38.00; outdoor kitchen veneer LS $9,850.00; drainage mat 2,800 sf x $2.10 |
| BROO | `import/04_sov_BROO_typed-commas-headerless.txt` | Typed comma rows, no header, `$1,150.00` split by the comma and rejoined, "O&P 10%" row | Floor tile 960 sf x $8.50; tub surrounds 18 ea x $1,150.00; backsplash 18 ea x $640.00; permit and protection $1,500.00 lump sum |

### Progress already recorded by the loader

* MILL: floor prep 4,200 sf (all), porcelain floor 1,800 sf, shower pans 2 ea, $4,000.00 wall tile stored. Base $31,620.00 + O&P $3,162.00 = $34,782.00, less 5% retainage $1,739.10 = **$33,042.90**.
* ASHB: pavers 1,400 sf ($23,100.00), coping 340 lf ($12,920.00) = **$36,020.00**.
* VEST $42,000.00 to date; GLEN $30,000.00; WHIT $8,412.50 (dollar progress, no SOV).
