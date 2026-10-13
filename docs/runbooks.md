# Runbooks

Step-by-step for the five things either founder may need to do without the other. Commands run
from the repository folder, or on Render from the service's **Shell** tab (it opens in `/app`
with the service's settings). Nothing here was run against the live service; the Render steps
follow `render.yaml` and should be checked the first time through.

## 1. Deploy

1. Open a pull request into `dev`. GitHub Actions runs the tests; merge only when it is green.
2. Merging into `dev` deploys **billdable-staging** (free, throwaway data, no email). Click
   through the change there: sign up, create a job, draft a pay app, open the PDF.
3. Open a pull request from `dev` into `main` and merge it. Render deploys **billdable**.
4. Check `https://<site>/healthz`: `ok` is true, `env` is `production`, `last_backup` is the
   same time as before the deploy (a new one appears within `BACKUP_HOURS`).
5. Sign in. If the deploy went wrong, Render's **Rollback** on the previous deploy puts the old
   code back; the data on the disk is not touched by a rollback.

The first production deploy with the disk: deploy twice and repeat step 4 both times. Only then
set `BILLDABLE_DATA_PERSISTENT=1`.

## 2. Restore from a backup

Local backups (on the disk, newest 14 kept):

```bash
python3 backup.py list
```

1. In Render, **Suspend** the service so nothing writes during the restore.
2. In the Shell tab: `python3 backup.py restore /var/data/backups/<file>.tar.gz`. The current
   data is moved to `/var/data/pre-restore-<time>/`, never deleted.
3. **Resume** the service and sign in.

From the off-site copy (disk lost, or the local backups are bad):

1. Download the newest `billdable-….tar.gz.age` from the B2 bucket.
2. On a laptop with the age private key: `age -d -i key.txt FILE.age > backup.tar.gz`
3. Copy `backup.tar.gz` to the server (or restore on a laptop to inspect it first with
   `BILLDABLE_DATA_DIR=/tmp/check python3 backup.py restore backup.tar.gz`), then follow the
   local steps above.

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

The owner can do it in the app: **Settings → Team & account → Delete the company**. When they
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
