"""Whose number a text can go to (Decision 7): a client's number in any form is never texted, and a
number takes texts only after a code texted to it comes back. A local stand-in plays Twilio."""

import http.client
import inspect
import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth
import importer
import server
import sms
import storage
from test_comms_limits import SmsBase, StandIn, env


def agree(user_id, phone):
    """A person who has answered the code texted to this number."""
    auth._CODES.pop(f"sms-to:{phone}", None)
    auth.confirm_sms_code(user_id, auth.issue_sms_code(user_id, phone))


class ClientNumbers(unittest.TestCase):
    def numbers(self, phone):
        return sms.client_numbers({"payers": [{"id": "p", "phone": phone}], "jobs": []})

    def test_every_way_a_clients_number_is_written_counts(self):
        for written in ("(404) 555-0142", "(404) 555-0142 x12", "404-555-0142 ext. 12", "+1 404 555 0142", "1.404.555.0142",
                        "4045550142", "Cell 404-555-0142, ask for Al", "404–555–0142", "(404) 555-٠١٤٢"):
            self.assertIn("+14045550142", self.numbers(written), written)

    def test_two_numbers_in_one_box_both_count(self):
        self.assertEqual(self.numbers("404-555-0142 / 678-555-0100"), {"+14045550142", "+16785550100"})

    def test_a_number_on_a_job_or_a_contact_counts(self):
        state = {"payers": [{"id": "p", "contacts": [{"phone": "404-555-0101 x9"}], "playbook": {"ap_phone": "404-555-0102"}}],
                 "jobs": [{"id": "j", "client_phone": "404-555-0103 x1", "contacts": [{"phone": "404-555-0104"}]}]}
        self.assertEqual(sms.client_numbers(state), {f"+1404555010{n}" for n in (1, 2, 3, 4)})

    def test_what_is_not_a_phone_number_adds_nothing(self):
        self.assertEqual(self.numbers(""), set())
        self.assertEqual(self.numbers("ask the office"), set())
        self.assertEqual(self.numbers("555-0142"), set())

    def test_other_digit_scripts_are_not_a_number_to_text(self):
        for typed in ("404555٠١٧٧", "٤٠٤ 555 0177", "404555０１７７"):
            self.assertEqual(sms.e164(typed), "", typed)


class SendTime(SmsBase):
    def test_a_client_added_or_edited_after_the_person_agreed_is_not_texted(self):
        agree(self.user["id"], "+14045550142")
        with self.ready():
            self.assertEqual(sms.send_to_member(self.state, "co-x", self.user["id"], "x"), "sent")
            self.state["payers"].append({"id": "p2", "name": "New client", "phone": "(404) 555-0142 x12"})
            self.assertIn("client", sms.send_to_member(self.state, "co-x", self.user["id"], "x"))
            self.state["payers"].pop()
            self.state["payers"][0]["phone"] = "404.555.0142"
            self.assertIn("client", sms.send_to_member(self.state, "co-x", self.user["id"], "x"))
        self.assertEqual(len(StandIn.calls), 1)

    def test_agreeing_without_a_code_does_not_take_effect(self):
        got = auth.set_sms(self.user["id"], "+14045550123", True)
        self.assertFalse(got["sms_opt_in"])
        with self.ready():
            self.assertIn("not agreed", sms.send_to_member(self.state, "co-x", self.user["id"], "x"))
        self.assertEqual(StandIn.calls, [])

    def test_an_account_that_agreed_before_codes_existed_has_to_confirm(self):
        data = auth._load()
        user = next(u for u in data["users"] if u["id"] == self.user["id"])
        user.update(phone="+14045550123", sms_opt_in=True)
        auth._save(data)
        self.assertFalse(auth.session_public(self.user["id"])["sms_opt_in"])
        with self.ready():
            self.assertIn("not agreed", sms.send_to_member(self.state, "co-x", self.user["id"], "x"))

    def test_a_read_only_company_uses_up_no_texts(self):
        agree(self.user["id"], "+14045550123")
        self.state["company"] = {"read_only": "the account is closed"}
        with self.ready():
            self.assertIn("read-only", sms.send_to_member(self.state, "co-x", self.user["id"], "x"))
        self.assertEqual(StandIn.calls, [])


class CodeRules(SmsBase):
    def send_code(self, phone="+14045550123"):
        code = auth.issue_sms_code(self.user["id"], phone)
        return code, sms.send_code(self.state, "co-x", self.user["id"], code)

    def test_the_code_goes_to_the_number_and_confirming_turns_texts_on(self):
        with self.ready():
            code, result = self.send_code()
        self.assertEqual(result, "sent")
        self.assertEqual(StandIn.calls[0]["form"]["To"], "+14045550123")
        self.assertIn(code, StandIn.calls[0]["form"]["Body"])
        self.assertFalse(auth.session_public(self.user["id"])["sms_opt_in"], "nothing until the code comes back")
        got = auth.confirm_sms_code(self.user["id"], code)
        self.assertEqual((got["phone"], got["sms_opt_in"]), ("+14045550123", True))
        with self.assertRaises(auth.AuthError):
            auth.confirm_sms_code(self.user["id"], code)   # once only

    def test_only_a_hash_of_the_code_is_kept(self):
        code = auth.issue_sms_code(self.user["id"], "+14045550123")
        self.assertNotIn(code, json.dumps(auth._load()["tokens"]))

    def test_a_code_only_proves_the_number_it_went_to(self):
        code = auth.issue_sms_code(self.user["id"], "+14045550123")
        auth.set_sms(self.user["id"], "+14045550199", False)   # another number typed since
        self.assertEqual(auth.confirm_sms_code(self.user["id"], code)["phone"], "+14045550123")
        self.assertFalse(auth.sms_proven(self.user["id"], "+14045550199"))

    def test_a_new_code_replaces_the_last(self):
        old = auth.issue_sms_code(self.user["id"], "+14045550123")
        new = auth.issue_sms_code(self.user["id"], "+14045550124")
        with self.assertRaises(auth.AuthError):
            auth.confirm_sms_code(self.user["id"], old)
        self.assertEqual(auth.confirm_sms_code(self.user["id"], new)["phone"], "+14045550124")

    def test_codes_expire(self):
        code = auth.issue_sms_code(self.user["id"], "+14045550123")
        data = auth._load()
        for token in data["tokens"].values():
            token["expires_at"] = "2000-01-01T00:00:00+00:00"
        auth._save(data)
        with self.assertRaises(auth.AuthError):
            auth.confirm_sms_code(self.user["id"], code)

    def test_wrong_tries_are_limited(self):
        code = auth.issue_sms_code(self.user["id"], "+14045550123")
        wrong = "000000" if code != "000000" else "000001"
        for _ in range(auth.CODE_MAX_WRONG):
            with self.assertRaises(auth.AuthError):
                auth.confirm_sms_code(self.user["id"], wrong)
        with self.assertRaises(auth.AuthError):
            auth.confirm_sms_code(self.user["id"], code)   # dropped: a new one has to be asked for

    def test_codes_an_hour_are_limited_per_person_and_per_number(self):
        codes = [auth.issue_sms_code(self.user["id"], f"+1404555{1000 + n}") for n in range(auth.CODES_MAX_PER_HOUR + 1)]
        self.assertTrue(all(codes[:-1]))
        self.assertIsNone(codes[-1])
        other = auth.create_user("sam@example.com", "correct horse battery", "Sam", "co-y")
        third = auth.create_user("kit@example.com", "correct horse battery", "Kit", "co-z")
        for n in range(auth.CODES_MAX_PER_HOUR):
            who = other if n % 2 else third
            self.assertTrue(auth.issue_sms_code(who["id"], "+14045552222"))
        self.assertIsNone(auth.issue_sms_code(auth.create_user("lee@example.com", "correct horse battery", "Lee", "co-w")["id"], "+14045552222"))

    def test_a_code_never_goes_to_a_clients_number(self):
        with self.ready():
            _, result = self.send_code("+14045550199")
        self.assertIn("client", result)
        self.assertEqual(StandIn.calls, [])

    def test_a_code_respects_the_allow_list(self):
        with self.ready(SMS_ALLOWLIST="(404) 555-0777"):
            _, result = self.send_code()
        self.assertEqual(result, sms.NOT_ALLOWED)
        with self.ready(DURACASH_ENV="staging"):
            _, result = self.send_code()
        self.assertEqual(result, sms.NOT_ALLOWED)
        self.assertEqual(StandIn.calls, [])

    def test_a_code_counts_toward_the_daily_limit(self):
        with self.ready(SMS_DAILY_LIMIT="1"):
            self.assertEqual(self.send_code()[1], "sent")
            self.assertIn("daily limit", self.send_code()[1])
        self.assertEqual(len(StandIn.calls), 1)

    def test_a_used_up_or_made_up_code_sends_nothing(self):
        with self.ready():
            self.assertNotEqual(sms.send_code(self.state, "co-x", self.user["id"], "123456"), "sent")
        self.assertEqual(StandIn.calls, [])

    def test_changing_the_number_means_proving_the_new_one(self):
        agree(self.user["id"], "+14045550123")
        self.assertFalse(auth.set_sms(self.user["id"], "+14045550124", True)["sms_opt_in"])
        self.assertFalse(auth.set_sms(self.user["id"], "+14045550123", True)["sms_opt_in"], "going back needs a code too")

    def test_stop_then_ticking_the_box_again_stays_stopped_until_a_code_gets_through(self):
        agree(self.user["id"], "+14045550123")
        auth.sms_stop("+14045550123")
        self.assertFalse(auth.session_public(self.user["id"])["sms_opt_in"])
        shown = auth.set_sms(self.user["id"], "+14045550123", True)
        self.assertEqual((shown["sms_opt_in"], shown["sms_stopped"]), (False, True))
        self.assertFalse(auth.sms_proven(self.user["id"], "+14045550123"))
        auth._CODES.pop("sms-to:+14045550123", None)
        shown = auth.confirm_sms_code(self.user["id"], auth.issue_sms_code(self.user["id"], "+14045550123"))
        self.assertEqual((shown["sms_opt_in"], shown["sms_stopped"]), (True, False))

    def test_someone_who_agreed_before_codes_is_told_to_confirm(self):
        data = auth._load()
        user = next(u for u in data["users"] if u["id"] == self.user["id"])
        user.update(phone="+14045550123", sms_opt_in=True)
        auth._save(data)
        shown = auth.session_public(self.user["id"])
        self.assertEqual((shown["sms_opt_in"], shown["sms_needs_confirming"]), (False, True))
        self.assertFalse(auth.set_sms(self.user["id"], "+14045550123", False)["sms_needs_confirming"])


class Limit(SmsBase):
    def test_concurrent_sends_can_not_pass_the_limit(self):
        agree(self.user["id"], "+14045550123")
        results = []
        barrier = threading.Barrier(8)

        def send():
            barrier.wait()
            results.append(sms.send_to_member(self.state, "co-x", self.user["id"], "x"))

        with self.ready(SMS_DAILY_LIMIT="2"):
            threads = [threading.Thread(target=send) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(results.count("sent"), 2, results)
        self.assertEqual(len(StandIn.calls), 2)

    def test_a_send_twilio_refused_gives_its_place_back(self):
        agree(self.user["id"], "+14045550123")
        StandIn.answer = (400, {"code": 30034})
        with self.ready(SMS_DAILY_LIMIT="1"), mock.patch("sys.stderr"):
            self.assertIn("30034", sms.send_to_member(self.state, "co-x", self.user["id"], "x"))
        StandIn.answer = (201, {"sid": "SM1"})
        with self.ready(SMS_DAILY_LIMIT="1"):
            self.assertEqual(sms.send_to_member(self.state, "co-x", self.user["id"], "x"), "sent")

    def test_a_send_that_may_have_gone_keeps_its_place(self):
        agree(self.user["id"], "+14045550123")
        with env(TWILIO_ACCOUNT_SID="A", TWILIO_AUTH_TOKEN="t", TWILIO_NUMBER="+14045550100", TWILIO_API_BASE="http://127.0.0.1:9", SMS_DAILY_LIMIT="1"), mock.patch("sys.stderr"):
            self.assertIn("reach", sms.send_to_member(self.state, "co-x", self.user["id"], "x"))
        with self.ready(SMS_DAILY_LIMIT="1"):
            self.assertIn("daily limit", sms.send_to_member(self.state, "co-x", self.user["id"], "x"))

    def bad_counter(self, content):
        path = Path(storage.DATA_DIR) / "sms_sends.json"
        path.write_text(content)
        return path

    def test_a_damaged_counter_stops_texts_and_is_kept(self):
        agree(self.user["id"], "+14045550123")
        for content in ('{"co-x": ["2026', "[]", '{"co-x": 5}'):
            path = self.bad_counter(content)
            with self.ready(), mock.patch("sys.stderr") as log, mock.patch("alerts.notify") as alert:
                self.assertEqual(sms.send_to_member(self.state, "co-x", self.user["id"], "x"), sms.COUNTER_BROKEN, content)
            self.assertTrue(alert.called)
            self.assertIn("count of texts", "".join(str(c) for c in log.write.call_args_list))
            self.assertEqual(path.read_text(), content, "left in place")
            self.assertTrue([p for p in path.parent.glob("sms_sends.json.bad-*") if p.read_text() == content], "a copy kept aside")
        self.assertEqual(StandIn.calls, [])

    def test_a_counter_that_can_not_be_written_stops_texts(self):
        agree(self.user["id"], "+14045550123")
        with self.ready(), mock.patch("sys.stderr"), mock.patch.object(storage, "write_json", side_effect=OSError("read-only disk")):
            self.assertEqual(sms.send_to_member(self.state, "co-x", self.user["id"], "x"), sms.COUNTER_BROKEN)
        self.assertEqual(StandIn.calls, [])

    def test_forgetting_a_company_survives_a_damaged_counter(self):
        self.bad_counter("{")
        with mock.patch("sys.stderr"):
            sms.forget_company("co-x")


class Http(SmsBase):
    """My account's whole path, against the running server and the stand-in."""

    def setUp(self):
        super().setUp()
        root = Path(self.tmp.name)
        for target, name, value in ((server, "COMPANIES", root / "companies"), (importer, "UPLOADS", root / "uploads")):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.httpd2 = server.Server(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.httpd2.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd2.server_close)
        self.addCleanup(self.httpd2.shutdown)
        auth._FAILURES.clear(); auth._IP_FAILURES.clear()
        res, _ = self.request("POST", "/api/auth/signup", {"accept_terms": True, "email": "http@example.com", "password": "correct horse battery", "name": "Pat", "company_name": "Pine"})
        self.cookie = res.getheader("Set-Cookie").split(";")[0]
        self.me = next(u for u in auth.all_users() if u["email"] == "http@example.com")

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd2.server_address[1], timeout=10)
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if getattr(self, "cookie", None):
            headers["Cookie"] = self.cookie
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        return res, json.loads(raw or b"{}")

    def code_texted(self):
        return re.search(r"[0-9]{6}", StandIn.calls[-1]["form"]["Body"]).group(0)

    def test_agree_then_answer_the_code(self):
        with self.ready():
            res, data = self.request("POST", "/api/auth/sms", {"phone": "(404) 555-0123", "sms_opt_in": True})
            self.assertEqual(res.status, 200, data)
            self.assertEqual((data["code_sent"], data["sent_to"]), (True, "+14045550123"))
            self.assertFalse(data["user"]["sms_opt_in"])
            self.assertEqual(data["user"]["phone"], "", "the old number stands until the new one is proven")
            res, _ = self.request("POST", "/api/reminders/prefs", {"prefs": {"missing": ["text"]}})
            self.assertEqual(res.status, 409, "no text reminders yet")
            res, data = self.request("POST", "/api/auth/sms/confirm", {"code": "not it"})
            self.assertEqual(res.status, 409)
            self.assertIn("sms_code", data["fields"])
            res, data = self.request("POST", "/api/auth/sms/confirm", {"code": self.code_texted()})
            self.assertEqual(res.status, 200, data)
            self.assertEqual((data["user"]["phone"], data["user"]["sms_opt_in"]), ("+14045550123", True))
            res, _ = self.request("POST", "/api/reminders/prefs", {"prefs": {"missing": ["text"]}})
            self.assertEqual(res.status, 200)

    def test_a_client_given_the_number_before_the_code_comes_back(self):
        with self.ready():
            self.request("POST", "/api/auth/sms", {"phone": "(404) 555-0142", "sms_opt_in": True})
            code = self.code_texted()
            res, data = self.request("POST", "/api/jobs", {"name": "Birch deck", "address": "5 Birch Rd", "customer": "Northline GC", "role": "GC", "approver": "Nia", "phone": "(404) 555-0142 x12",
                                                           "ap_email": "ap@northline.example", "contract_cents": 2000000})
            self.assertEqual(res.status, 200, data)
            res, data = self.request("POST", "/api/auth/sms/confirm", {"code": code})
            self.assertEqual(res.status, 409)
            self.assertIn("client", data["fields"]["sms_code"])
        self.assertFalse(auth.session_public(self.me["id"])["sms_opt_in"])

    def test_no_code_on_staging_without_the_allow_list(self):
        with self.ready(DURACASH_ENV="staging"):
            res, data = self.request("POST", "/api/auth/sms", {"phone": "(404) 555-0123", "sms_opt_in": True})
        self.assertEqual(res.status, 409)
        self.assertIn("allow-list", data["fields"]["phone"])
        self.assertEqual(StandIn.calls, [])

    def test_saving_a_number_with_no_agreement_needs_no_code(self):
        with self.ready():
            res, data = self.request("POST", "/api/auth/sms", {"phone": "(404) 555-0123", "sms_opt_in": False})
        self.assertEqual((res.status, data["user"]["phone"], data["user"]["sms_opt_in"]), (200, "+14045550123", False))
        self.assertEqual(StandIn.calls, [])

    def test_one_more_code_than_the_limit_is_refused(self):
        with self.ready():
            for _ in range(auth.CODES_MAX_PER_HOUR):
                self.assertEqual(self.request("POST", "/api/auth/sms", {"phone": "(404) 555-0123", "sms_opt_in": True})[0].status, 200)
            res, data = self.request("POST", "/api/auth/sms", {"phone": "(404) 555-0123", "sms_opt_in": True})
        self.assertEqual(res.status, 429)
        self.assertEqual(len(StandIn.calls), auth.CODES_MAX_PER_HOUR)


class CodeTroubles(Http):
    """What a person meets asking for a code: a limit that keeps the newest one live, and a number
    that replied STOP, told to text START first."""

    def ask(self, phone):
        return self.request("POST", "/api/auth/sms", {"phone": phone, "sms_opt_in": True})

    def test_over_the_limit_on_a_new_number_the_newest_code_still_works(self):
        with self.ready():
            for _ in range(auth.CODES_MAX_PER_HOUR):
                self.ask("(404) 555-0123")
            code = self.code_texted()
            res, data = self.ask("(404) 555-0124")
            self.assertEqual(res.status, 429)
            self.assertIn("none was sent to (404) 555-0124", data["error"])
            self.assertIn("(404) 555-0123", data["error"], "says which number the code on the screen went to")
            self.assertIn("Use the newest one", data["error"])
            res, data = self.request("POST", "/api/auth/sms/confirm", {"code": code})
            self.assertEqual(res.status, 200, data)

    def test_a_number_that_replied_stop_is_told_to_text_start_first(self):
        StandIn.answer = (400, {"code": 21610, "message": "unsubscribed"})
        with self.ready():
            res, data = self.ask("(404) 555-0130")
        self.assertEqual(res.status, 409)
        self.assertIn("Text START to (404) 555-0100 first", data["fields"]["phone"])

    def test_stop_then_ticking_the_box_shows_stopped_with_the_start_line(self):
        with self.ready():
            self.ask("(404) 555-0123")
            self.request("POST", "/api/auth/sms/confirm", {"code": self.code_texted()})
            auth.sms_stop("+14045550123")
            StandIn.answer = (400, {"code": 21610, "message": "unsubscribed"})
            res, data = self.ask("(404) 555-0123")
            self.assertEqual(res.status, 409)
            self.assertIn("Text START", data["fields"]["phone"])
            res, me = self.request("GET", "/api/auth/me")
            self.assertEqual((me["user"]["sms_opt_in"], me["user"]["sms_stopped"], me["user"]["texts_number"]), (False, True, "+14045550100"))
            StandIn.answer = (201, {"sid": "SM1", "status": "queued"})   # they texted START
            res, data = self.ask("(404) 555-0123")
            self.assertTrue(data["code_sent"])
            res, data = self.request("POST", "/api/auth/sms/confirm", {"code": self.code_texted()})
            self.assertEqual((data["user"]["sms_opt_in"], data["user"]["sms_stopped"]), (True, False))


class Inbound(Http):
    def post_text(self, env_name):
        token = auth.session_public(self.me["id"])
        res, settings = self.request("GET", "/api/import/settings")
        path = "/api/inbound/sms/" + settings["sms_webhook"].split("/")[-1]
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd2.server_address[1], timeout=10)
        with env(DURACASH_ENV=env_name, DURACASH_URL="http://127.0.0.1"):
            conn.request("POST", path, body="From=%2B14045550123&Body=hello", headers={"Content-Type": "application/x-www-form-urlencoded"})
            res = conn.getresponse()
            res.read()
        conn.close()
        return res.status

    def test_unsigned_texts_are_refused_on_staging_and_allowed_only_for_local_development(self):
        self.assertEqual(self.post_text("staging"), 403)
        self.assertEqual(self.post_text("preview"), 403)
        self.assertEqual(self.post_text("development"), 200)
        self.assertEqual(self.post_text(""), 200)


class NoNumberFromACaller(unittest.TestCase):
    def test_send_code_takes_a_code_not_a_number(self):
        self.assertEqual(list(inspect.signature(sms.send_code).parameters), ["state", "company_id", "user_id", "code"])


if __name__ == "__main__":
    unittest.main()
