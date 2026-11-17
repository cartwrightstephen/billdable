"""Who may get mail and texts: the allow-list (Decision 8), and that texts only reach a company's
own team members who opted in (Decision 7). All sends go to local stand-ins."""

import ast
import base64
import inspect
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import auth
import domain
import mailer
import outbox
import server
import sms
import storage

ROOT = Path(__file__).resolve().parents[1]
CLEAN = ("MAIL_ALLOWLIST", "SMS_ALLOWLIST", "DURACASH_ENV", "SMTP_HOST", "MAIL_FROM", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN",
         "TWILIO_NUMBER", "TWILIO_API_BASE", "SMS_DAILY_LIMIT")


def agree(user_id, phone):
    """A person who has answered the code texted to this number."""
    auth._CODES.pop(f"sms-to:{phone}", None)
    auth.confirm_sms_code(user_id, auth.issue_sms_code(user_id, phone))


def env(**values):
    base = {k: v for k, v in os.environ.items() if k not in CLEAN}
    return mock.patch.dict(os.environ, {**base, **values}, clear=True)


class FakeSmtp:
    sent = []

    def __init__(self, *a, **k):
        pass

    def ehlo(self):
        pass

    def starttls(self, **k):
        pass

    def login(self, *a):
        pass

    def quit(self):
        pass

    def send_message(self, msg, to_addrs=None):
        FakeSmtp.sent.append(list(to_addrs or []))
        return {}


class AllowListTests(unittest.TestCase):
    def test_unset_lets_everyone_through(self):
        with env():
            self.assertTrue(mailer.allowed("anyone@example.com"))

    def test_addresses_and_domains_ignoring_case(self):
        with env(MAIL_ALLOWLIST=" Pat@Example.com, @team.example "):
            self.assertTrue(mailer.allowed("pat@example.COM"))
            self.assertTrue(mailer.allowed("Sam@TEAM.example"))
            self.assertFalse(mailer.allowed("other@example.com"))
            self.assertFalse(mailer.allowed("sam@sub.team.example"))
            self.assertFalse(mailer.allowed("sam@team.example.evil.com"))

    def test_a_list_with_nothing_usable_sends_to_nobody(self):
        with env(MAIL_ALLOWLIST="team.example, ,"):
            self.assertFalse(mailer.allowed("sam@team.example"))

    def test_staging_with_no_list_sends_to_nobody(self):
        with env(DURACASH_ENV="staging"):
            self.assertFalse(mailer.allowed("sam@team.example"))
        with env(DURACASH_ENV="staging", MAIL_ALLOWLIST="@team.example"):
            self.assertTrue(mailer.allowed("sam@team.example"))

    def cfg(self):
        return {"host": "h", "port": 25, "user": "", "password": "", "security": "none", "from": "b@duracash.example", "daily_limit": 50}

    def test_the_send_itself_refuses_anyone_else(self):
        msg = EmailMessage()
        msg["To"] = "client@example.com"
        msg.set_content("x")
        msg.recipients = ["client@example.com"]
        FakeSmtp.sent = []
        with env(MAIL_ALLOWLIST="@team.example"):
            results = mailer.send([msg], self.cfg(), connect=FakeSmtp)
        self.assertEqual(results, [mailer.NOT_ALLOWED])
        self.assertEqual(FakeSmtp.sent, [])

    def test_a_copy_address_off_the_list_is_left_off_and_named(self):
        with env(MAIL_ALLOWLIST="@team.example"):
            msg, _ = mailer.build_message({"to": "a@team.example", "cc": ["b@team.example", "client@example.com"], "subject": "s", "body": "b"},
                                          "Co", "", [], self.cfg())
            FakeSmtp.sent = []
            self.assertEqual(mailer.send([msg], self.cfg(), connect=FakeSmtp), [None])
        self.assertEqual(FakeSmtp.sent, [["a@team.example", "b@team.example"]])
        self.assertNotIn("client@example.com", str(msg["Cc"]))
        self.assertTrue(any("client@example.com" in r for r in msg.refused))

    def test_system_mail_is_held_too(self):
        with env(MAIL_ALLOWLIST="@team.example", SMTP_HOST="h", MAIL_FROM="b@duracash.example"), \
                mock.patch.object(mailer, "send") as send:
            self.assertEqual(server.send_system_email("client@example.com", "s", "b"), mailer.NOT_ALLOWED)
            send.assert_not_called()

    def test_outbox_email_stays_marked_and_nothing_goes_out(self):
        state = {"email_log": [{"id": "e1", "to": "client@example.com", "subject": "s", "body": "b", "status": "queued"},
                               {"id": "e2", "to": "me@team.example", "subject": "s", "body": "b", "status": "queued"}],
                 "jobs": [], "files": [], "company": {"name": "Co"}}
        lock = threading.Lock()
        site = mock.Mock()
        site.lock.return_value = lock
        site.load.return_value = state
        site.inbound.return_value = ""
        with tempfile.TemporaryDirectory() as tmp, env(MAIL_ALLOWLIST="@team.example", SMTP_HOST="h", MAIL_FROM="b@duracash.example"), \
                mock.patch.object(storage, "DATA_DIR", Path(tmp)), mock.patch.object(domain, "light_snapshot", lambda s: s), \
                mock.patch.object(mailer, "send", side_effect=lambda msgs, cfg: [None] * len(msgs)) as send:
            outbox.deliver_queued(site, "co-x", {"email_verified": True, "email": "me@team.example"})
        self.assertEqual(state["email_log"][0]["status"], mailer.NOT_ALLOWED)
        self.assertTrue(mailer.NOT_ALLOWED.startswith("not sent"))
        self.assertEqual(state["email_log"][1]["status"], "sent")
        self.assertEqual([str(m["To"]) for m in send.call_args[0][0]], ["me@team.example"])

    def test_without_smtp_host_staging_still_sends_nothing(self):
        with env(DURACASH_ENV="staging", MAIL_ALLOWLIST="@team.example"):
            self.assertFalse(mailer.configured())

    def test_sign_in_codes_are_not_asked_of_someone_the_list_can_not_reach(self):
        handler = mock.Mock()
        handler._token.return_value = None
        user = {"id": "u1", "company_id": "co-x", "email": "tester@else.example"}
        with env(MAIL_ALLOWLIST="@team.example", SMTP_HOST="h", MAIL_FROM="b@duracash.example"), mock.patch.object(auth, "device_known", return_value=False):
            self.assertFalse(server.Handler._email_code_due(handler, user))
        with env(MAIL_ALLOWLIST="@team.example", SMTP_HOST="h", MAIL_FROM="b@duracash.example", DURACASH_ENV="production"), \
                mock.patch.object(auth, "device_known", return_value=False):
            self.assertTrue(server.Handler._email_code_due(handler, user), "production never skips the code")


class StandIn(BaseHTTPRequestHandler):
    calls = []
    answer = (201, {"sid": "SM1", "status": "queued"})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        StandIn.calls.append({"path": self.path, "form": {k: v[0] for k, v in parse_qs(raw.decode()).items()}, "auth": self.headers.get("Authorization")})
        status, reply = StandIn.answer
        body = json.dumps(reply).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class SmsBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = HTTPServer(("127.0.0.1", 0), StandIn)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for target, name, value in ((auth, "AUTH_PATH", root / "auth.json"), (storage, "DATA_DIR", root)):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        StandIn.calls = []
        StandIn.answer = (201, {"sid": "SM1", "status": "queued"})
        auth._CODES.clear()   # the hourly limit on codes is kept per number, across tests
        self.user = auth.create_user("pat@example.com", "correct horse battery", "Pat Lee", "co-x")
        self.state = {"payers": [{"id": "p1", "name": "Client", "phone": "(404) 555-0199"}], "jobs": []}

    def ready(self, **extra):
        return env(TWILIO_ACCOUNT_SID="ACtest", TWILIO_AUTH_TOKEN="standin", TWILIO_NUMBER="+14045550100", TWILIO_API_BASE=self.base, **extra)


class SmsTests(SmsBase):
    def test_phone_numbers(self):
        for text in ("(404) 555-0123", "404.555.0123", "+1 404 555 0123", "14045550123"):
            self.assertEqual(sms.e164(text), "+14045550123")
        for text in ("", "555-0123", "(404) 555-0123 x5", "+442071234567", "(104) 555-0123", None):
            self.assertEqual(sms.e164(text), "")

    def test_sends_through_the_stand_in(self):
        agree(self.user["id"], "+14045550123")
        with self.ready():
            self.assertEqual(sms.send_to_member(self.state, "co-x", self.user["id"], "Oak needs a signature."), "sent")
        call = StandIn.calls[0]
        self.assertEqual(call["path"], "/2010-04-01/Accounts/ACtest/Messages.json")
        self.assertEqual(call["form"]["To"], "+14045550123")
        self.assertEqual(call["form"]["From"], "+14045550100")
        self.assertIn("Oak needs a signature.", call["form"]["Body"])
        self.assertIn("Reply STOP", call["form"]["Body"])
        self.assertEqual(call["auth"], "Basic " + base64.b64encode(b"ACtest:standin").decode())

    def test_nothing_without_a_phone_or_without_opting_in(self):
        with self.ready():
            self.assertIn("no phone", sms.send_to_member(self.state, "co-x", self.user["id"], "x"))
            auth.set_sms(self.user["id"], "+14045550123", False)
            self.assertIn("not agreed", sms.send_to_member(self.state, "co-x", self.user["id"], "x"))
        self.assertEqual(StandIn.calls, [])

    def test_only_a_member_of_that_company(self):
        agree(self.user["id"], "+14045550123")
        with self.ready():
            self.assertIn("not on this team", sms.send_to_member(self.state, "co-other", self.user["id"], "x"))
            self.assertIn("not on this team", sms.send_to_member(self.state, "co-x", "+14045550199", "x"))
        self.assertEqual(StandIn.calls, [])

    def test_a_clients_number_is_never_texted_even_on_a_members_account(self):
        agree(self.user["id"], "+14045550199")   # the client's phone, typed on an account
        with self.ready():
            self.assertIn("client", sms.send_to_member(self.state, "co-x", self.user["id"], "x"))
        self.assertEqual(StandIn.calls, [])

    def test_no_code_path_takes_a_number(self):
        """The one way to send takes a user id; only sms.py can reach the part that dials."""
        self.assertEqual(list(inspect.signature(sms.send_to_member).parameters), ["state", "company_id", "user_id", "body"])
        public = [n for n, f in vars(sms).items() if inspect.isfunction(f) and f.__module__ == "sms" and not n.startswith("_")]
        for name in public:
            params = inspect.signature(getattr(sms, name)).parameters
            if name not in {"e164", "allowed", "opt_out", "client_numbers", "is_stop", "is_keyword"}:
                self.assertFalse({"to", "phone", "number"} & set(params), name)
        for path in [*ROOT.glob("*.py"), *ROOT.glob("domain/*.py")]:
            source = path.read_text()
            if path.name != "sms.py":
                self.assertNotIn("/Messages.json", source, path.name)
            for node in ast.walk(ast.parse(source)):
                if path.name != "sms.py" and isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "sms":
                    self.assertFalse(node.attr.startswith("_"), f"{path.name} reaches into sms.{node.attr}")

    def test_the_allow_list_holds_texts(self):
        agree(self.user["id"], "+14045550123")
        with self.ready(SMS_ALLOWLIST="(404) 555-0777"):
            self.assertEqual(sms.send_to_member(self.state, "co-x", self.user["id"], "x"), sms.NOT_ALLOWED)
        with self.ready(SMS_ALLOWLIST="(404) 555-0123"):
            self.assertEqual(sms.send_to_member(self.state, "co-x", self.user["id"], "x"), "sent")
        self.assertEqual(len(StandIn.calls), 1)

    def test_a_mail_only_list_or_staging_means_no_texts(self):
        agree(self.user["id"], "+14045550123")
        for extra in ({"MAIL_ALLOWLIST": "@team.example"}, {"DURACASH_ENV": "staging"}):
            with self.ready(**extra):
                self.assertEqual(sms.send_to_member(self.state, "co-x", self.user["id"], "x"), sms.NOT_ALLOWED)
        self.assertEqual(StandIn.calls, [])

    def test_daily_limit(self):
        agree(self.user["id"], "+14045550123")
        with self.ready(SMS_DAILY_LIMIT="1"):
            self.assertEqual(sms.send_to_member(self.state, "co-x", self.user["id"], "x"), "sent")
            self.assertIn("daily limit", sms.send_to_member(self.state, "co-x", self.user["id"], "x"))
        self.assertEqual(len(StandIn.calls), 1)

    def test_a_failure_is_logged_and_returned_never_raised(self):
        agree(self.user["id"], "+14045550123")
        StandIn.answer = (400, {"code": 30034, "message": "unregistered"})
        with self.ready(), mock.patch("sys.stderr") as log:
            result = sms.send_to_member(self.state, "co-x", self.user["id"], "x")
        self.assertIn("30034", result)
        self.assertNotIn("+14045550123", "".join(str(c) for c in log.write.call_args_list), "no number in the log")
        with env(TWILIO_ACCOUNT_SID="A", TWILIO_AUTH_TOKEN="t", TWILIO_NUMBER="+14045550100", TWILIO_API_BASE="http://127.0.0.1:9"), mock.patch("sys.stderr"):
            self.assertIn("not sent", sms.send_to_member(self.state, "co-x", self.user["id"], "x"))

    def test_twilio_saying_unsubscribed_turns_texts_off_here(self):
        agree(self.user["id"], "+14045550123")
        StandIn.answer = (400, {"code": 21610, "message": "unsubscribed"})
        with self.ready(), mock.patch("sys.stderr"):
            sms.send_to_member(self.state, "co-x", self.user["id"], "x")
        self.assertFalse(next(u for u in auth.all_users() if u["id"] == self.user["id"]).get("sms_opt_in"))

    def test_not_set_up_sends_nothing(self):
        agree(self.user["id"], "+14045550123")
        with env():
            self.assertEqual(sms.send_to_member(self.state, "co-x", self.user["id"], "x"), sms.NOT_SET_UP)

    def test_demo_company_never_texts(self):
        with self.ready():
            self.assertEqual(sms.send_to_member(self.state, auth.DEMO_COMPANY_ID, self.user["id"], "x"), sms.DEMO)

    def test_stop_keywords(self):
        for word in ("STOP", "stop ", "Unsubscribe", "END", "quit", "STOPALL", "CANCEL", "REVOKE", "OPTOUT"):
            self.assertTrue(sms.is_stop(word), word)
        for text in ("stop by the Oak job", "", "please cancel"):
            self.assertFalse(sms.is_stop(text), text)

    def test_stop_clears_every_account_with_that_number(self):
        other = auth.create_user("sam@example.com", "correct horse battery", "Sam", "co-y")
        agree(self.user["id"], "+14045550123")
        agree(other["id"], "+14045550123")
        self.assertEqual(sms.opt_out("+1 404 555 0123"), 2)
        for person in auth.all_users():
            self.assertFalse(person.get("sms_opt_in"))

    def test_phones_stay_out_of_team_lists(self):
        agree(self.user["id"], "+14045550123")
        self.assertNotIn("+14045550123", json.dumps(auth.team("co-x")))
        self.assertNotIn("+14045550123", json.dumps(auth.all_users()))

    def test_changing_the_number_needs_agreeing_again(self):
        agree(self.user["id"], "+14045550123")
        got = auth.set_sms(self.user["id"], "+14045550124", False)
        self.assertFalse(got["sms_opt_in"])
        got = auth.set_sms(self.user["id"], "", True)
        self.assertFalse(got["sms_opt_in"])
        self.assertEqual(got["phone"], "")


if __name__ == "__main__":
    unittest.main()
