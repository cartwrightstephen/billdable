# Runbooks

Step-by-step for the five things either founder may need to do without the other. Commands run
from the repository folder, or on Render from the service's **Shell** tab (it opens in `/app`
with the service's settings). Nothing here was run against the live service; the Render steps
follow `render.yaml` and should be checked the first time through.

## 1. Deploy

1. Open a pull request into `dev`. GitHub Actions runs the tests; merge only when it is green.
2. Merging into `dev` deploys **duracash-staging** (free, throwaway data, no email). Click
   through the change there: sign up, create a job, draft a pay app, open the PDF.
3. Open a pull request from `dev` into `main` and merge it. Render deploys **duracash**.
4. Check `https://<site>/healthz`: `ok` is true, `env` is `production`, `last_backup` is the
   same time as before the deploy (a new one appears within `BACKUP_HOURS`).
5. Sign in. If the deploy went wrong, Render's **Rollback** on the previous deploy puts the old
   code back; the data on the disk is not touched by a rollback.

The first production deploy with the disk: deploy twice and repeat step 4 both times. Only then
set `DURACASH_DATA_PERSISTENT=1`.

## 2. Restore from a backup

Local backups (on the disk, newest 14 kept):

```bash
python3 backup.py list
```

1. In Render, **Suspend** the service so nothing writes during the restore.
2. In the Shell tab: `python3 backup.py restore /var/data/backups/<file>.tar.gz`. The current
   data is moved to `/var/data/pre-restore-<time>/`, never deleted. That includes the list of
   deleted companies, the QuickBooks connections and the monthly and daily job records: the
   backup's copies come back with the companies, so a company deleted after the backup is back
   and can be changed. A backup made with `BACKUP_UPLOADS=0` leaves the uploads on the disk as
   they are; to restore them too, use the off-site copy.
3. **Resume** the service and sign in.

From the off-site copy (disk lost, or the local backups are bad):

1. Download the newest `duracash-….tar.gz.age` from the B2 bucket.
2. On a laptop with the age private key: `age -d -i key.txt FILE.age > backup.tar.gz`
3. Copy `backup.tar.gz` to the server (or restore on a laptop to inspect it first with
   `DURACASH_DATA_DIR=/tmp/check python3 backup.py restore backup.tar.gz`), then follow the
   local steps above.

**Monthly drill.** A backup nobody has restored is a guess. Once a month, in the Shell tab
(the server can keep running; nothing live is touched):

```bash
python3 backup.py drill
```

The server also runs it by itself once a month and posts an alert if it fails
(`monthly_jobs.json` in the data directory records the last run). By hand, it unpacks the newest
backup into a temporary folder and checks that the accounts file reads,
every user's company is there, every company opens with its audit chain intact, and every
uploaded file it refers to is in the backup. It prints PASS or each problem (exit code 1).
Do the same with a downloaded off-site copy: decrypt it as above, then
`python3 backup.py drill backup.tar.gz`.

## 3. Rotate a secret

Change the value at the provider first, then in Render (**Environment**), then save; Render
redeploys with the new value.

| Secret | Where it comes from | After rotating |
| --- | --- | --- |
| `SMTP_PASSWORD` | The mail provider's API key or SMTP password | Send yourself a password reset to check email still goes |
| `TWILIO_AUTH_TOKEN` | Twilio console | Text the number; the item should land in the Inbox |
| `BACKUP_S3_KEY_ID`, `BACKUP_S3_SECRET` | B2 application keys (write-only) | `python3 backup.py upload` in the Shell; `/healthz` shows a new `offsite_backup` |
| `BACKUP_AGE_RECIPIENT` | `age-keygen` on a laptop; store the private key in both password managers | Upload one backup and decrypt it on the laptop before deleting the old key |
| `ALERT_WEBHOOK_URL` | Slack or Better Stack | The next crash or failed backup posts there |

A customer's inbound email, text or calendar address that leaked: the owner uses
**Make a new address** in the app (Inbox or Calendar). Signing someone out everywhere:
`python3 admin.py end-sessions <company id>`.

## 4. Delete a company on request

The owner can do it in the app: **Settings → Company → Your data, reset and delete → Delete the company**. When they
can't (they lost access, or asked by email):

1. Confirm the request comes from the account's owner (reply to the email on the account).
2. Find the company: `python3 admin.py find owner@their.example`
3. Optional, to send them their data first: there is no admin export; ask them to use
   **Export data** while they can still sign in.
4. `python3 admin.py delete-company <company id>` and type the id to confirm.
5. Tell them it is done, and that copies in backups expire on their own: local backups after
   `BACKUP_KEEP` days, off-site copies after the bucket's retention (about 35 days).

## 4a. Put a company on hold (read-only)

For a payment that is past due, or while a dispute is sorted out. Nothing is deleted: the team
can still sign in, see everything and use **Export data**, change passwords and two-step
sign-in, manage the team and delete the company. Every change to jobs, money and the Inbox is
refused with the reason, and the app shows it in a banner.

1. `python3 admin.py read-only <company id> payment is past due` (the words after the id are
   the reason they see).
2. To undo: `python3 admin.py writable <company id>`. Both are written to the company's Audit.

## 5. Incident

Something is down, data looks wrong, or a leak is suspected.

1. **Look**: `/healthz`, Render's **Logs** (one JSON line per request; a crash logs its
   traceback with the request id the user saw), and the alert channel.
2. **Contain**: if data is being damaged or exposed, **Suspend** the service. A suspended
   service shows Render's page; nothing more is written.
3. **Tell the other founder** with what you know and don't know.
4. **Fix**: roll back the deploy (runbook 1, step 5), or restore (runbook 2).
5. **Check the audit trail**: `python3 audit_check.py` names any company whose audit rows were
   changed after they were written.
6. **Write it down** within two days: what happened, when, who was affected, what was done, and
   what stops it happening again. If customer data may have been exposed, ask the lawyer what
   notice the law and the Privacy Policy require before telling customers.

## 6. QuickBooks Online (read-only)

Once the Intuit Developer app exists (setup doc, section 1):

1. In the Intuit app's settings, add the redirect URI `<DURACASH_URL>/api/qbo/callback`, for
   example `https://duracash.onrender.com/api/qbo/callback`, for both Development and Production.
2. On Render, set `QBO_CLIENT_ID` and `QBO_CLIENT_SECRET` (the Development keys first) and keep
   `QBO_ENV` unset: that is the sandbox.
3. An owner opens **Money → Payments → Connect QuickBooks**, signs in to Intuit and picks the
   sandbox company. **Read payments from QuickBooks** proposes matches; nothing is recorded until
   they are ticked. **Check against QuickBooks** lists pay apps missing from QuickBooks, totals
   that differ, and pay apps paid on one side only.
4. Check the first sandbox read against what the sandbox shows. The endpoints and field names in
   `qbo.py` were written from Intuit's documentation and tested only against a stand-in.
5. For real companies: Intuit's production keys (after their app review), `QBO_ENV=production`.

Tokens are in `qbo_connections.json` in the data directory, and the backups hold it (R6-06): a
restore keeps the connection. When a connection expires (Intuit's refresh tokens last about 100
days unused), or one was made after the backup that was restored, an owner presses Connect
QuickBooks again. Disconnect revokes the token at Intuit.

## 7. Billing with Stripe

Needs the company's Stripe account (after the entity exists).

1. In Stripe (test mode first): create the product and its monthly price; copy the price id
   (`price_…`) and the secret key (`sk_test_…`).
2. **Developers → Webhooks → Add endpoint**: `<DURACASH_URL>/api/stripe/webhook`, with the
   events `checkout.session.completed`, `customer.subscription.created`,
   `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.paid` and
   `invoice.payment_failed`. Copy its signing secret (`whsec_…`).
3. **Settings → Billing → Customer portal**: turn it on (update card, see invoices, cancel).
4. On Render: `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID`, `STRIPE_WEBHOOK_SECRET`. Leave
   `BILLING_ENFORCE` unset until a few test-mode subscriptions have gone through.
5. Test: an owner presses **Subscribe**, pays with Stripe's test card 4242 4242 4242 4242, and
   comes back to Settings showing **Trial** (or **Active**). Then, in Stripe, mark a test
   subscription unpaid and check the company shows the read-only banner once `BILLING_ENFORCE=1`.

The free trial carries over: a company that subscribes during it starts with Stripe's trial
ending on its last day, and when its first pay app is paid Duracash ends Stripe's trial, so
the first charge follows the business plan. Charges land on the 5th (`BILLING_DAY`), after the
contractor's own pay apps are out: a trial ends on the next 5th, and a subscription started after
the trial is anchored to the 5th with the first part-month prorated. A company that bills
homeowners in draws (Settings, Company, How you bill) gets a 45-day trial instead of 90.

Design partners: make a second price at $99 a month, set `STRIPE_PARTNER_PRICE_ID`, and mark each
partner with `python3 admin.py partner <company id> on` before they subscribe. The plan caps them
at 3; the command says how many there are. Nothing is ever deleted for an unpaid bill.
