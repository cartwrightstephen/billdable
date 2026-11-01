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
`PaymentsImport`, `Inbox`, `Settings` and `PayerPage` (now the customer page). The final pass after the six
rounds (also Oct 29) redrew six more, `Today`, `PhoneToday`, `Packet`, `PhonePacket`, `SendCheck` and
`Playbook`, with the demo company checked against a throwaway server, and brought the words and tabs of every
other board up to the app (`Main`, `Landing`, `Jobs`, `History`, `Results`, `Calendar`, `TwoStep`, `CoSign`,
`States` and the four first-run boards): Money's six tabs, Settings' seven, and the plain words of the wording
glossary (`docs/improvement-plan.md` section 4) on every board, checked by `tests/js/docs.test.mjs`. A grey
panel under a redrawn screen shows what opens in place, the other states and what the other tabs hold.
`canvas.json` has the new heights and titles.

Not on the live canvas yet: the four first-run boards, the Oct 27 changes to `Jobs` and `SendCheck`, the seven
R6-11 boards and the final pass's changes. Copy this folder to the canvas to publish them.

| File | Screen | Size (px) |
|---|---|---|
| `Main.dc.html` | Map: five destinations and the money path | 1440 × 900 |
| `Landing.dc.html` | Landing and sign-up | 1280 × 980 |
| `Today.dc.html` | Today: To Do three at a time, the metrics and the jobs table | 1280 × 2200 |
| `Jobs.dc.html` | Jobs, with the New job form for a saved customer | 1280 × 1450 |
| `Job.dc.html` | Job page: Next step, More, Before you can finish, Money, job record, history; what opens in place | 1280 × 3450 |
| `Packet.dc.html` | Paperwork for a pay app: Take a photo, Choose file, I have this…, Also blocking send | 1280 × 1400 |
| `SendCheck.dc.html` | Pay app: before you send (period, subject, a blank company address), and the email box when there is none | 1280 × 2200 |
| `Money.dc.html` | Money: pay apps with the Paperwork column; Unpaid and Closeout | 1280 × 1850 |
| `Inbox.dc.html` | Inbox: Waiting first, one tap to add, a payments export, the paste box | 1280 × 2150 |
| `Calendar.dc.html` | Calendar, with customers' pay-app cutoffs | 1280 × 1240 |
| `Settings.dc.html` | Settings: Company, with Team, My account and Billing | 1280 × 2550 |
| `PhoneToday.dc.html` | Phone: Today | 390 × 2200 |
| `PhoneMenu.dc.html` | Phone: menu open | 390 × 844 |
| `PhoneJob.dc.html` | Phone: job page | 390 × 2400 |
| `PhonePacket.dc.html` | Phone: paperwork | 390 × 1150 |
| `Results.dc.html` | Today: Results | 1280 × 1250 |
| `PaymentsImport.dc.html` | Money: payments, QuickBooks and the payments import (pick, split, already recorded) | 1280 × 1850 |
| `Playbook.dc.html` | Settings: customer settings, a suggested paperwork item, timing, Add customer and merge | 1280 × 1600 |
| `History.dc.html` | Jobs: job history, paged | 1280 × 900 |
| `TwoStep.dc.html` | Two-step sign-in | 1400 × 760 |
| `PayerPage.dc.html` | Customer page for one pay app: received, approve for payment, a question | 1280 × 2450 |
| `CoSign.dc.html` | Change order signing page | 1280 × 1000 |
| `States.dc.html` | Confirmations and banners | 1280 × 900 |
| `CheckEmail.dc.html` | Check your email, right after sign-up | 1280 × 1150 |
| `FirstRun.dc.html` | Today: first run, with the setup checklist and "Not billed yet" | 1280 × 1500 |
| `NewJob.dc.html` | New job: five boxes, More details folded | 1280 × 1100 |
| `FirstPacket.dc.html` | First pay app: homeowner paperwork, attached from the job record and not needed | 1280 × 1150 |
