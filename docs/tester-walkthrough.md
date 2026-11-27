# Tester walkthrough: one billing cycle, from Create account to the lien waiver

You play the owner of a small electrical subcontractor in Atlanta. You create the account, set up
the company, add your general contractor client, start a job with a schedule of values, record
two days of work, bill it with a Progress Pay App, get paid, and send the lien waiver.

It takes about 30 minutes. Type the sample data exactly as written, and check the figures at each
**Checkpoint**. If a figure is different, stop and report it (see "What to report" at the end).

Everything here is made up: the companies, the people, the phone numbers (555-01xx) and the
job. Only your email address is real, so the emails come to you.

The browser test `tests/e2e/steps/88-walkthrough.mjs` follows these steps with the same data and
checks the same figures. If the app changes and this page no longer matches, that test fails.

## Before you start: email

Duracash sends three kinds of email in this walkthrough:

| Email | When | Goes to |
|---|---|---|
| "Confirm your email for Duracash" (a link) | Right after Create account | Your sign-up address |
| The pay app, then the lien waiver | When you send them | The client's "Email for pay apps" |
| A 6-digit sign-in code | Only when you sign in again from a new browser | Your sign-up address |

You use your own inbox for all of it, with plus-addressing: add `+tester` or `+gc` before the
`@`. Gmail, Google Workspace, Outlook and iCloud deliver `you+tester@yourdomain.com` to
`you@yourdomain.com`. Below, the samples use `alex@example.com`; put your own address in its place.

- You (sign-up): `alex+tester@example.com`
- The client's billing contact: `alex+gc@example.com`

**On staging, email goes only to addresses on the allow-list.** The list matches whole
addresses, so `alex+tester@example.com` and `alex+gc@example.com` each need to be on it (or your
whole domain). Send both addresses to Yusuf before you start. Without that:

- the confirmation link never comes, and Duracash won't send a pay app until your email is
  confirmed ("Confirm your email first so the pay app goes out from you");
- a pay app or waiver to an address not on the list is marked sent, but the email doesn't go
  out, and the Dashboard lists it under "Didn't go out".

If the server has no email set up at all, the yellow line at the top says "email isn't set up on
this server yet". Then nothing needs confirming, and each send says "The email ... was not sent:
email delivery is not set up". Carry on; the figures are the same.

You also need three small files on your computer or phone: two photos (any photos will do) and
one more photo or PDF. They stand in for the signed change order, the signed waivers and the time
sheet.

## Sample data

**Your company**

| Field | Type this |
|---|---|
| Company name | Peachtree Current Electric |
| Phone | (404) 555-0142 |
| Street address | 1450 Marietta Blvd NW, Suite 210 |
| City / State / ZIP code | Atlanta / GA / 30318 |
| Contractor licence number | EN-218734 |
| Legal name (on contracts and lien waivers) | Peachtree Current Electric, LLC |

**Your client (the general contractor)**

| Field | Type this |
|---|---|
| Client name | Brightline Builders Group |
| Client type | General contractor |
| Who signs off on your bills | Dana Whitfield |
| Email for pay apps | `alex+gc@example.com` (your address with `+gc`) |
| Phone | (404) 555-0187 |
| Payment terms, days | 30 |
| Supporting documents needed with each pay app | tick **Progress photos** |

**The job**

| Field | Type this |
|---|---|
| Job name | Peachtree Commons tenant build-out |
| Street address | 2150 Peachtree Commons Dr NW |
| City / State / ZIP | Atlanta / GA / 30318 |
| Contract amount ($) | 82,000 |
| Retainage | 10 % (it fills in by itself when you pick the client) |
| Contract number | BBG-2611-E |

**Schedule of values** (the five lines add up to the $82,000 contract)

| # | Description | Qty | Unit | Unit rate $ | Scheduled value |
|---|---|---|---|---|---|
| 1 | Temporary power | 1 | LS | 4500 | $4,500.00 |
| 2 | Branch conduit and wire | 2400 | LF | 12.50 | $30,000.00 |
| 3 | Light fixtures, set and wired | 120 | EA | 175 | $21,000.00 |
| 4 | Panelboards | 4 | EA | 3250 | $13,000.00 |
| 5 | Receptacles and switches | 300 | EA | 45 | $13,500.00 |
| | **Total** | | | | **$82,000.00** |

**Change order 1**: "Four EV charger circuits in the garage", Owner request, $6,400, signed on
paper by Dana Whitfield. It brings the contract total to $88,400.00.

**The work** (two job records)

| Line | Job record 1 | Job record 2 | Done to date | Earned |
|---|---|---|---|---|
| Temporary power | 100 % | | 100% | $4,500.00 |
| Branch conduit and wire | 700 LF | 500 LF | 1,200 LF | $15,000.00 |
| Light fixtures, set and wired | | 40 EA | 40 EA | $7,000.00 |
| Panelboards | 2 EA | | 2 EA | $6,500.00 |
| Receptacles and switches | | | 0 | $0.00 |
| CO 1, EV charger circuits | | 50 % | 50% | $3,200.00 |
| **Total** | | | | **$36,200.00** |

Retainage 10% holds back $3,620.00, so the pay app asks for **$32,580.00**.

## Step 1. Create your account

1. Open the staging address you were given. You land on **Create your company**. (If it shows
   **Sign in**, tap **Create one** at the bottom of the box.)
2. Fill in:
   - **Your name**: your own first and last name
   - **Company name**: `Peachtree Current Electric`
   - **Email**: `alex+tester@example.com` (your address with `+tester`)
   - **Password**: 10 characters or more, and not a common one (`password123` and the like are refused)
3. Tick **I agree to the Terms of Service and the Privacy Policy.** and tap **Create account**.
4. The page says "Check your email: we sent a link to …". On a server that sends email it says
   this whether or not the address already has an account, so nobody can use the form to find
   out who is registered. Open your inbox, find "Confirm your email for Duracash" and open its
   link: you are signed in, on the **Dashboard**, with a card called **Get your first pay app
   out**, "0 of 5 steps done". No email after a few minutes? Check that your address is on the
   allow-list, then create the account again (an address that already has one gets "You already
   have a Duracash account" instead, with how to sign in).
5. On a server without email, Create account signs you in at once and the yellow "Email not
   confirmed" line stays until a link can be sent.

## Step 2. Company details

1. On the Dashboard card, tap **Company details** (or open **Settings**, then **Company**).
2. Fill in the **Company details** from the sample data: Phone, Street address, City, State, ZIP
   code, Contractor licence number, and Legal name (on contracts and lien waivers). Leave
   **Company name** as it is.
3. Tap **Save** at the bottom of the page. "Saved" shows briefly.
4. Leave **Pay app approvers** alone for now. With nobody ticked, the owners (you) approve and sign
   each pay app before it goes.

**Checkpoint:** back on the **Dashboard**, the card reads "1 of 5 steps done" and the company
step has a tick.

## Step 3. Add the client

1. Open **Jobs**, then the **Client playbook** tab, and tap **Add client**.
2. Fill in **Client name**, **Client type** (General contractor), **Who signs off on your
   bills**, **Email for pay apps** (your address with `+gc`), **Phone** and **Payment terms,
   days** from the sample data. Leave **Pay apps due by (day of the month)** blank.
3. Under **Supporting documents needed with each pay app**, tick **Progress photos**. Leave the
   rest unticked. (The conditional waiver is asked for with every pay app anyway.)
4. Tap **Add client** under the form.

**Checkpoint:** the playbook lists "Brightline Builders Group" with "General contractor · Dana
Whitfield · alex+gc@example.com · 0 jobs".

## Step 4. New job with its schedule of values

1. Open the **Open jobs** tab and tap **New job**.
2. Fill in **Job name**, **Street address**, **City**, **State**, **ZIP** and **Contract amount
   ($)** from the sample data.
3. Tap **Existing client**, then pick **Brightline Builders Group** under **Client**. A line
   below says "General contractor. Pay apps go to Dana Whitfield · alex+gc@example.com · (404)
   555-0187."

   **Checkpoint:** **Retainage** now reads 10.
4. Leave **Start date** as today. Type the **Contract number**.
5. Open **Schedule of values** (below the notes). It has three empty lines; tap **Add line**
   twice to get five. Type each line: Description, Qty, Unit, Unit rate $.
6. Tap **Save job**. The job page opens.

**Checkpoint:** on the job page, the **Money** card shows Contract **$82,000.00** and Contract
total **$82,000.00**, and **Retainage** 10%. The **Schedule of values** card lists the five lines
and a Total of **$82,000.00**.

## Step 5. The change order

1. On the job page, tap **New Change Order**.
2. **Title**: `Four EV charger circuits in the garage`. **Reason**: Owner request. **Amount ($)**:
   `6,400`. Leave the rest blank.
3. Tap **Create change order**. It shows as "CO 1 ... $6,400.00 Unsigned" under **Change orders**.
4. The client signed it on paper. Under CO 1, tap **Signed on paper…**:
   - **Signed by (name)**: `Dana Whitfield`
   - **Date signed**: today
   - Tap **Take a photo of the signed copy** and pick one of your files. "1 file added to the job
     records."
   - **The signed copy** now names that file. Tap **Save as signed**.

**Checkpoint:** CO 1 reads **Signed**. The **Money** card shows Signed change orders
**$6,400.00** and Contract total **$88,400.00**. The schedule of values has a sixth line, "CO1",
for $6,400.00, and its Total is **$88,400.00**.

## Step 6. Two job records with quantities

**Job record 1**

1. On the job page, tap **New job record**. The job is already picked.
2. **Diary**: `Temporary power set. Ran conduit on level 1. Set panels LP-1 and LP-2.`
   Under **Time sheet**, **Crew member**: **Someone else (type the name)**, **Name**: `Level 1
   crew`, **Hours**: `32`.
3. Under **Work done**, in **Today, or % done to date**, type `100` for Temporary power (a %),
   `700` for Branch conduit and wire and `2` for Panelboards. Leave the others blank.
4. Tap **Save job record**.

**Checkpoint:** the bar at the top reads **Done, not billed $19,750**.

**Job record 2**

1. Same page (or **New job record** again). **Diary**: `Finished level 1 conduit. Hung 40
   fixtures. Roughed in the EV circuits.`
2. Under **Work done**: `500` for Branch conduit and wire, `40` for Light fixtures, set and
   wired, `50` for CO 1 (a %).
3. Tap **+ Photos**, then **Choose files** (or **Take a photo**), and pick a photo.
4. Under **Time sheet**: **Crew member** **Someone else (type the name)**, **Name** `Level 1
   crew`, **Hours** `40`. Tap its **Choose files** and pick a file (a photo of the paper sheet).
5. Tap **Save job record**. "Saved to Peachtree Commons tenant build-out: 1 diary entry, 1
   progress photo, 1 time sheet."

**Checkpoint:** open the job (**Open jobs**, then the job's name). The **Money** card shows Done,
not billed **$36,200.00**. Under **This billing period**: Suggested pay app value **$36,200.00**,
Less retainage 10% **-$3,620.00**, Suggested amount due **$32,580.00**.

## Step 7. Progress Pay App

1. On the job page, tap **Progress Pay App**. Its own page opens, with the job's pay period, and a
   table with **Done to date** already filled in from your job records: 100, 1200, 40, 2, 0 and 50.
2. Leave **Period to** as it is (the pay period's end). Tap **Draft progress pay app**. The draft's
   own page opens; tap the job's name to go back to the job.

**Checkpoint:** the job page's **Pay apps** card lists **PA-PEAC-01 Draft $32,580.00**, with "2
supporting documents missing" (progress photos are a check, not a hard stop). Under **This billing period**, "On PA-PEAC-01" is **$36,200.00**,
less retainage **-$3,620.00**, Amount due **$32,580.00**.

## Step 8. The supporting documents it asks for

1. Above the job's buttons, tap **Add or check 2 documents**. The pay app's page opens, with
   **Missing (2)**, the hard stops:
   - **Conditional waiver on progress payment**
   - **Daily tickets and time sheets**

   **Progress photos** is a check: it is under **Check before it goes** and in **Supporting
   documents**, where its photo waits for a look.
2. **Conditional waiver**: tap **Choose file** (or **Take a photo**) on it and pick a file (it
   stands in for the waiver Dana's office would want signed). Duracash can't read a photo, so it
   asks "Which waiver is this?". Tap **Conditional**.
3. **Progress photos**: Duracash found your photo in the job records. Tap **I checked this file**.
4. **Daily tickets and time sheets**: Duracash asks for these because the pay app bills
   quantities ("For Branch conduit and wire, ..."), and it found the time sheet you saved with job
   record 2. Tap **I checked this file**.

**Checkpoint:** the line under **PA-PEAC-01** reads **Waiting for signature** (your signature, in step 9) and **$32,580.00**. Each of
the three documents has a ✓.

Further down, **The email** shows what Dana will get: "Less retainage 10%: -$3,620.00" and
"Amount due this application: $32,580.00", and a continuation sheet whose Retainage column reads
450.00, 1,500.00, 700.00, 650.00, 0.00 and 320.00. Tap **Open the PDF** and check it shows the same.

## Step 9. Sign, Review & send

1. Look at **Review & send** on the pay app's page. **To** reads "Dana Whitfield ·
   alex+gc@example.com". **Amount** is $32,580.00. **Payment due** is 30 days after it goes
   out. A note says it's the first pay app to this client: check the email address.
2. Sign the pay app. Every pay app needs approval before it can go, and with no approvers named
   the company's owners approve, so that is you. Under **Review & send** an approvals block shows
   your name as waiting. Type your full name in **Your full name** (a **Title** is optional) and
   tap **Sign PA-PEAC-01**. Your name then shows as signed, and the PDF gets an "Approved by" block
   with your name and the date.
3. Tap **Approve & send to alex+gc@example.com**. A box asks "Email PA-PEAC-01 for $32,580.00
   ... to Dana Whitfield"; tap **Send**.

**Checkpoint:** the pay app reads **Sent**. On the job page, the **Money** card shows Billed
**$32,580.00**, Received **$0.00**, Outstanding **$32,580.00**, Held back, still owed
**$3,620.00**.

The pay app email arrives at your `+gc` address, with the PDF and a private link. Open the link
the way the client would.

## Step 10. Record the payment

Brightline pays in full, less the retainage.

1. On the job page, tap **Record a Payment**. Under **Pay app**: "PA-PEAC-01 · $32,580.00 ·
   Sent".
2. **Amount received ($)** is already $32,580.00 and **Date received** today. **Check or reference
   no.**: `Check 10442`.
3. Tap **Record**.

**Checkpoint:** the pay app reads **Paid**. The **Money** card shows Received **$32,580.00**,
Outstanding **$0.00**, and Held back, still owed **$3,620.00** (the retainage, still owed to you).

## Step 11. Send the lien waiver

You have the money, so Brightline is owed an unconditional waiver for this payment.

1. On the job page, tap **Choose waiver and send**.
2. Under **Waivers due**: "PA-PEAC-01 · $32,580.00 · Due", with **Unconditional, progress
   payment** picked. Under it, **Check the signed form shows** lists Amount covered **$32,580.00**
   and Not released: the **$3,620.00** retainage.
3. Tap **Choose file** (or **Take a photo**) and pick a file (it stands in for the signed
   unconditional waiver).
4. Tap **Approve & send**. Duracash can't read a photo, so it asks you to look at it: tap **I
   opened it and it's right**. Then it asks "Email the signed unconditional waiver for
   PA-PEAC-01, $32,580.00 to Dana Whitfield"; tap **Send the waiver**.

**Checkpoint:** under **By pay app**, PA-PEAC-01 reads "Conditional, progress payment: Sent" and
"Unconditional, progress payment: Sent". The waiver email arrives at your `+gc` address.

## Where you end up

| Where | Figure |
|---|---|
| Contract total | $88,400.00 |
| Pay app PA-PEAC-01 | $36,200.00 of work, less $3,620.00 retainage = $32,580.00 |
| Received | $32,580.00 |
| Outstanding | $0.00 |
| Held back, still owed | $3,620.00 |
| Backlog (top bar) | $52,200 |

## What to report

Send one note per problem to Yusuf, with:

- **The step and checkpoint**, for example "Step 7, checkpoint".
- **What you expected and what you saw**, with the figure or the words on screen.
- **A screenshot** of the whole page.
- **Your browser and device**, for example "Chrome on a Windows laptop" or "Safari on iPhone".
- **The time**, so it can be found in the server's logs.

Report also anything that confused you, even when the figures were right: a button you couldn't
find, words you didn't understand, an email that never came or looked wrong. Note the emails too:
did the confirmation link, the pay app and the waiver each arrive, and how long did they take?
