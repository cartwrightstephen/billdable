"""The second independent hunt of Nov 5: a slow client on a refused body, a long file under the
company lock, the daily and monthly emails after a kill, who changes a whole client's waiver rule,
and Confirm on old data."""

import json
import socket
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import alerts
import auth
import doc_check
import domain
import mailer
import monthly
import server
import storage
from domain import profiles
from test_hunt_nov5_http import Base

UTC = timezone.utc


class SlowClientOnARefusedBody(Base):
    def setUp(self):
        pass   # no company needed: the login route is open

    def tearDown(self):
        server.BODY_SECONDS = 60

    def test_the_drain_keeps_its_deadline(self):
        server.BODY_SECONDS = 1
        sock = socket.create_connection(("127.0.0.1", self.port))
        sock.settimeout(0.2)
        sock.sendall(b"POST /api/auth/login HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 2000000\r\n\r\n")
        started, answer = time.monotonic(), b""
        while time.monotonic() - started < 8 and not answer:
            sock.sendall(b"x")
            try:
                answer = sock.recv(200)
            except socket.timeout:
                time.sleep(0.8)
        sock.close()
        self.assertIn(b" 413 ", answer[:20], "the answer comes at the deadline, not after 64 KB more")
        self.assertLess(time.monotonic() - started, 6)

    def test_a_browser_still_sending_gets_the_answer(self):
        size = 3_000_000
        sock = socket.create_connection(("127.0.0.1", self.port))
        sock.settimeout(10)
        sock.sendall(f"POST /api/auth/login HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: {size}\r\n\r\n".encode())
        sock.sendall(b"a" * size)
        self.assertIn(b" 413 ", sock.recv(200)[:20])
        sock.close()

    def test_the_drain_takes_no_more_than_its_cap(self):
        with mock.patch.object(server, "DRAIN_BYTES", 100_000):
            sock = socket.create_connection(("127.0.0.1", self.port))
            sock.settimeout(10)
            sock.sendall(b"POST /api/auth/login HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 90000000\r\n\r\n")
            sent = 0
            started = time.monotonic()
            answer = b""
            sock.setblocking(False)
            while not answer and time.monotonic() - started < 8:
                try:
                    sent += sock.send(b"a" * 8192)
                except (BlockingIOError, OSError):
                    time.sleep(0.01)
                try:
                    answer = sock.recv(200)
                except (BlockingIOError, OSError):
                    pass
            sock.close()
            self.assertIn(b" 413 ", answer[:20])


class ALongFileUnderTheLock(unittest.TestCase):
    """A file read at attach and again at send, while everyone in the company waits."""

    def timed(self, text):
        started = time.monotonic()
        doc_check.waiver_reading([("a.txt", ".txt", text.encode())])
        return time.monotonic() - started

    def test_a_long_file_takes_no_time(self):
        for filler in ("unconditional ", "not an unconditional ", "u n c o n d i t i o n a l x ", "not " + "a " * 50, "un" * 7 + " ", "un \n" * 3, "[X] "):
            text = "Elm addition daily log. " + filler * (420_000 // len(filler))
            self.assertLess(self.timed(text), 1.0, filler)

    def test_the_whole_check_on_a_long_file(self):
        text = ("Elm addition daily log. " + "unconditional " * 30_000).encode()
        started = time.monotonic()
        doc_check.check([("a.txt", ".txt", text)], "Elm addition", "$1.00", 100, "conditional_progress", True)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_a_word_far_into_a_long_file_still_counts_for_the_guess(self):
        got = doc_check.waiver_reading([("a.txt", ".txt", ("filler " * 5000 + "unconditional lien waiver").encode())])
        self.assertEqual(got["guess"], "unconditional")


class Site:
    """What the background jobs are handed; send can die part way, as a killed process does."""

    def __init__(self, companies, die_on=None):
        self.companies, self.die_on, self.sent = companies, die_on, []

    def company_ids(self):
        return list(self.companies)

    def read(self, company_id):
        return {"company": {}, "jobs": []}

    def deleted(self):
        return set()

    def app_url(self):
        return "https://x.test"

    def log(self, level, **fields):
        pass

    def send(self, to, subject, body, company_id=""):
        if self.die_on == to:
            self.die_on = None
            self.sent.append(to)   # it went; the process died before anything after it ran
            raise SystemExit("killed")
        self.sent.append(to)
        return "sent"


class EmailsAfterAKill(unittest.TestCase):
    """At most once per address: a person never gets the email twice. The address in hand when the
    process died isn't sent again, and everyone not reached yet still gets theirs on the next run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._dir = storage.DATA_DIR
        storage.DATA_DIR = Path(self.tmp.name)
        self.addCleanup(setattr, storage, "DATA_DIR", self._dir)
        self.to = {"co-1": ["a1@x.test", "a2@x.test"], "co-2": ["b1@x.test", "b2@x.test"], "co-3": ["c1@x.test"]}
        patches = [mock.patch.object(mailer, "configured", return_value=True),
                   mock.patch.object(auth, "owner_emails", side_effect=lambda cid: self.to[cid]),
                   mock.patch.object(auth, "user_by_email", return_value=None),
                   mock.patch.object(auth, "all_users", return_value=[]),
                   mock.patch.object(domain, "daily_digest", return_value=("Due today", "body")),
                   mock.patch("metrics.month_summary", return_value=("Summary", "body")),
                   mock.patch.object(alerts, "notify")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.now = datetime(2026, 11, 5, 14, tzinfo=UTC)
        self.everyone = sorted(a for addrs in self.to.values() for a in addrs)

    def run_killed(self, go, die_on):
        site = Site(self.to, die_on=die_on)
        with self.assertRaises(SystemExit):
            go(site)
        again = Site(self.to)
        go(again)
        return site.sent, again.sent

    def test_daily_each_address_once_after_a_kill_at_any_point(self):
        for die_on in self.everyone:
            (storage.DATA_DIR / monthly.DAILY_PATH_NAME).unlink(missing_ok=True)
            first, second = self.run_killed(lambda s: monthly.send_daily_digests(s, self.now), die_on)
            self.assertEqual(sorted(first + second), self.everyone, f"killed sending to {die_on}")

    def test_the_company_cut_short_says_so(self):
        first, second = self.run_killed(lambda s: monthly.send_daily_digests(s, self.now), "b1@x.test")
        self.assertEqual(second, ["b2@x.test", "c1@x.test"])
        alerts.notify.assert_called()
        self.assertIn("co-2", alerts.notify.call_args[0][1])

    def test_daily_once_finished_nothing_goes_again(self):
        site = Site(self.to)
        monthly.send_daily_digests(site, self.now)
        self.assertEqual(sorted(site.sent), self.everyone)
        again = Site(self.to)
        monthly.send_daily_digests(again, self.now)
        self.assertEqual(again.sent, [])
        done = json.loads((storage.DATA_DIR / monthly.DAILY_PATH_NAME).read_text())
        self.assertEqual(done["co-1"], "2026-11-05")

    def test_daily_the_next_day_goes_to_everyone(self):
        first, _ = self.run_killed(lambda s: monthly.send_daily_digests(s, self.now), "b1@x.test")
        tomorrow = Site(self.to)
        monthly.send_daily_digests(tomorrow, self.now.replace(day=6))
        self.assertEqual(sorted(tomorrow.sent), self.everyone)

    def test_a_failure_in_one_company_loses_nobody_else(self):
        site = Site(self.to)
        real = site.read
        site.read = lambda cid: (_ for _ in ()).throw(KeyError("x")) if cid == "co-2" else real(cid)
        monthly.send_daily_digests(site, self.now)
        self.assertEqual(sorted(site.sent), ["a1@x.test", "a2@x.test", "c1@x.test"])

    def test_monthly_each_address_once_after_a_kill_at_any_point(self):
        when = datetime(2026, 11, 2, 9, tzinfo=UTC)
        with mock.patch.dict("os.environ", {"MONTHLY_SUMMARY_EMAILS": "1"}), mock.patch("backup.backups", return_value=[]):
            for die_on in self.everyone:
                (storage.DATA_DIR / monthly.MONTHLY_PATH_NAME).unlink(missing_ok=True)
                first, second = self.run_killed(lambda s: monthly.run_monthly_jobs(s, when), die_on)
                self.assertEqual(sorted(first + second), self.everyone, f"killed sending to {die_on}")
            again = Site(self.to)
            monthly.run_monthly_jobs(again, when)
            self.assertEqual(again.sent, [], "once the run finished, nothing goes again this month")


class WholeClientWaiverRule(Base):
    """Item 7, by profile: a whole client's rule takes the company permission, both ways."""

    def setUp(self):
        super().setUp()
        line = self.job()["sov"]["lines"][0]
        status, data = self.post(f"/api/jobs/{self.job_id}/sov-progress", {"done": {line["id"]: "0.25"}})
        self.assertEqual(status, 200, data)
        self.item = next(p for p in self.draft()["packet"] if p.get("waiver_kind"))
        self.payer_id = self.job()["payer_id"]

    def give_member(self, perms):
        with server.company_lock(self.company):
            state = server.load_company(self.company)
            state["company"]["profiles"] = [dict(p, perms=perms) if p["id"] == "member" else p for p in profiles.profiles(state)]
            server.save_company(self.company, state)

    def payer(self):
        return next(p for p in server.load_company(self.company)["payers"] if p["id"] == self.payer_id)

    def skip(self, cookie, **body):
        return self.post(f"/api/jobs/{self.job_id}/packet-skip/{self.item['id']}", {"reason": "They don't take them", **body}, cookie=cookie)

    def test_a_member_cant_mark_the_whole_client(self):
        status, data = self.skip(self.bob, whole_client=True)
        self.assertEqual(status, 403, data)
        self.assertNotIn("waiver_not_needed", self.payer())

    def test_a_member_cant_take_it_back_for_the_whole_client(self):
        self.assertEqual(self.skip(self.ann, whole_client=True)[0], 200)
        status, data = self.post(f"/api/payers/{self.payer_id}/waiver-needed", {}, cookie=self.bob)
        self.assertEqual(status, 403, data)
        self.assertIn("waiver_not_needed", self.payer())

    def test_holding_company_does_both(self):
        self.give_member(["company"])
        self.assertEqual(self.skip(self.bob, whole_client=True)[0], 200)
        self.assertIn("waiver_not_needed", self.payer())
        status, data = self.post(f"/api/payers/{self.payer_id}/waiver-needed", {}, cookie=self.bob)
        self.assertEqual(status, 200, data)
        self.assertNotIn("waiver_not_needed", self.payer())

    def test_on_the_job_a_member_does_both(self):
        self.assertEqual(self.skip(self.bob)[0], 200)
        self.assertTrue(self.job().get("waiver_skipped"))
        status, data = self.post(f"/api/jobs/{self.job_id}/waiver-needed", {}, cookie=self.bob)
        self.assertEqual(status, 200, data)
        self.assertFalse(self.job().get("waiver_skipped"))


class ConfirmOnOldData(Base):
    """Item 8: an unconfirmed attachment by name only, with no record, from before files were kept."""

    def test_the_page_sends_blank_for_no_record(self):
        line = self.job()["sov"]["lines"][0]
        self.post(f"/api/jobs/{self.job_id}/sov-progress", {"done": {line["id"]: "0.25"}})
        item = next(p for p in self.draft()["packet"] if "nsurance" in p["label"])
        res, data = self.upload("coi.txt", b"nothing about the place here at all", kind="insurance", attach=item["id"])
        self.assertEqual(res.status, 200, data)
        with server.company_lock(self.company):
            state = server.load_company(self.company)
            job = next(j for j in state["jobs"] if j["id"] == self.job_id)
            stored = next(p for i in job["invoices"] for p in i["packet"] if p["id"] == item["id"])
            stored["attachments"].append({"name": "COI by name", "unconfirmed": True})
            server.save_company(self.company, state)
        item = next(p for p in self.draft()["packet"] if p["id"] == item["id"])
        shown = [a.get("record_id") or "" for a in item["attachments"] if a.get("unconfirmed")]
        self.assertIn("", shown)
        status, data = self.post(f"/api/jobs/{self.job_id}/packet-confirm/{item['id']}", {"looked": True, "shown": shown})
        self.assertEqual(status, 200, data)
        self.assertTrue(next(p for p in self.draft()["packet"] if p["id"] == item["id"])["present"])

    def test_a_newer_file_still_asks_for_its_own_look(self):
        line = self.job()["sov"]["lines"][0]
        self.post(f"/api/jobs/{self.job_id}/sov-progress", {"done": {line["id"]: "0.25"}})
        item = next(p for p in self.draft()["packet"] if "nsurance" in p["label"])
        self.upload("coi.txt", b"nothing about the place here at all", kind="insurance", attach=item["id"])
        status, data = self.post(f"/api/jobs/{self.job_id}/packet-confirm/{item['id']}", {"looked": True, "shown": [""]})
        self.assertEqual(status, 409, data)
        self.assertTrue(data.get("review_changed"))


if __name__ == "__main__":
    unittest.main()
