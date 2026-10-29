# Wireframes

The live, clickable version is the Duracash wireframes canvas:
https://claude.ai/artifact/9EAKjG3KbsKpqpn5Afxr2J (version 21, Oct 13, 2026; anyone with the link
can open it). Press Play on an artboard to follow its links.

This folder is a copy of the canvas's source, kept here so the wireframes are versioned with the
code. The `.dc.html` files render inside the canvas (they load its `support.js`), not on their
own; `canvas.json` is the layout. When the canvas changes, copy its files here again.

Data on every screen is the demo company (Hartline Remodeling), except the four first-run boards
added Oct 27 (`CheckEmail`, `FirstRun`, `NewJob`, `FirstPacket`), which are drawn with a new company,
Ridgeway Tile & Bath, and its first job. Bracketed values such as `[LICENCE NO.]` are placeholders for a
company's own details.

Seven boards were redrawn from the code on Oct 29 (R6-11), after Round 6: `Job`, `PhoneJob`, `Money`,
`PaymentsImport`, `Inbox`, `Settings` and `PayerPage` (now the customer page). Their labels are the plain
words of the wording glossary (`docs/improvement-plan.md` section 4), checked by
`tests/js/docs.test.mjs`, and a grey panel under each screen shows what opens in place, the other states
and what the other tabs hold. The other boards are as of Oct 13 or Oct 27 and still use some older
words (packet, payer, playbook); the app uses the plain ones everywhere. `canvas.json` has the new
heights, the rows moved down to fit them, and its notes in the plain words.

Not on the live canvas yet: the four first-run boards, the Oct 27 changes to `Jobs` (the New job form)
and `SendCheck` (the email box when a customer has none), and the seven Oct 29 boards. Copy this
folder to the canvas to publish them.

| File | Screen | Size (px) |
|---|---|---|
| `Main.dc.html` | Map: five destinations and the money path | 1440 × 900 |
| `Landing.dc.html` | Landing and sign-up | 1280 × 980 |
| `Today.dc.html` | Today: one action list | 1280 × 1680 |
| `Jobs.dc.html` | Jobs, with the New job form for a saved customer | 1280 × 1450 |
| `Job.dc.html` | Job page: Next step, More, Before you can finish, Money, job record, history; what opens in place | 1280 × 3450 |
| `Packet.dc.html` | Paperwork for a pay app (drawn Oct 13: Packet) | 1280 × 1120 |
| `SendCheck.dc.html` | Pay app: before you send, and the email box when there is none | 1280 × 1900 |
| `Money.dc.html` | Money: pay apps with the Paperwork column; Unpaid and Closeout | 1280 × 1850 |
| `Inbox.dc.html` | Inbox: Waiting first, one tap to add, a payments export, the paste box | 1280 × 2150 |
| `Calendar.dc.html` | Calendar, with customers' pay-app cutoffs | 1280 × 1240 |
| `Settings.dc.html` | Settings: Company, with Team, My account and Billing | 1280 × 2550 |
| `PhoneToday.dc.html` | Phone: Today | 390 × 1300 |
| `PhoneMenu.dc.html` | Phone: menu open | 390 × 844 |
| `PhoneJob.dc.html` | Phone: job page | 390 × 2400 |
| `PhonePacket.dc.html` | Phone: packet | 390 × 1200 |
| `Results.dc.html` | Today: Results | 1280 × 1250 |
| `PaymentsImport.dc.html` | Money: payments, QuickBooks and the payments import (pick, split, already recorded) | 1280 × 1850 |
| `Playbook.dc.html` | Settings: customer settings, learned suggestion, timing, cutoff (drawn Oct 13: Payer playbook) | 1280 × 1290 |
| `History.dc.html` | Jobs: job history, paged | 1280 × 900 |
| `TwoStep.dc.html` | Two-step sign-in | 1400 × 760 |
| `PayerPage.dc.html` | Customer page for one pay app: received, approve for payment, a question | 1280 × 2450 |
| `CoSign.dc.html` | Change order signing page | 1280 × 1000 |
| `States.dc.html` | Confirmations and banners | 1280 × 900 |
| `CheckEmail.dc.html` | Check your email, right after sign-up | 1280 × 1150 |
| `FirstRun.dc.html` | Today: first run, with the setup checklist and "Not billed yet" | 1280 × 1500 |
| `NewJob.dc.html` | New job: five boxes, More details folded | 1280 × 1100 |
| `FirstPacket.dc.html` | First pay app: homeowner paperwork, attached from the job record and not needed | 1280 × 1150 |
