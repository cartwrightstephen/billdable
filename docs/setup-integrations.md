# Setting up email, texts, QuickBooks and DocuSign

For whoever holds the accounts. Everything below goes in the Render dashboard, under the service's Environment page. Names only here; values never go in the repository. `render.yaml` lists the staging names with `sync: false`.

Try everything on `duracash-staging` first. Staging has `DURACASH_ENV=staging`, so it sends mail and texts only to the people on its allow-lists, and to nobody while a list is blank.

## Email

**Settings**

| Name | Where | What it is |
|---|---|---|
| `SMTP_HOST` | staging, production | The mail provider's server. Without it nothing is sent, and the Outbox says so. |
| `SMTP_PORT` | if not 587 | 465 goes with `SMTP_SECURITY=ssl`. |
| `SMTP_SECURITY` | if not starttls | `starttls` or `ssl`. |
| `SMTP_USER`, `SMTP_PASSWORD` | staging, production | The provider's login. |
| `MAIL_FROM` | staging, production | The sender, on a domain the provider has verified. |
| `DURACASH_URL` | staging, production | The site's `https://` address, used for links in emails. |
| `MAIL_ALLOWLIST` | staging only | Addresses and `@domains`, separated by commas. Leave it out on production. |
| `MAIL_DAILY_LIMIT` | optional | Emails per company per 24 hours. Default 50. |
| `MAIL_EVENTS_TOKEN` | optional | A random secret of 16 or more characters, for bounce reports. |
| `MAIL_EVENTS_PASSWORD` | optional, recommended | A second secret: the bounce webhook then needs HTTP Basic with it. |
| `INBOUND_WEBHOOK_PASSWORD` or `INBOUND_SIGNING_KEY` | production, if inbound email is used | Production refuses inbound email without one of them (the address alone is not enough). |

**To register with the mail provider**

- Verify the `MAIL_FROM` domain (SPF and DKIM).
- Bounce webhook: `https://<site>/api/mail-events/<MAIL_EVENTS_TOKEN>`, or with `MAIL_EVENTS_PASSWORD`
  set, `https://duracash:<MAIL_EVENTS_PASSWORD>@<site>/api/mail-events/<MAIL_EVENTS_TOKEN>`
  (Postmark and SendGrid both take a user name and password in the webhook address).
- Inbound email, if used: an owner copies the address from Communications, Inbox.

**Who to list on staging.** List everyone who signs in to test, as well as anyone who should receive test mail. A person off the list gets no confirmation link, so their own emails stay unsent until they are listed. Outside production they sign in without an emailed code, and they can't get the code that changes a password.

**Live test**

1. On staging, set the settings above, with `MAIL_ALLOWLIST` naming only your own test addresses.
2. Sign up with a listed address. The confirmation email should arrive. Confirm it.
3. Make a client whose billing email is a second listed address. Send a pay app. Check it arrives, with the PDF attached, and that Reply-To is you.
4. Make a client whose billing email is not listed. Send a pay app. Nothing should arrive, and the Outbox should say "not sent: this address isn't on this server's allow-list".
5. Add that address to the list, then press Send again in the Outbox. It should arrive.
6. Check that the message isn't in spam, and that its headers show SPF and DKIM passing.
7. Send to an address that doesn't exist. The Outbox row should turn "bounced" if the bounce webhook is set.

## Texts (Twilio)

Texts are internal only. They go to a company's own team members who added a phone and agreed on My account. Clients are never texted.

**Confirming a number.** Ticking the agreement on My account texts a 6-digit code to the number. Texts turn on only when that code is entered there. The code lasts 15 minutes, 5 wrong tries drop it, and each person and each number can be sent 5 codes an hour. Codes go through the same checks as any text: never to a client's number, only to `SMS_ALLOWLIST` numbers where a list is in force, and counted toward `SMS_DAILY_LIMIT`. With Twilio not set up, nobody can confirm a number, so nobody can turn texts on. Anyone who agreed before codes existed has to confirm once.

A client's number is matched on its 10 digits, so an extension or a different format changes nothing. It is checked when a person saves their number, when a code is sent and confirmed, and at every send, so a client added later stops the texts.

**Settings**

| Name | Where | What it is |
|---|---|---|
| `TWILIO_ACCOUNT_SID` | staging, production | From the Twilio console. |
| `TWILIO_AUTH_TOKEN` | staging, production | Checks that inbound texts really came from Twilio, and logs in to send. Production and staging refuse inbound texts without it. Only local development, on the same machine, takes them unsigned. |
| `TWILIO_NUMBER` | staging, production | The Duracash number, as `+1...`. |
| `SMS_ALLOWLIST` | staging only | Phone numbers, separated by commas. With a blank list on staging, or with `MAIL_ALLOWLIST` set and this blank, no text goes out. |
| `SMS_DAILY_LIMIT` | optional | Texts per company per 24 hours. Default 20. |
| `REMINDER_REPEAT_DAYS` | optional | How often the same reminder comes round. Default 3. |

**To register at Twilio**

- Phone number, Messaging, "A message comes in": Webhook, HTTP POST, the address an owner sees in Communications, Inbox: `https://<site>/api/inbound/sms/<token>`.
- Paste it exactly. The signature covers the whole address, so it must start with the same address as `DURACASH_URL`. If `DURACASH_URL` is a custom domain and Twilio has the `onrender.com` address, every text is refused with 403.
- Leave Twilio's default opt-out handling on. It answers STOP, START and HELP itself.

**One number, one company.** A Twilio number has one webhook, and each company has its own address, so one number files inbound texts to one company. Outgoing reminders can share a number across companies. Serving more companies from inbound texts needs either a number per company or routing by sender, which is not built.

**STOP.** A one-word STOP (or STOPALL, UNSUBSCRIBE, CANCEL, END, QUIT, REVOKE, OPTOUT) turns texts off in Duracash for every account with that number, and doesn't land in the Inbox. Texting START lifts Twilio's block but doesn't turn reminders back on; the person ticks the box on My account as well.

**US carrier registration (A2P 10DLC).** Do this before relying on texts. Twilio says any application-sent text to US numbers over a 10-digit number needs it, internal messages included. Unregistered traffic gets extra carrier fees and filtering, and may not be delivered. Allow time: this review is not instant.

- [ ] Pick the brand type. With an EIN: Standard, or Low-Volume Standard under 6,000 segments a day. Sole Proprietor is only for a sender with no tax ID, and an LLC doesn't qualify.
- [ ] Have the exact legal name as on the IRS CP 575 or 147C letter, and the EIN. A new EIN can take 30 to 90 days to reach the registry.
- [ ] Have a working public website that isn't behind a login, a business contact email, and an authorized representative's name, title, phone and email.
- [ ] Register the campaign. Use a use case that fits internal work notifications (Low-Volume Mixed is the likely one; confirm in the console). Description: "Duracash sends work reminders to employees of its customer companies who opted in: important dates and missing information on their jobs."
- [ ] Sample messages, 2 to 5, each naming the brand. For example: "Duracash: 2 reminders. Oak Street: Change order Extra outlets has work on a job record and needs a signature. See Automations for the rest. Reply STOP to opt out."
- [ ] Opt-in description: the person signs in, opens Settings, My account, enters their mobile number and ticks the box. Quote the box's wording from that page.
- [ ] Publish a privacy policy and terms at public URLs. The privacy policy must say mobile numbers aren't shared, give the message frequency and include "Message and data rates may apply." **The current `legal/` pages need checking and likely adding to for this; that wording is for Stephen or counsel.**
- [ ] Add the Twilio number to the campaign's Messaging Service.
- [ ] Check current fees on Twilio's A2P pricing page.

**Live test**

1. On staging, set the settings, with `SMS_ALLOWLIST` naming only your own phone.
2. From your phone, text the number with a photo and a PDF. On Communications, Inbox, the item should appear with both files, from your number.
3. On My account, add that phone. Text again. The item should now say your name.
4. As an owner, or with the permission to edit company details, file the item to a job with a kind. Check it on the job's record, and open both files.
5. Change one character of the auth token in Render, and text again. Twilio's log should show 403, and nothing should land in the Inbox. Put it back.
6. On My account, tick the agreement. On Automations, tick Text on a reminder. The next text goes at or after `DAILY_EMAIL_HOUR_UTC`, if something is due.
7. Reply STOP. My account should show texts are off, and no more texts should arrive.
8. Reply START, tick the box again, and check the next one arrives.
9. In the Twilio log, check the message status is "delivered", not filtered with error 30034 (unregistered) or similar.

On staging and production, also check that with `TWILIO_AUTH_TOKEN` unset, an inbound text gets 403.

**If a counting file is damaged.** `sms_sends.json` holds the texts per company and `reminder_jobs.json` what each person was last reminded of, both in the data directory. If either can't be read, no text (or no reminder) goes out, an alert fires and a copy is kept beside it as `<name>.bad-<number>`. The file stays until someone fixes or removes it. Removing `sms_sends.json` restarts the daily count. Removing `reminder_jobs.json` restarts the reminders and clears the in-app lists, so anything due goes out once more. Companies that are read-only get no reminders or texts.

**Staging has no disk.** `duracash-staging` has no persistent disk, so `reminder_jobs.json` and `sms_sends.json` reset on every deploy. People on the allow-lists can get a reminder again after a deploy, even within its interval, and the daily count starts over. Nothing is built for this; production has a disk.

**After a deploy or a restart.** Nothing is sent at start-up. The first scheduled run is about 5 minutes later, then every hour. Each day's emails go once, even across restarts, because what is done for the day is kept in the data directory.

## QuickBooks Online

Duracash sends sent pay apps to QuickBooks as invoices, and brings back the payments recorded there against them (`qbo_sync.py`). It also still reads payments to propose matches, checks pay apps against QuickBooks invoices, and offers the invoice CSV (`qbo.py`, `qbo_csv.py`).

**Settings (Render, Environment)**

| Name | Where | What it is |
|---|---|---|
| `QBO_CLIENT_ID`, `QBO_CLIENT_SECRET` | staging, production | From the app's Keys and credentials page at Intuit. Development keys work only with sandbox companies, production keys only with real ones. |
| `QBO_ENVIRONMENT` | staging: `sandbox`; production: `sandbox` until the production keys arrive, then `production` | Must match the keys. Unset, it is `production` on a server with `DURACASH_ENV=production` and `sandbox` anywhere else, so **set it to `sandbox` on production while that server still has development keys**. `QBO_ENV`, its earlier name, is still read. |
| `DURACASH_URL` | staging, production | Required: it makes the redirect URI (`<DURACASH_URL>/api/qbo/callback`). |
| `QBO_API_BASE`, `QBO_TOKEN_URL`, `QBO_REVOKE_URL` | nowhere | Only for tests, which point them at a local stand-in. |

The daily payment sync runs from the same hourly loop as the daily email, about 5 minutes after start-up and then hourly, once a day per company.

**Set up the app at Intuit (once)**

1. Sign in at [developer.intuit.com](https://developer.intuit.com) with the Intuit account that will own the app (use a company address, not a personal one).
2. Dashboard, **Create an app**, choose **QuickBooks Online and Payments**. Name it Duracash.
3. Scope: tick **Accounting** only (`com.intuit.quickbooks.accounting`). Duracash doesn't use Payments, OpenID or profile scopes.
4. Open the app, **Keys and credentials**, the **Development** section. Copy the Client ID and Client Secret into staging's `QBO_CLIENT_ID` and `QBO_CLIENT_SECRET`.
5. In the same section, **Redirect URIs**, add exactly `https://<staging site>/api/qbo/callback` (for example `https://duracash-staging.onrender.com/api/qbo/callback`). No trailing slash. Save.
6. Intuit makes a sandbox company with the developer account. Find it under **Sandbox** on the dashboard; add a second one there if you want a clean company for tests. It is US English, with sample customers and items.
7. In the sandbox company (open it from the Sandbox page), set up the two items Duracash bills on:
   - Chart of accounts, **New**: type **Other Current Assets**, name **Retainage Receivable**.
   - Products and services, **New**, **Service**: name **Construction billing**, income account your construction income account (for example Construction Income or Services).
   - Products and services, **New**, **Service**: name **Retainage**, income account **Retainage Receivable** (the asset account above, not income).
   - If you number invoices yourself (Account and settings, Sales, **Custom transaction numbers** on), the QuickBooks invoice number is the pay app's number, such as `PA-OAK-02`.
8. For the live site, when ready: the **Production** section of Keys and credentials asks for Intuit's app assessment (a questionnaire), the app's host domain, launch URL (`https://<site>/`), disconnect URL (`https://<site>/`), and public privacy policy and terms URLs (`https://<site>/privacy`, `https://<site>/terms`). Allow a few days. Then add `https://<site>/api/qbo/callback` under Production's Redirect URIs, put the production keys on the production service, and set `QBO_ENVIRONMENT=production`.

**In Duracash, per company**

1. An owner (Edit company details, client settings and connections) presses **Connect QuickBooks** on Settings, Company or Data, Import, and approves at Intuit.
2. Someone with both that permission and **See company financials** presses **Choose items**: the billing item (Construction billing) and the retainage item (Retainage). A pay app holding retainage can't be sent until a retainage item is chosen.
3. Optional: **Link clients** pairs each client with its QuickBooks customer. Clients left unlinked are matched when their first pay app is sent: by the same name, then the same email, and a new QuickBooks customer is made when neither is found.
4. On a sent pay app, someone with **See company financials** presses **Send to QuickBooks**. Sending it again updates the same QuickBooks invoice. If the invoice was changed in QuickBooks since, Duracash asks before replacing it.
5. Payments recorded in QuickBooks against those invoices are recorded here by **Sync now** (anyone with Record payments) and once a day on their own, through the same Record payment path and audit trail.

**How a pay app goes into QuickBooks**

- One QuickBooks invoice per pay app, numbered like it, dated the day it was sent, due when it is due, with a memo naming the job.
- One invoice line per pay app line: the schedule-of-values progress this period, overhead and profit, and unsigned change orders billed with it, all on the billing item. Credit change orders have already lowered these lines, so nothing is subtracted separately. Mirroring the lines was chosen over one line per job because the pay app's lines are fixed when it is sent and already add up to its amount (Duracash refuses to send one that doesn't), and they all use one item, so nothing needs a QuickBooks item per schedule line.
- Retainage withheld is one negative line on the retainage item. With that item pointed at Retainage Receivable (an Other Current Asset), the invoice total is what the client owes now, income counts the whole of the work billed, and the retainage waits on the balance sheet. This is the common way contractors book retainage in QuickBooks Online, which has no retainage feature of its own. A company on cash-basis books may point the retainage item at an income account instead; ask the company's bookkeeper.
- When retainage is released, Duracash doesn't send that to QuickBooks yet: bill it there with a positive line on the same Retainage item, which moves it from Retainage Receivable to Accounts Receivable.
- A pay app pulled back in Duracash after it was sent to QuickBooks is not voided there; void it in QuickBooks.
- Sales tax isn't set on the invoice. A company whose QuickBooks adds sales tax automatically should check the first invoice.

**Live test (sandbox)**

1. On staging, with development keys and `QBO_ENVIRONMENT=sandbox`, sign in as an owner. Press Connect QuickBooks, sign in to Intuit, pick the sandbox company and approve. You should come back to Payments, connected.
2. Choose items. The list should show the sandbox's Service and Non-inventory items.
3. Link clients. A client named like a sandbox customer should be suggested.
4. On a sent pay app with retainage, press Send to QuickBooks. In the sandbox, open the invoice: its lines, the negative retainage line and the total should match the pay app's PDF. Check Retainage Receivable on the balance sheet.
5. Press Send to QuickBooks again: the same invoice, not a second one. Edit the invoice's amount in QuickBooks and send again: Duracash should ask before replacing it.
6. In the sandbox, receive a payment against that invoice. Press Sync now: the pay app should read paid, with the payment in its history. Press Sync now again: nothing new.
7. Make a client whose name is already a sandbox vendor, and send its pay app: the error should say the name is taken, in plain words.
8. Leave it connected over a day: the next day's sync should run (server log: `QuickBooks sync`), which also proves the token refresh.
9. The older steps still apply: Read payments from QuickBooks, Check against QuickBooks, Download for QuickBooks, Disconnect (and check Connected apps at Intuit).
10. Repeat 1, 2, 4 and 6 on production with a real company before telling a client it works.

**What was checked, Nov 27 2026 (qbo_sync.py)**

| Item | Result | Source |
|---|---|---|
| Create and update address: `POST /v3/company/<realm>/invoice` (and `/customer`); an update carries `Id` and `SyncToken`; `GET /invoice/<id>` reads one | Matches | [python-quickbooks client](https://github.com/ej2/python-quickbooks/blob/master/quickbooks/client.py) (Intuit's reference pages don't render for the tools used) |
| Invoice fields `CustomerRef`, `Line`, `DocNumber`, `TxnDate`, `DueDate`, `PrivateNote`, `CustomerMemo`, `TotalAmt`, `Balance`; line `DetailType` `SalesItemLineDetail` with `ItemRef`, `Qty`, `UnitPrice`, and `Amount`, `Description` | Match | [python-quickbooks: invoice](https://github.com/ej2/python-quickbooks/blob/master/quickbooks/objects/invoice.py), [detail lines](https://github.com/ej2/python-quickbooks/blob/master/quickbooks/objects/detailline.py), [Intuit Java SDK: SalesItemLineDetail](https://static.developer.intuit.com/sdkdocs/qbv3doc/ipp-v3-java-devkit-javadoc/com/intuit/ipp/data/SalesItemLineDetail.html) |
| `sparse: true` on an update (other fields kept; the `Line` list sent replaces the invoice's lines) | Not confirmed from a readable source | Live test step 5 |
| Errors come as `Fault.Error[]` with `Message`, `Detail`, `code`; 5010 stale SyncToken, 6240 duplicate name, 610 not found | Match | [python-quickbooks client](https://github.com/ej2/python-quickbooks/blob/master/quickbooks/client.py), [Intuit: handling common errors](https://developer.intuit.com/app/developer/qbo/docs/develop/troubleshooting/handling-common-errors) (title and codes from search results) |
| Throttling: HTTP 429, 500 requests a minute and 10 a second per company; Intuit advises waiting before retrying | Matches; Duracash retries 429 and 5xx three times with growing waits (Retry-After when sent), at most a minute each | [Intuit: API call limits and throttles](https://developer.intuit.com/app/developer/qbo/docs/learn/limits-and-throttles) (from search results) |
| `requestid` on writes: Intuit answers a repeated id with the first answer, so a retried create doesn't duplicate; at most 50 characters | Matches | [Intuit developer blog on requestid](https://blogs.a.intuit.com/2015/04/06/15346/) (from search results), [python-quickbooks client](https://github.com/ej2/python-quickbooks/blob/master/quickbooks/client.py) |
| Apostrophes in a query value are escaped with a backslash | Matches | [Intuit: data queries](https://developer.intuit.com/app/developer/qbo/docs/learn/explore-the-quickbooks-online-api/data-queries) (from search results) |
| A payment's link to an invoice: `Payment.Line[].LinkedTxn[]` with `TxnId` and `TxnType` `Invoice`, the line's `Amount` being what was applied | Matches (as checked Nov 3) | [Intuit: linked transactions](https://developer.intuit.com/app/developer/qbo/docs/workflows/manage-linked-transactions) |
| Retainage as a negative line on an item tied to an Other Current Asset account | The usual QuickBooks Online practice, per Intuit's community answers and contractor guides | [QuickBooks community: contractor retention](https://quickbooks.intuit.com/learn-support/en-us/reports-and-accounting/contractor-retention/00/1065395), [Levelset: retainage receivable in QuickBooks](https://www.levelset.com/blog/recording-tracking-retainage-receivable-quickbooks-for-contractors/), [Sage: track retainage in QuickBooks Online](https://help.sagecm.intacct.com/Content/AccountingLink/CLQuickBooksOnline/QuickBooksOnlineSetup/QuickBooksOnlineDataSetup_RetainageItem.htm) |
| A negative `UnitPrice` with `Qty` 1 on an invoice line through the API | Not confirmed (the QuickBooks screen allows it) | Live test step 4 |
| Item `Type` values `Service` and `NonInventory`; `IncomeAccountRef`; Customer `DisplayName`, `PrimaryEmailAddr.Address`, `Active` | From memory and the libraries above, not a page read | Live test steps 2 and 3 |

**What was checked, Nov 3 2026**

| Item in `qbo.py` | Result | Source |
|---|---|---|
| Authorize, token and revoke addresses | Match | Intuit's live discovery documents: [sandbox](https://developer.api.intuit.com/.well-known/openid_sandbox_configuration), [production](https://developer.api.intuit.com/.well-known/openid_configuration) |
| Scope | Matches | [Intuit's Python OAuth client](https://github.com/intuit/oauth-pythonclient/blob/master/intuitlib/enums.py) |
| Minor version 75 | Matches; it is the floor. Lower values and no value are answered as 75. | [Intuit: minor versions](https://developer.intuit.com/app/developer/qbo/docs/learn/explore-the-quickbooks-online-api/minor-versions) |
| New refresh token on each refresh; the code reads the expiry from each reply | Matches. Intuit's refresh tokens now last at most five years, and the old 100-day figure is only a fallback here. | [Intuit release notes](https://developer.intuit.com/app/developer/qbo/docs/release-notes/general-release-notes), [Authorization FAQ](https://developer.intuit.com/app/developer/qbo/docs/develop/authentication-and-authorization/faq) |
| API addresses; `STARTPOSITION`, `MAXRESULTS` | Match Intuit's query docs. The 1,000-row page limit could not be confirmed from a page readable here. The code stops at a short page, so a lower real limit would cut a read short on a very large company. | [Intuit: data queries](https://developers.intuit.com/app/developer/qbo/docs/learn/explore-the-quickbooks-online-api/data-queries) |
| Payment's `Line[].LinkedTxn[].TxnId` and `TxnType` | Match | [Intuit: linked transactions](https://developer.intuit.com/app/developer/qbo/docs/workflows/manage-linked-transactions) |
| Other Payment and Invoice field names | Match, from a widely used third-party library, as Intuit's reference pages couldn't be read by the tools used | [python-quickbooks: payment](https://github.com/ej2/python-quickbooks/blob/master/quickbooks/objects/payment.py), [invoice](https://github.com/ej2/python-quickbooks/blob/master/quickbooks/objects/invoice.py) |
| Column names of the invoice import file | Not checked against a source | Test step 8 |

The one code change then: a 403 tells the owner to check `QBO_ENV` (now `QBO_ENVIRONMENT`) against the keys, since mixing sandbox and live is the likely first mistake.

The sample QuickBooks import on a job runs only on the demo company. The server refuses it for any other, and a test covers it.

## DocuSign eSignature

With DocuSign connected, the Sign step offers **Sign with DocuSign** beside the typed name, on a pay app's approval, a pending change order and a state's waiver form. Duracash makes its own PDF with invisible anchor words where the signature and date go, creates an envelope in the company's own DocuSign account with the signed-in person as an embedded signer, and sends them to DocuSign's signing page and back. When DocuSign says the envelope is completed, the signed PDF and the certificate of completion go on the job's records, and the document counts as signed: the pay app's approval (so Approve & send lets it go), a lien waiver of its type on file, or, when the client signed too, the change order approved. Without the settings, or with no account connected, nothing changes: the typed name works as before.

**Settings**

| Name | Where | What it is |
|---|---|---|
| `DOCUSIGN_CLIENT_ID` | staging, production | The integration key, from Apps and Keys in the DocuSign account that owns the app. |
| `DOCUSIGN_CLIENT_SECRET` | staging, production | A secret key of that integration key. Shown once when it is made. Production needs its own (go-live doesn't copy secrets). |
| `DOCUSIGN_ENV` | staging: `demo`; production: `production` after go-live | `demo` (the default) signs in at `account-d.docusign.com`; `production` at `account.docusign.com`. |
| `DOCUSIGN_CONNECT_KEY` | staging, production | The Connect HMAC secret. Two, comma separated, only while one replaces the other. Without it Duracash asks for no webhook and trusts none: a signature is filed when the signer comes back, or with Check DocuSign on Settings. |
| `DURACASH_URL` | staging, production | Required, `https://`: it makes the redirect URI and the webhook's address. |

**To set up at DocuSign (Stephen)**

1. Make a free developer account at developers.docusign.com. It is a demo account: nothing signed there is legally binding.
2. Apps and Keys, Add App and Integration Key. Authentication: Authorization Code Grant, with a client secret (Duracash keeps it on the server). Add a secret key and copy it.
3. Redirect URIs, exactly: `https://<staging site>/api/docusign/callback`, and later the live site's. No fragment (`#`).
4. Connect HMAC, in the same account (the one that holds the integration key): Admin, Integrations, Connect, Connect Keys, Add Secret Key. Copy the whole key: later only its first four characters show. No Connect configuration is needed: Duracash asks for the webhook on each envelope (envelope-level `eventNotification` with `includeHMAC` and `integratorManaged`), so DocuSign signs those messages with this account's keys and the contractors' own accounts need no Connect set-up. The webhook goes to `https://<site>/api/docusign/webhook`.
5. In Render, set the four `DOCUSIGN_*` names above on staging (`DOCUSIGN_ENV=demo`).
6. A contractor's own account must allow embedded signing: DocuSign refuses an embedded signer when the account requires signers to have, or log in to, a DocuSign account.

**Go-live (before any real contractor uses it)**

- The integration key starts in the demo environment. Moving it to production is DocuSign's go-live review, started from the developer console. It asks for a one-time identity verification, a paid production DocuSign account to manage the integration (admin or developer access), OAuth 2.0 and the API rules met, and the right integration type. Manual reviews take about 24 to 48 hours.
- **Integration type is the decision for Stephen.** Each Duracash company connects its *own* DocuSign account, which DocuSign calls a public integration, and a public integration needs the DocuSign Partner Program. The other kind, where contractors don't need DocuSign at all and envelopes go through Duracash's account, is an embedded integration and needs ISV Embed partner licensing. Duracash is built as the first. Ask DocuSign which applies before submitting.
- After go-live the key is copied to production, but its secrets and redirect URIs are not: make a production secret, add the live redirect URI, make a Connect key on the production account that holds the key, then set `DOCUSIGN_CLIENT_SECRET`, `DOCUSIGN_CONNECT_KEY` and `DOCUSIGN_ENV=production` on production. Each contractor's API address differs by data centre; Duracash reads it from `/oauth/userinfo` when they connect.
- The API rules are part of the review: no more than one status read per envelope per 15 minutes. Duracash reads an envelope only when the signer comes back from signing, on Check DocuSign, and when a webhook message has no recipients in it, never more often than that.

**Live test (staging, demo keys)**

1. Sign in as an owner. On Settings, Company, press Connect DocuSign, sign in to the demo account and allow. You should come back connected, with the account's name.
2. On a pay app ready to send, as an approver, press Sign with DocuSign. Check the Sign Here and Date tabs sit on the contractor's signature line (the offsets in `docusign.tabs` may need a few pixels). Sign. You come back to the pay app, signed; Approve & send lets it go.
3. On the job's records: the signed PDF and the certificate of completion.
4. With "Ask clients to sign change orders in DocuSign too" on, sign a pending change order. A test address as the client's email gets DocuSign's email; sign there. The webhook should mark the change order approved within a minute. Turn the switch off and sign another: it stays pending, with the signed copy kept.
5. With `DURACASH_WAIVER_FORMS=GA` and a Georgia job, sign a waiver form. Check where the signature lands and that it is on the records as a lien waiver of its type.
6. Press Sign with DocuSign and then Finish Later in DocuSign: nothing is marked signed; Check DocuSign on Settings says it is still waiting.
7. Edit a pay app while its envelope is out, then sign: the signed copy is kept, the approval isn't, and the audit trail says why.
8. Wait over eight hours and sign again, which proves the token refresh.
9. In DocuSign's Connect logs (Admin, Connect, Logs), check the messages were delivered with a 200.

**What was checked, Nov 27 2026**

| Item in `docusign.py` | Result | Source |
|---|---|---|
| Authorize, token and userinfo addresses; scope `signature`; code exchange and refresh with Basic auth; `accounts[].account_id`, `is_default`, `base_uri`; `/restapi/v2.1/accounts/{id}` | Match | [Authorization Code Grant: get a token](https://developers.docusign.com/platform/auth/authcode/authcode-get-token/) |
| `extended` scope: each refresh brings a refresh token with a full lifetime; access tokens about 8 hours; refresh tokens about 30 days | Match | Same page |
| Envelope JSON (`documents[].documentBase64`, `fileExtension`, `documentId`; `recipients.signers[]` with `clientUserId`, `routingOrder`; `status: sent`) and the recipient view (`returnUrl`, `authenticationMethod`, `email`, `userName`, `clientUserId`) | Match | [Embedded signing how-to](https://developers.docusign.com/docs/esign-rest-api/how-to/request-signature-in-app-embedded/) |
| The signing page's address works once and expires after 5 minutes; the `event` values on the return address; don't rely on the return address alone | Match: it is used at once and never stored, and the return only triggers a status read | [EnvelopeViews: createRecipient](https://developers.docusign.com/docs/esign-rest-api/reference/envelopes/envelopeviews/createrecipient/) |
| Anchor tabs (`anchorString`, `anchorUnits`, offsets, `anchorIgnoreIfNotPresent`), white anchor text, anchors apply to all documents | Match; `dateSigned` takes the same anchor fields | [Auto-place tabs](https://developers.docusign.com/docs/esign-rest-api/esign101/concepts/tabs/auto-place/), [EnvelopeRecipientTabs reference](https://developers.docusign.com/docs/esign-rest-api/reference/envelopes/enveloperecipienttabs/create/) |
| Documents `combined` (with `certificate=false`) and `certificate` as PDF | Match | [EnvelopeDocuments: get](https://developers.docusign.com/docs/esign-rest-api/reference/envelopes/envelopedocuments/get/) |
| HMAC: `X-DocuSign-Signature-1..N`, one per key; base64 of HMAC-SHA256 over the raw body; one match is enough | Match; compared in constant time | [HMAC security](https://developers.docusign.com/platform/webhooks/connect/hmac/), [Validate a message](https://developers.docusign.com/platform/webhooks/connect/validate/), [Set up HMAC](https://developers.docusign.com/platform/webhooks/connect/setting-up-hmac/) |
| Envelope-level `eventNotification` (`deliveryMode: SIM`, `eventData.version: restv2.1`, `includeHMAC`, `integratorManaged`, `requireAcknowledgment`) | From DocuSign's developer blog, not the API reference | [JSON SIM and HMAC](https://www.docusign.com/blog/developers/event-notifications-using-json-sim-and-hmac), [HMAC for partners](https://www.docusign.com/blog/developers/introducing-hmac-partners-docusign-connect) |
| Polling limit: one status read per envelope per 15 minutes | Match | [API rules and limits](https://developers.docusign.com/docs/esign-rest-api/esign101/rules-and-limits/) |
| Go-live: review, identity verification, paid account, integration types, secrets and redirect URIs not copied, production base URIs | As described above | [Go-live](https://developers.docusign.com/platform/go-live/), [eSignature go-live](https://developers.docusign.com/docs/esign-rest-api/go-live/) |
| The JSON SIM message's shape (`data.envelopeId`, `data.accountId`, `data.envelopeSummary`) | Not checked against a DocuSign page | Live test step 4, and Connect's logs |
| The tabs' exact positions on our PDFs | Not checked | Live test steps 2 and 5 |

**For the attorney**

- Georgia's statutory waiver form has a witness line. DocuSign records the signer, but nobody witnesses the signature. Whether a DocuSign-signed Georgia waiver needs a witness, and how, is a question for counsel before the button is used for Georgia waivers.
- DocuSign shows signers its own consent to do business electronically. The typed-name consent wording in Duracash isn't shown in DocuSign; the change order PDF says it is signed through DocuSign.

## Twilio sources

- [Webhook security](https://www.twilio.com/docs/usage/webhooks/webhooks-security)
- [Message resource](https://www.twilio.com/docs/messaging/api/message-resource)
- [A2P 10DLC](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc)
- [Business information to gather](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc/collect-business-info)
- [Opt-out keywords](https://support.twilio.com/hc/en-us/articles/223134027-Twilio-Support-for-Opt-Out-Keywords-SMS-STOP-Filtering)
- [Error 21610](https://www.twilio.com/docs/api/errors/21610)
