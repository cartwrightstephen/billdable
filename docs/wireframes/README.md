# Wireframes

The live, clickable version is the Duracash wireframes canvas:
https://claude.ai/artifact/9EAKjG3KbsKpqpn5Afxr2J (version 21, Oct 13, 2026; anyone with the link
can open it). Press Play on an artboard to follow its links.

This folder is a copy of the canvas's source, kept here so the wireframes are versioned with the
code. The `.dc.html` files render inside the canvas (they load its `support.js`), not on their
own; `canvas.json` is the layout. When the canvas changes, copy its files here again.

Data on every screen is the demo company (Hartline Remodeling), except the four first-run boards
added Oct 27 (`CheckEmail`, `FirstRun`, `NewJob`, `FirstPacket`), which are drawn with a new company,
Ridgeway Tile & Bath, and its first job. Those four, and the Oct 27 changes to `Jobs` (the New job
form) and `SendCheck` (the email box when a customer has none), were drawn here from the code and
are not on the live canvas yet. Bracketed values such as `[LICENCE NO.]` are placeholders for a
company's own details.

| File | Screen | Size (px) |
|---|---|---|
| `Main.dc.html` | Map: five destinations and the money path | 1440 × 900 |
| `Landing.dc.html` | Landing and sign-up | 1280 × 980 |
| `Today.dc.html` | Today: one action list | 1280 × 1680 |
| `Jobs.dc.html` | Jobs, with the New job form for a saved customer | 1280 × 1450 |
| `Job.dc.html` | Job page, with its history | 1280 × 2150 |
| `Packet.dc.html` | Packet | 1280 × 1120 |
| `SendCheck.dc.html` | Pay app: before you send, and the email box when there is none | 1280 × 1900 |
| `Money.dc.html` | Money: pay apps | 1280 × 1000 |
| `Inbox.dc.html` | Inbox | 1280 × 1300 |
| `Calendar.dc.html` | Calendar, with payers' pay-app cutoffs | 1280 × 1240 |
| `Settings.dc.html` | Settings: you, company, team, billing | 1280 × 1920 |
| `PhoneToday.dc.html` | Phone: Today | 390 × 1300 |
| `PhoneMenu.dc.html` | Phone: menu open | 390 × 844 |
| `PhoneJob.dc.html` | Phone: job page | 390 × 1700 |
| `PhonePacket.dc.html` | Phone: packet | 390 × 1200 |
| `Results.dc.html` | Today: Results | 1280 × 1250 |
| `PaymentsImport.dc.html` | Money: payments, QuickBooks connection and CSV import | 1280 × 1650 |
| `Playbook.dc.html` | Settings: payer playbook, learned suggestion, timing, cutoff | 1280 × 1290 |
| `History.dc.html` | Jobs: job history, paged | 1280 × 900 |
| `TwoStep.dc.html` | Two-step sign-in | 1400 × 760 |
| `PayerPage.dc.html` | Payer page for one pay app | 1280 × 1200 |
| `CoSign.dc.html` | Change order signing page | 1280 × 1000 |
| `States.dc.html` | Confirmations and banners | 1280 × 900 |
| `CheckEmail.dc.html` | Check your email, right after sign-up | 1280 × 1150 |
| `FirstRun.dc.html` | Today: first run, with the setup checklist and "Not billed yet" | 1280 × 1500 |
| `NewJob.dc.html` | New job: five boxes, More details folded | 1280 × 1100 |
| `FirstPacket.dc.html` | First pay app: homeowner paperwork, attached from the job record and not needed | 1280 × 1150 |
