# Case company: Sweetgum Ridge Mechanical

A made-up mechanical subcontractor in Decatur, Georgia, with five people, three clients, three jobs and three weeks of daily job history, Monday November 2 to Friday November 20, 2026. Testers type it into Duracash in order, day by day, and check the figures at each **Checkpoint**.

Everything is made up: the companies, people, addresses, phone numbers (555-01xx) and jobs. Only your own email address is real.

This guide is written from the same data that `tests/test_case_company.py` types into the app on every test run, checking every figure below. If the app changes and this guide stops matching, that test fails.

## How to work through it

- Go in order. Quantities count when they are saved, not by their date, so a pay app picks up whatever has been entered before it.
- Type the **dates** given for job records, change order signatures and the job start dates. Sending a pay app, signing it, recording a payment and sending a waiver are dated the day you do them, which is fine: the figures to check are dollars, and they don't depend on the day.
- Use your own inbox with plus-addressing: add `+dana`, `+rosa` and so on before the `@`. Below, `you@example.com` stands for your address. On staging, each address you use has to be on the email allow-list; send them to Yusuf first.
- Have two or three photos on hand. Any photos will do: they stand in for signed change orders, signed waivers and time sheets.
- About three hours in all: the setup takes 20 minutes, each week about an hour.

## The company

Sign up with "Your name" Dana Whitfield, "Company name" Sweetgum Ridge Mechanical and your `+dana` address. Then Settings > Company > "Company details":

| Box | Type this |
|---|---|
| "Company name" | Sweetgum Ridge Mechanical |
| "Phone" | (404) 555-0163 |
| "Street address" | 2207 Lawrenceville Hwy, Unit 4 |
| "City" / "State" / "ZIP code" | Decatur / GA / 30033 |
| "Contractor licence number" | MP-210468 |
| "Legal name (on contracts and lien waivers)" | Sweetgum Ridge Mechanical, LLC |

## The team

Settings > Team. First add three permission profiles (only an Owner can), then invite four people.

| Profile | "It can" boxes to tick | Why |
|---|---|---|
| Office | Send pay apps, Send lien waivers, Ask for retainage, Record payments, See company financials, Edit company details, client settings and connections, See payroll and sensitive documents | Rosa runs billing: sends pay apps and waivers, records payments, keeps client settings, opens payroll and waivers. |
| Project manager | Change a job's contract, change orders and mark work done, Approve pay apps before they go, Finish and reopen jobs | Marcus runs the jobs: change orders, schedule changes, signs off pay apps, finishes jobs. |
| Field | none | The crew enter job records only. Anyone on the team can do that. |

| Person | Job | "Their email" | "Profile" |
|---|---|---|---|
| Dana Whitfield | Owner | `you+dana@example.com` | Owner (you) |
| Rosa Delgado | Office manager and bookkeeper | `you+rosa@example.com` | Office |
| Marcus Bell | Project manager | `you+marcus@example.com` | Project manager |
| Terrell Hughes | Field foreman (HVAC) | `you+terrell@example.com` | Field |
| Kenji Park | Plumber | `you+kenji@example.com` | Field |

Accepting the invites is optional. To test permissions, accept Terrell's in another browser and enter one week's job records for Linden Hollow as him: he can save job records but can't send a pay app or change the contract.

## The clients

Clients > "Add client" for each. Tick the supporting documents listed.

| Box | Ironbridge Builders | Thornwell Construction | Lena Ortiz |
|---|---|---|---|
| "Client type" | General contractor | General contractor | Homeowner |
| "Who signs off on your bills" | Paul Kessler | Derrick Owens | Lena Ortiz |
| "Email for pay apps" | `you+ironbridge@example.com` | `you+thornwell@example.com` | `you+ortiz@example.com` |
| "Phone" | (404) 555-0171 | (678) 555-0182 | (770) 555-0194 |
| "Pay apps due by (day of the month)" | 25 | 20 | blank |
| "Payment terms, days" | 30 | 45 | 10 |
| Supporting documents | Insurance certificate, Daily tickets and time sheets, Progress photos | Insurance certificate, Certified payroll, Daily tickets and time sheets | leave unticked |
| What they are like | Commercial general contractor. Monthly pay apps by the 25th, 10% retainage, pays in 30 days. | General contractor on a public school job. Certified payroll with every pay app, 5% retainage, pays in 45 days. | Homeowner. No retainage; pays on completion, within 10 days. |

Lena Ortiz signs her own bills, so when it asks whether the signer is the client herself, say yes. On Thornwell's client card, set "Usual retainage" to 5 and tap "Save client".

## The jobs

Open jobs > "New job" for each, with the client picked under "Existing client". Open "Schedule of values" and type the lines; they add up to the contract amount exactly.

### Linden Hollow Office Suite 210

HVAC for a second-floor office fit-out: duct, three rooftop units, VAV boxes, grilles, controls, balancing.

| Box | Type this |
|---|---|
| "Job name" | Linden Hollow Office Suite 210 |
| "Street address" / "City" / "State" / "ZIP" | 410 Linden Hollow Rd, Suite 210 / Atlanta / GA / 30309 |
| "Client" | Ironbridge Builders |
| "Contract amount ($)" | $148,600.00 |
| "Retainage" % | 10 |
| "Start date" | Nov 2, 2026 |
| "Anticipated end date" | Dec 18, 2026 |
| "Contract number" | IB-2026-114 |
| "Assigned to" (once the team has joined) | Marcus Bell |

| # | "Description" | "Qty" | "Unit" | "Unit rate $" | Line total |
|---|---|---|---|---|---|
| 1 | Mobilization and submittals | 1 | LS | 6,400.00 | $6,400.00 |
| 2 | Galvanized ductwork | 1,200 | LF | 42.00 | $50,400.00 |
| 3 | Rooftop units, set and connect | 3 | EA | 14,800.00 | $44,400.00 |
| 4 | VAV boxes | 12 | EA | 1,650.00 | $19,800.00 |
| 5 | Grilles, registers and diffusers | 60 | EA | 185.00 | $11,100.00 |
| 6 | Controls and thermostats | 1 | LS | 9,800.00 | $9,800.00 |
| 7 | Test, adjust and balance | 1 | LS | 6,700.00 | $6,700.00 |
| | **Total** | | | | **$148,600.00** |

### Sorrel Creek Elementary Restrooms

Plumbing for two student restrooms: demolition, underground waste, water piping, 24 fixtures, a water heater.

| Box | Type this |
|---|---|
| "Job name" | Sorrel Creek Elementary Restrooms |
| "Street address" / "City" / "State" / "ZIP" | 3900 Sorrel Creek Rd / Decatur / GA / 30033 |
| "Client" | Thornwell Construction |
| "Contract amount ($)" | $96,450.00 |
| "Retainage" % | 5 |
| "Start date" | Nov 9, 2026 |
| "Anticipated end date" | Jan 15, 2027 |
| "Contract number" | TC-0457-P |
| "Assigned to" (once the team has joined) | Marcus Bell |

| # | "Description" | "Qty" | "Unit" | "Unit rate $" | Line total |
|---|---|---|---|---|---|
| 1 | Mobilization | 1 | LS | 3,500.00 | $3,500.00 |
| 2 | Demolish existing fixtures | 24 | EA | 120.00 | $2,880.00 |
| 3 | Underground waste and vent | 260 | LF | 68.00 | $17,680.00 |
| 4 | Domestic water piping | 420 | LF | 38.00 | $15,960.00 |
| 5 | Set fixtures (toilets, lavatories, urinals) | 24 | EA | 1,450.00 | $34,800.00 |
| 6 | Water heater | 1 | LS | 8,900.00 | $8,900.00 |
| 7 | Inspections and closeout | 1 | LS | 12,730.00 | $12,730.00 |
| | **Total** | | | | **$96,450.00** |

### Ortiz Repipe

Whole-house repipe in PEX and a tankless water heater for a homeowner. Starts and finishes in week 1.

| Box | Type this |
|---|---|
| "Job name" | Ortiz Repipe |
| "Street address" / "City" / "State" / "ZIP" | 1186 Clairmont Way / Decatur / GA / 30030 |
| "Client" | Lena Ortiz |
| "Contract amount ($)" | $14,200.00 |
| "Retainage" % | 0 |
| "Start date" | Nov 2, 2026 |
| "Anticipated end date" | Nov 6, 2026 |
| "Assigned to" (once the team has joined) | Dana Whitfield |

| # | "Description" | "Qty" | "Unit" | "Unit rate $" | Line total |
|---|---|---|---|---|---|
| 1 | Repipe in PEX | 1 | LS | 11,400.00 | $11,400.00 |
| 2 | Tankless water heater | 1 | LS | 2,800.00 | $2,800.00 |
| | **Total** | | | | **$14,200.00** |

## Week 1

### Monday, November 2

**New job record: Linden Hollow Office Suite 210** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Linden Hollow Office Suite 210 |
| "Date" | Nov 2, 2026 |
| "Diary" | Mobilized: job box and lift on site. Submittals approved by Ironbridge on Friday. |
| "Crew members" | 3 |
| "Names (optional)" | Terrell, Andre, Luis |
| "Weather" | Clear |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 1. Mobilization and submittals | 100 in "% done to date" |

Tap "Save job record".

**New job record: Ortiz Repipe** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Ortiz Repipe |
| "Date" | Nov 2, 2026 |
| "Diary" | Water off at 8. Cut in the new manifold in the basement and started the first-floor runs. |
| "Crew members" | 2 |
| "Names (optional)" | Kenji, Sam |
| "Weather" | Clear |
| "Materials used or installed" | PEX-A 3/4 in and 1/2 in, manifold, brass fittings |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 1. Repipe in PEX | 30 in "% done to date" |

Tap "Save job record".

### Tuesday, November 3

**New job record: Linden Hollow Office Suite 210** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Linden Hollow Office Suite 210 |
| "Date" | Nov 3, 2026 |
| "Diary" | Hung duct mains on the north side of the second floor. |
| "Crew members" | 4 |
| "Names (optional)" | Terrell, Andre, Luis, Mike |
| "Weather" | Clear |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 2. Galvanized ductwork | 140 LF in "Today" |

Tap "Save job record".

**New job record: Ortiz Repipe** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Ortiz Repipe |
| "Date" | Nov 3, 2026 |
| "Diary" | Second-floor runs done; pressure test held at 100 psi for 30 minutes. |
| "Crew members" | 2 |
| "Names (optional)" | Kenji, Sam |
| "Weather" | Clear |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 1. Repipe in PEX | 70 in "% done to date" |

Tap "Save job record".

### Wednesday, November 4

**New job record: Linden Hollow Office Suite 210** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Linden Hollow Office Suite 210 |
| "Date" | Nov 4, 2026 |
| "Diary" | Duct mains on the south side. |
| "Crew members" | 4 |
| "Names (optional)" | Terrell, Andre, Luis, Mike |
| "Weather" | Cloudy |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 2. Galvanized ductwork | 160 LF in "Today" |

- Tap "+ Delivery ticket". "Supplier": Southern Sheet Metal Supply; "Ticket number": 88213; "What arrived": Duct, fittings and hangers.

Tap "Save job record".

**New job record: Ortiz Repipe** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Ortiz Repipe |
| "Date" | Nov 4, 2026 |
| "Diary" | Repipe finished and patched. Water back on at 3. |
| "Crew members" | 2 |
| "Names (optional)" | Kenji, Sam |
| "Weather" | Cloudy |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 1. Repipe in PEX | 100 in "% done to date" |

- Tap "+ Inspection". "Who inspected": City of Decatur plumbing inspector; "Result": Passed.

Tap "Save job record".

### Thursday, November 5

**New job record: Linden Hollow Office Suite 210** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Linden Hollow Office Suite 210 |
| "Date" | Nov 5, 2026 |
| "Diary" | Rain: the crane pick for the rooftop units moved to Monday. Two men hung branch duct inside. |
| "Crew members" | 2 |
| "Names (optional)" | Terrell, Andre |
| "Weather" | Rain |
| "Weather note (optional)" | Rain until 1 pm |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 2. Galvanized ductwork | 60 LF in "Today" |

Tap "Save job record".

**New change order on Ortiz Repipe** (job page > "New Change Order")

| Box | Type this |
|---|---|
| "Title" | Owner supplies the tankless water heater |
| "Reason" | Owner request |
| "Description" | Mrs. Ortiz bought her own heater. Our price keeps the installation and takes off the heater. |
| "Type" | Credit (deducts) |
| "Pricing" | Lump sum |
| "Amount ($)" | $1,650.00 |

Tap "Create change order".

It came back signed on paper. Tap "Signed on paper…": "Signed by (name)" Lena Ortiz, "Date signed" Nov 5, 2026. Add the signed copy with "Take a photo of the signed copy" (any photo will do), pick it under "The signed copy", and tap "Save as signed".

### Friday, November 6

**New job record: Linden Hollow Office Suite 210** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Linden Hollow Office Suite 210 |
| "Date" | Nov 6, 2026 |
| "Diary" | Branch duct on the north side; first three VAV boxes hung. |
| "Crew members" | 4 |
| "Names (optional)" | Terrell, Andre, Luis, Mike |
| "Weather" | Clear |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 2. Galvanized ductwork | 150 LF in "Today" |
| 4. VAV boxes | 3 EA in "Today" |

Tap "Save job record".

**New change order on Linden Hollow Office Suite 210** (job page > "New Change Order")

| Box | Type this |
|---|---|
| "Title" | Exhaust fan for the server room |
| "Reason" | Owner request |
| "Description" | Tenant added a server room; one inline exhaust fan with its duct and controls. |
| "Type" | Adds to the contract |
| "Pricing" | Lump sum |
| "Amount ($)" | $4,850.00 |

Tap "Create change order".

It came back signed on paper. Tap "Signed on paper…": "Signed by (name)" Paul Kessler, "Date signed" Nov 6, 2026. Add the signed copy with "Take a photo of the signed copy" (any photo will do), pick it under "The signed copy", and tap "Save as signed".

**New job record: Ortiz Repipe** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Ortiz Repipe |
| "Date" | Nov 6, 2026 |
| "Diary" | Installed the owner's tankless heater, vented and tested. Walked Mrs. Ortiz through it. Job complete. |
| "Crew members" | 1 |
| "Names (optional)" | Kenji |
| "Weather" | Clear |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 2. Tankless water heater | 100 in "% done to date" |

Tap "Save job record".

**Final pay app on Ortiz Repipe.** Job page > "Final Pay App". "Period to" Nov 6, 2026. Tap "Draft final pay app".

Supporting documents: for each item under "Missing", upload any file with "Choose file" or "Take a photo", or tap "I have this…" and type a reason. The conditional waiver is always asked for; mark it "Conditional" when asked "Which waiver is this?".

Approvals: "Your full name" Dana Whitfield, "Title" Owner, then "Sign" with the pay app's number. Then "Review & send" > "Approve & send to" `you+ortiz@example.com` > "Send". It is sent today, whatever today is.

> **Checkpoint: Ortiz final pay app.** If a figure is different, stop and report it.
>
> - PA-ORTI-01: this period $12,550.00, retainage $0.00, amount due $12,550.00
> - Ortiz Repipe: contract total $12,550.00, billed $12,550.00, paid $0.00, outstanding $12,550.00, retainage held $0.00

**Payment on Ortiz Repipe.** Job page > "Record a Payment": "Amount received ($)" $12,550.00, "Date received" today, "Check or reference no." Check 1187. Tap "Record".

**Lien waiver on Ortiz Repipe.** Tap "Choose waiver and send". Add a photo of the signed form (any photo), answer "Which waiver is this?" with Unconditional, final payment, tap "Approve & send", then "I opened it and it's right" and "Send the waiver".

**Finish Ortiz Repipe.** Job page > "Finish job". If it asks "Why it can finish without them", type Homeowner job: no closeout documents.

> **Checkpoint: End of week 1.** If a figure is different, stop and report it.
>
> - Ortiz Repipe: contract total $12,550.00, billed $12,550.00, paid $12,550.00, outstanding $0.00, retainage held $0.00, under Finished jobs
> - Linden Hollow Office Suite 210: contract total $153,450.00, billed $0.00, paid $0.00, outstanding $0.00, retainage held $0.00

## Week 2

### Monday, November 9

**New job record: Linden Hollow Office Suite 210** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Linden Hollow Office Suite 210 |
| "Date" | Nov 9, 2026 |
| "Diary" | Crane day: all three rooftop units set on their curbs and connected. |
| "Crew members" | 6 |
| "Names (optional)" | Terrell, Andre, Luis, Mike, Dee, Ray |
| "Weather" | Clear |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 3. Rooftop units, set and connect | 3 EA in "Today" |

- Tap "+ Photos". "What they show": Rooftop units on their curbs. Add any photo with "Take a photo" or "Choose files".

Tap "Save job record".

**New job record: Sorrel Creek Elementary Restrooms** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Sorrel Creek Elementary Restrooms |
| "Date" | Nov 9, 2026 |
| "Diary" | Mobilized. Demolished the fixtures in the boys' restroom. |
| "Crew members" | 3 |
| "Names (optional)" | Kenji, Sam, Jo |
| "Weather" | Clear |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 1. Mobilization | 100 in "% done to date" |
| 2. Demolish existing fixtures | 8 EA in "Today" |

Tap "Save job record".

### Tuesday, November 10

**New change order on Linden Hollow Office Suite 210** (job page > "New Change Order")

| Box | Type this |
|---|---|
| "Title" | Reroute duct around the beam at grid C-4 |
| "Reason" | Unforeseen condition |
| "Description" | A steel beam not on the drawings blocks the main at C-4. Rerouting on time and materials; price to follow. |
| "Type" | Adds to the contract |
| "Pricing" | Time and materials |
| "Amount ($)" | leave it blank: no price yet |

Tap "Create change order".

**New job record: Linden Hollow Office Suite 210** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Linden Hollow Office Suite 210 |
| "Date" | Nov 10, 2026 |
| "Diary" | Found the beam conflict at C-4; started the reroute. Mains on the east side. |
| "Crew members" | 4 |
| "Names (optional)" | Terrell, Andre, Luis, Mike |
| "Weather" | Clear |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 2. Galvanized ductwork | 100 LF in "Today" |

Tick "Change order work":

- "Change order": CO 2 Reroute duct around the beam at grid C-4 (no price yet); "Hours": 6; "Work done": Reroute at C-4: new offsets and hangers

Tap "Save job record".

**New job record: Sorrel Creek Elementary Restrooms** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Sorrel Creek Elementary Restrooms |
| "Date" | Nov 10, 2026 |
| "Diary" | Demolished the girls' restroom fixtures. Saw-cut the slab for the new waste lines. |
| "Crew members" | 3 |
| "Names (optional)" | Kenji, Sam, Jo |
| "Weather" | Clear |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 2. Demolish existing fixtures | 16 EA in "Today" |

Tap "Save job record".

### Wednesday, November 11

**New job record: Linden Hollow Office Suite 210** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Linden Hollow Office Suite 210 |
| "Date" | Nov 11, 2026 |
| "Diary" | Finished the C-4 reroute. Started the server room exhaust fan. |
| "Crew members" | 4 |
| "Names (optional)" | Terrell, Andre, Luis, Mike |
| "Weather" | Clear |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 2. Galvanized ductwork | 120 LF in "Today" |
| CO1: Exhaust fan for the server room | 40 in "% done to date" |

Tick "Change order work":

- "Change order": CO 2 Reroute duct around the beam at grid C-4 (no price yet); "Hours": 8; "Work done": Reroute at C-4 finished

Tap "Save job record".

### Thursday, November 12

**New job record: Linden Hollow Office Suite 210** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Linden Hollow Office Suite 210 |
| "Date" | Nov 12, 2026 |
| "Diary" | Five more VAV boxes. First grilles in the open office. Exhaust fan running. |
| "Crew members" | 4 |
| "Names (optional)" | Terrell, Andre, Luis, Mike |
| "Weather" | Cloudy |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 4. VAV boxes | 5 EA in "Today" |
| 5. Grilles, registers and diffusers | 12 EA in "Today" |
| CO1: Exhaust fan for the server room | 100 in "% done to date" |

Tap "Save job record".

**New job record: Sorrel Creek Elementary Restrooms** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Sorrel Creek Elementary Restrooms |
| "Date" | Nov 12, 2026 |
| "Diary" | Underground waste and vent in the boys' restroom. |
| "Crew members" | 3 |
| "Names (optional)" | Kenji, Sam, Jo |
| "Weather" | Cloudy |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 3. Underground waste and vent | 120 LF in "Today" |

Tap "Save job record".

### Friday, November 13

**New job record: Linden Hollow Office Suite 210** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Linden Hollow Office Suite 210 |
| "Date" | Nov 13, 2026 |
| "Diary" | Duct on the west side. Ten more grilles. Rough-in inspection passed. |
| "Crew members" | 3 |
| "Names (optional)" | Terrell, Andre, Luis |
| "Weather" | Clear |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 2. Galvanized ductwork | 90 LF in "Today" |
| 5. Grilles, registers and diffusers | 10 EA in "Today" |

- Tap "+ Time sheet". "Who or which crew": Terrell's crew, Nov 9-13; "Hours": 152. Add any photo with "Take a photo" or "Choose files".
- Tap "+ Inspection". "Who inspected": City of Atlanta mechanical inspector; "Result": Passed rough-in, second floor.

Tap "Save job record".

**New job record: Sorrel Creek Elementary Restrooms** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Sorrel Creek Elementary Restrooms |
| "Date" | Nov 13, 2026 |
| "Diary" | Underground in the girls' restroom. Inspected and backfilled. |
| "Crew members" | 3 |
| "Names (optional)" | Kenji, Sam, Jo |
| "Weather" | Clear |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 3. Underground waste and vent | 90 LF in "Today" |

- Tap "+ Inspection". "Who inspected": DeKalb County plumbing inspector; "Result": Underground passed.

Tap "Save job record".

**Progress pay app on Linden Hollow Office Suite 210.** Job page > "Progress Pay App". "Period to" Nov 13, 2026. Leave every "Done to date" box as it is: the job records filled them in. Tap "Draft progress pay app".

Supporting documents: for each item under "Missing", upload any file with "Choose file" or "Take a photo", or tap "I have this…" and type a reason. The conditional waiver is always asked for; mark it "Conditional" when asked "Which waiver is this?".

Approvals: "Your full name" Dana Whitfield, "Title" Owner, then "Sign" with the pay app's number. Then "Review & send" > "Approve & send to" `you+ironbridge@example.com` > "Send". It is sent today, whatever today is.

> **Checkpoint: Linden Hollow pay app 1.** If a figure is different, stop and report it.
>
> - PA-LIND-01: this period $107,360.00, retainage $10,736.00, amount due $96,624.00

**Payment on Linden Hollow Office Suite 210.** Job page > "Record a Payment": "Amount received ($)" $90,000.00, "Date received" today, "Check or reference no." ACH 55102. Tap "Record".

**Lien waiver on Linden Hollow Office Suite 210.** Tap "Choose waiver and send". Add a photo of the signed form (any photo), answer "Which waiver is this?" with Unconditional, progress payment, tap "Approve & send", then "I opened it and it's right" and "Send the waiver".

> **Checkpoint: End of week 2.** If a figure is different, stop and report it.
>
> - Linden Hollow Office Suite 210: contract total $153,450.00, billed $96,624.00, paid $90,000.00, outstanding $6,624.00, retainage held $10,736.00
> - Sorrel Creek Elementary Restrooms: contract total $96,450.00, billed $0.00, paid $0.00, outstanding $0.00, retainage held $0.00

## Week 3

### Monday, November 16

**New job record: Linden Hollow Office Suite 210** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Linden Hollow Office Suite 210 |
| "Date" | Nov 16, 2026 |
| "Diary" | Duct on the south side; four more VAV boxes, all twelve in. |
| "Crew members" | 4 |
| "Names (optional)" | Terrell, Andre, Luis, Mike |
| "Weather" | Clear |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 2. Galvanized ductwork | 110 LF in "Today" |
| 4. VAV boxes | 4 EA in "Today" |

Tap "Save job record".

**New job record: Sorrel Creek Elementary Restrooms** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Sorrel Creek Elementary Restrooms |
| "Date" | Nov 16, 2026 |
| "Diary" | Last of the underground. Started the water piping. |
| "Crew members" | 3 |
| "Names (optional)" | Kenji, Sam, Jo |
| "Weather" | Clear |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 3. Underground waste and vent | 50 LF in "Today" |
| 4. Domestic water piping | 140 LF in "Today" |

Tap "Save job record".

### Tuesday, November 17

**New job record: Linden Hollow Office Suite 210** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Linden Hollow Office Suite 210 |
| "Date" | Nov 17, 2026 |
| "Diary" | Grilles and diffusers in the conference rooms. |
| "Crew members" | 4 |
| "Names (optional)" | Terrell, Andre, Luis, Mike |
| "Weather" | Cold |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 5. Grilles, registers and diffusers | 18 EA in "Today" |

Tap "Save job record".

**Price CO 2 on Linden Hollow Office Suite 210.** On the change order, "Type" Adds to the contract, "Price ($)" $2,160.00, then "Save price". (14 hours of labor ($1,540) and material ($620).) Don't get it signed yet: it is still waiting for Ironbridge at the end of the case.

**New job record: Sorrel Creek Elementary Restrooms** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Sorrel Creek Elementary Restrooms |
| "Date" | Nov 17, 2026 |
| "Diary" | Water piping above the ceiling. |
| "Crew members" | 3 |
| "Names (optional)" | Kenji, Sam, Jo |
| "Weather" | Cold |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 4. Domestic water piping | 160 LF in "Today" |

Tap "Save job record".

### Wednesday, November 18

**New job record: Linden Hollow Office Suite 210** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Linden Hollow Office Suite 210 |
| "Date" | Nov 18, 2026 |
| "Diary" | Thermostats and controls wiring, west half. More grilles. |
| "Crew members" | 3 |
| "Names (optional)" | Terrell, Andre, Mike |
| "Weather" | Clear |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 5. Grilles, registers and diffusers | 10 EA in "Today" |
| 6. Controls and thermostats | 40 in "% done to date" |

Tap "Save job record".

**New change order on Sorrel Creek Elementary Restrooms** (job page > "New Change Order")

| Box | Type this |
|---|---|
| "Title" | Hose bibb in the custodian's closet |
| "Reason" | Owner request |
| "Description" | School asked for a hose bibb in the custodian's closet. |
| "Type" | Adds to the contract |
| "Pricing" | Lump sum |
| "Amount ($)" | $780.00 |

Tap "Create change order".

It came back signed on paper. Tap "Signed on paper…": "Signed by (name)" Derrick Owens, "Date signed" Nov 18, 2026. Add the signed copy with "Take a photo of the signed copy" (any photo will do), pick it under "The signed copy", and tap "Save as signed".

**New job record: Sorrel Creek Elementary Restrooms** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Sorrel Creek Elementary Restrooms |
| "Date" | Nov 18, 2026 |
| "Diary" | Water piping to the fixture walls. Hose bibb installed. |
| "Crew members" | 3 |
| "Names (optional)" | Kenji, Sam, Jo |
| "Weather" | Clear |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 4. Domestic water piping | 80 LF in "Today" |
| CO1: Hose bibb in the custodian's closet | 100 in "% done to date" |

Tap "Save job record".

### Thursday, November 19

**New job record: Linden Hollow Office Suite 210** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Linden Hollow Office Suite 210 |
| "Date" | Nov 19, 2026 |
| "Diary" | Fire damper inspection failed: two dampers have no access door. Controls wiring, east half. |
| "Crew members" | 2 |
| "Names (optional)" | Terrell, Andre |
| "Weather" | Rain |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 6. Controls and thermostats | 60 in "% done to date" |

- Tap "+ Inspection". "Who inspected": City of Atlanta mechanical inspector; "Result": Failed: two fire dampers need access doors.

Tap "Save job record".

**New job record: Sorrel Creek Elementary Restrooms** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Sorrel Creek Elementary Restrooms |
| "Date" | Nov 19, 2026 |
| "Diary" | Water heater set; piping it in. |
| "Crew members" | 2 |
| "Names (optional)" | Kenji, Sam |
| "Weather" | Rain |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 6. Water heater | 50 in "% done to date" |

Tap "Save job record".

### Friday, November 20

**New job record: Linden Hollow Office Suite 210** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Linden Hollow Office Suite 210 |
| "Date" | Nov 20, 2026 |
| "Diary" | Access doors in; damper reinspection passed. Duct on the east side. |
| "Crew members" | 4 |
| "Names (optional)" | Terrell, Andre, Luis, Mike |
| "Weather" | Clear |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 2. Galvanized ductwork | 70 LF in "Today" |

- Tap "+ Inspection". "Who inspected": City of Atlanta mechanical inspector; "Result": Passed fire damper reinspection.

Tap "Save job record".

**New job record: Sorrel Creek Elementary Restrooms** (Jobs > "New job record")

| Box | Type this |
|---|---|
| "Job" | Sorrel Creek Elementary Restrooms |
| "Date" | Nov 20, 2026 |
| "Diary" | Water heater fired and tested. Top-out inspection passed. |
| "Crew members" | 3 |
| "Names (optional)" | Kenji, Sam, Jo |
| "Weather" | Clear |

Quantities (the table under the diary):

| Line | Type this |
|---|---|
| 6. Water heater | 100 in "% done to date" |

- Tap "+ Inspection". "Who inspected": DeKalb County plumbing inspector; "Result": Top-out passed.
- Tap "+ Time sheet". "Who or which crew": Kenji's crew, Nov 9-20; "Hours": 236. Add any photo with "Take a photo" or "Choose files".

Tap "Save job record".

**Progress pay app on Linden Hollow Office Suite 210.** Job page > "Progress Pay App". "Period to" Nov 20, 2026. Leave every "Done to date" box as it is: the job records filled them in. Tap "Draft progress pay app".

Supporting documents: for each item under "Missing", upload any file with "Choose file" or "Take a photo", or tap "I have this…" and type a reason. The conditional waiver is always asked for; mark it "Conditional" when asked "Which waiver is this?".

Approvals: "Your full name" Dana Whitfield, "Title" Owner, then "Sign" with the pay app's number. Then "Review & send" > "Approve & send to" `you+ironbridge@example.com` > "Send". It is sent today, whatever today is.

**Progress pay app on Sorrel Creek Elementary Restrooms.** Job page > "Progress Pay App". "Period to" Nov 20, 2026. Leave every "Done to date" box as it is: the job records filled them in. Tap "Draft progress pay app".

Supporting documents: for each item under "Missing", upload any file with "Choose file" or "Take a photo", or tap "I have this…" and type a reason. The conditional waiver is always asked for; mark it "Conditional" when asked "Which waiver is this?".

Approvals: "Your full name" Dana Whitfield, "Title" Owner, then "Sign" with the pay app's number. Then "Review & send" > "Approve & send to" `you+thornwell@example.com` > "Send". It is sent today, whatever today is.

> **Checkpoint: End of week 3.** If a figure is different, stop and report it.
>
> - PA-LIND-02: this period $25,220.00, retainage $2,522.00, amount due $22,698.00
> - PA-SORR-01: this period $48,180.00, retainage $2,409.00, amount due $45,771.00
> - Linden Hollow Office Suite 210: contract total $153,450.00, billed $119,322.00, paid $90,000.00, outstanding $29,322.00, retainage held $13,258.00, 1 change order waiting for signature
> - Sorrel Creek Elementary Restrooms: contract total $97,230.00, billed $45,771.00, paid $0.00, outstanding $45,771.00, retainage held $2,409.00
> - Ortiz Repipe: under Finished jobs

## Where the case ends

- Ortiz Repipe is finished: billed, paid in full, final waiver sent.
- Linden Hollow has two pay apps out. Ironbridge paid $90,000.00 of the first, so $6,624.00 of it is still owed, and CO 2 (the beam reroute, $2,160.00) is waiting for their signature.
- Sorrel Creek Elementary has its first pay app out, nothing paid yet.

Things to try from here, with no figures to check: get CO 2 signed with "Request signature"; record the rest of Ironbridge's payment or accept the short pay; look at Money > Financials and the money strip; open the Dashboard's To Do as Rosa or Terrell.

## What to report

For anything that is different from this guide or doesn't work: the day and step, what you typed, what you expected, what you saw, and a screenshot. Send it to Yusuf.
