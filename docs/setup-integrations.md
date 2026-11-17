# Setting up email, texts and QuickBooks

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

**To register with the mail provider**

- Verify the `MAIL_FROM` domain (SPF and DKIM).
- Bounce webhook: `https://<site>/api/mail-events/<MAIL_EVENTS_TOKEN>`
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

Duracash only reads from QuickBooks. It never changes the books.

**Settings**

| Name | Where | What it is |
|---|---|---|
| `QBO_CLIENT_ID`, `QBO_CLIENT_SECRET` | staging, production | From the app's Keys page in the Intuit developer portal. Development and production keys are separate. |
| `QBO_ENV` | staging: `sandbox`; production: `production` | Must match the keys. Default `sandbox`. |
| `DURACASH_URL` | staging, production | Required: it makes the redirect URI. |

**To register at Intuit**

- Redirect URI: `https://<site>/api/qbo/callback`, exactly, once under Development for staging and once under Production for the live site. The two lists are separate.
- Scope: `com.intuit.quickbooks.accounting`.
- Production keys need Intuit's app assessment, plus public privacy policy and terms URLs. Allow time for that.

**Live test**

1. On staging, with development keys and `QBO_ENV=sandbox`, sign in as an owner. On Payments, press Connect QuickBooks.
2. Sign in to Intuit, pick the sandbox company and approve. You should come back to Payments, connected.
3. In the sandbox company, make a customer, an invoice numbered like a sent pay app (say `PA-OAK-01`) and a payment on that invoice.
4. Press Read payments from QuickBooks. The payment should match that pay app. Record it. Read again; it should say already recorded.
5. Run the reconciliation. Change the invoice's amount in the sandbox and check that the difference shows.
6. Wait over an hour, then read again, which proves the token refresh. Check that it stays connected the next day.
7. Press Disconnect. Check the connection is gone from the company's Connected apps at Intuit.
8. On a sent pay app, press Download for QuickBooks, and import the file at Sales, Invoices, Import in the sandbox.
9. Repeat 1 to 4 on production with a real company before telling a client it works.

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

The one code change: a 403 now tells the owner to check `QBO_ENV` against the keys, since mixing sandbox and live is the likely first mistake.

The sample QuickBooks import on a job runs only on the demo company. The server refuses it for any other, and a test covers it.

## Twilio sources

- [Webhook security](https://www.twilio.com/docs/usage/webhooks/webhooks-security)
- [Message resource](https://www.twilio.com/docs/messaging/api/message-resource)
- [A2P 10DLC](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc)
- [Business information to gather](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc/collect-business-info)
- [Opt-out keywords](https://support.twilio.com/hc/en-us/articles/223134027-Twilio-Support-for-Opt-Out-Keywords-SMS-STOP-Filtering)
- [Error 21610](https://www.twilio.com/docs/api/errors/21610)
