"""Compliance documents and Georgia lien deadlines (compliance.py, liens.py; Siteline comparison
items 7 and 8). The date math is checked by hand against O.C.G.A. § 1-3-1(d)(3)'s counting."""

import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import compliance
import domain
import server
from domain import profiles
import importer
import liens
import reminders
from test_accounts import ServerCase

TODAY = date(2026, 11, 27)
D = date.fromisoformat


def job(**extra):
    return {"id": "j1", "name": "Oak", "code": "OAK", "payer_id": "p1", "invoices": [], "payments": [], "change_orders": [], "records": [],
            "contract_cents": 100_000, "daily_records": [], "assignee_id": "", "address": "1 Oak St, Atlanta, GA 30301", **extra}


def company(client_type="gc", **extra):
    return {"company": {"name": "Pine"}, "jobs": [job()], "audit": [], "files": [],
            "payers": [{"id": "p1", "name": "Northline", "ap_email": "ap@n.example", "phone": "(404) 555-0100", "approver": "Sam", "client_type": client_type},
                       {"id": "p2", "name": "Redline", "ap_email": "ap@r.example", "phone": "(404) 555-0101", "approver": "Ray", "client_type": "gc"}],
            "daily_records": [], "inbox": [], "tasks": [], "task_assignments": {}, **extra}


class Holidays(unittest.TestCase):
    def test_federal_holidays_and_observed_days(self):
        h = liens.us_federal_holidays(2026)
        for d in ("2026-01-01", "2026-01-19", "2026-02-16", "2026-05-25", "2026-06-19", "2026-07-03", "2026-07-04",
                  "2026-09-07", "2026-10-12", "2026-11-11", "2026-11-26", "2026-12-25"):
            self.assertIn(D(d), h, d)
        self.assertNotIn(D("2026-11-27"), h, "the day after Thanksgiving is the Governor's to add, not a federal holiday")
        self.assertIn(D("2021-12-31"), liens.us_federal_holidays(2021), "New Year's Day 2022 fell on a Saturday")
        self.assertNotIn(D("2020-06-19"), liens.us_federal_holidays(2020), "Juneteenth is federal from 2021")

    def test_business_day(self):
        self.assertTrue(liens.is_business_day(D("2026-11-25")))
        self.assertFalse(liens.is_business_day(D("2026-11-26")))
        self.assertFalse(liens.is_business_day(D("2026-11-28")))


class Counting(unittest.TestCase):
    def test_first_day_not_counted_last_day_counted(self):
        c = liens.count(D("2026-10-01"), {"days": 90})
        self.assertEqual((c["counted"], c["last_day"], c["file_by"], c["moved"]), (D("2026-12-30"),) * 3 + (False,))

    def test_a_saturday_runs_through_monday_and_the_safe_day_is_before(self):
        # Sep 27 + 90 = Sat Dec 26, 2026: through Mon Dec 28; safer by Thu Dec 24 (Fri Dec 25 is Christmas).
        c = liens.count(D("2026-09-27"), {"days": 90})
        self.assertEqual(c["counted"], D("2026-12-26"))
        self.assertEqual(c["last_day"], D("2026-12-28"))
        self.assertEqual(c["file_by"], D("2026-12-24"))
        self.assertTrue(c["moved"])

    def test_a_holiday_runs_through_the_next_business_day(self):
        c = liens.count(D("2026-10-27"), {"days": 30})   # Thanksgiving, Thu Nov 26
        self.assertEqual((c["last_day"], c["file_by"]), (D("2026-11-27"), D("2026-11-25")))

    def test_a_sunday_before_a_monday_holiday(self):
        # Sun Jan 18, 2026, then MLK Day: through Tue Jan 20.
        c = liens.count(D("2025-10-20"), {"days": 90})
        self.assertEqual(c["counted"], D("2026-01-18"))
        self.assertEqual(c["last_day"], D("2026-01-20"))
        self.assertEqual(c["file_by"], D("2026-01-16"))

    def test_business_days_skip_weekends_and_holidays(self):
        # Filed Thu Jul 2, 2026: Fri Jul 3 is Independence Day observed, then the weekend.
        self.assertEqual(liens.count(D("2026-07-02"), {"business_days": 2})["last_day"], D("2026-07-07"))
        self.assertEqual(liens.count(D("2026-11-23"), {"business_days": 2})["last_day"], D("2026-11-25"))

    def test_365_days_and_a_leap_year(self):
        self.assertEqual(liens.count(D("2027-01-04"), {"days": 365})["counted"], D("2028-01-04"))
        self.assertEqual(liens.count(D("2028-01-04"), {"days": 365})["counted"], D("2029-01-03"))


def rows(picture):
    return {r["key"]: r for r in picture["deadlines"]}


class GeorgiaDeadlines(unittest.TestCase):
    def picture(self, state, today=TODAY):
        return compliance.job_liens(state, state["jobs"][0], today)

    def test_the_rules_are_data_with_only_georgia_filled(self):
        self.assertEqual(set(liens.STATE_RULES), {"GA"})
        keys = [d["key"] for d in liens.STATE_RULES["GA"]["deadlines"]]
        self.assertEqual(keys, ["preliminary_notice", "notice_to_contractor", "lien_claim", "lien_copy_owner", "lien_action",
                                "contest_action", "action_notice", "affidavit_nonpayment"])
        for d in liens.STATE_RULES["GA"]["deadlines"]:
            self.assertTrue(d["cite"].startswith("O.C.G.A. §"), d)
        self.assertIsNone(liens.rules_for("FL"))

    def test_a_job_outside_georgia_has_no_dates(self):
        state = company()
        state["jobs"][0]["address"] = "1 Oak St, Rochester, NY 14604"
        p = self.picture(state)
        self.assertEqual((p["state"], p["supported"], p["deadlines"]), ("NY", False, []))

    def test_last_furnishing_comes_from_the_latest_job_record(self):
        state = company()
        state["daily_records"] = [{"id": "d1", "job_id": "j1", "at": "2026-10-01T12:00:00+00:00"}, {"id": "d2", "job_id": "j1", "at": "2026-11-02T12:00:00+00:00"},
                                  {"id": "d3", "job_id": "other", "at": "2026-11-20T12:00:00+00:00"}]
        state["jobs"][0]["started_on"] = "2026-09-15"
        state["jobs"][0]["invoices"] = [{"id": "i1", "number": "PA-1", "status": "sent", "amount_cents": 50_000}]
        p = self.picture(state)
        self.assertEqual((p["inputs"]["first_furnished_on"], p["inputs"]["last_furnished_on"], p["last_from"]), ("2026-09-15", "2026-11-02", "job_record"))
        claim = rows(p)["lien_claim"]
        self.assertEqual((claim["counted"], claim["last_day"], claim["file_by"]), ("2027-01-31", "2027-02-01", "2027-01-29"))   # Sun Jan 31
        self.assertEqual(claim["status"], "upcoming")
        self.assertIn("still going on", claim["note"])
        # A date typed in stands over the records.
        compliance.set_job_liens(state, "j1", {"last_furnished_on": "2026-10-20"}, "Pat")
        p = self.picture(state)
        self.assertEqual((p["inputs"]["last_furnished_on"], p["last_from"], rows(p)["lien_claim"]["last_day"]), ("2026-10-20", "saved", "2027-01-19"))
        # Oct 20 + 90 is MLK Day 2027, Mon Jan 18: the last day is Tue Jan 19, safer by Fri Jan 15.
        self.assertEqual((rows(p)["lien_claim"]["counted"], rows(p)["lien_claim"]["file_by"]), ("2027-01-18", "2027-01-15"))

    def test_tier_from_the_client_and_the_notice_to_contractor(self):
        state = company("gc")
        state["jobs"][0]["started_on"] = "2026-10-01"
        ntc = rows(self.picture(state))["notice_to_contractor"]
        self.assertEqual(ntc["status"], "not_needed")
        state = company("sub")   # a lower tier: in privity with neither owner nor contractor
        state["jobs"][0]["started_on"] = "2026-10-01"
        p = self.picture(state, D("2026-10-05"))
        self.assertEqual((p["tier"], p["tier_from"]), ("lower", "client"))
        ntc = rows(p)["notice_to_contractor"]
        self.assertEqual(ntc["status"], "not_needed")
        self.assertIn("Notice of Commencement", ntc["note"])
        compliance.set_job_liens(state, "j1", {"noc_filed_on": "2026-10-10"}, "Pat")
        ntc = rows(self.picture(state, D("2026-10-12")))["notice_to_contractor"]
        # The later of the NOC filing (Oct 10) and first furnishing (Oct 1), plus 30: Mon Nov 9.
        self.assertEqual((ntc["from_on"], ntc["last_day"], ntc["status"]), ("2026-10-10", "2026-11-09", "due_soon"))
        compliance.set_job_liens(state, "j1", {"noc_filed_on": "2026-09-01"}, "Pat")
        self.assertEqual(rows(self.picture(state, D("2026-10-12")))["notice_to_contractor"]["from_on"], "2026-10-01")
        # A saved answer stands over the client's type.
        compliance.set_job_liens(state, "j1", {"tier": "owner", "noc_filed_on": "2026-10-10"}, "Pat")
        self.assertEqual(rows(self.picture(state))["notice_to_contractor"]["status"], "not_needed")

    def test_after_the_lien_is_filed(self):
        state = company()
        compliance.set_job_liens(state, "j1", {"last_furnished_on": "2026-09-01", "done": {"lien_claim": "2026-11-20"}}, "Pat")
        r = rows(self.picture(state))
        self.assertEqual(r["lien_claim"]["status"], "done")
        self.assertEqual(r["lien_copy_owner"]["last_day"], "2026-11-24")   # Fri Nov 20 filing: Mon, Tue
        self.assertEqual(r["lien_action"]["counted"], "2027-11-20")       # Saturday
        self.assertEqual(r["lien_action"]["last_day"], "2027-11-22")
        self.assertEqual(r["action_notice"]["status"], "waiting")
        compliance.set_job_liens(state, "j1", {"last_furnished_on": "2026-09-01", "done": {"lien_claim": "2026-11-20", "lien_action": "2027-03-01"}}, "Pat")
        self.assertEqual(rows(self.picture(state))["action_notice"]["last_day"], "2027-03-31")

    def test_contest_affidavit_skipped_and_passed(self):
        state = company()
        compliance.set_job_liens(state, "j1", {"contest_received_on": "2026-11-02", "waiver_signed_on": "2026-09-01",
                                               "first_furnished_on": "2026-01-02", "skipped": ["preliminary_notice"]}, "Pat")
        r = rows(self.picture(state))
        self.assertEqual(r["contest_action"]["last_day"], "2027-01-04")   # Fri Jan 1, 2027: Mon Jan 4 (New Year's)
        self.assertEqual(r["affidavit_nonpayment"]["counted"], "2026-11-30")
        self.assertEqual(r["affidavit_nonpayment"]["status"], "due_soon")
        self.assertEqual(r["preliminary_notice"]["status"], "skipped")
        compliance.set_job_liens(state, "j1", {"first_furnished_on": "2026-01-02", "skipped": []}, "Pat")
        self.assertEqual(rows(self.picture(state))["preliminary_notice"]["status"], "passed")

    def test_a_public_project(self):
        p = self.picture(company("public"))
        self.assertTrue(all(r["status"] == "not_needed" for r in p["deadlines"]))
        self.assertIn("bond", p["deadlines"][0]["note"])

    def test_bad_inputs_are_refused(self):
        state = company()
        for body, field in (({"state": "Georgia"}, "state"), ({"tier": "boss"}, "tier"), ({"noc_filed_on": "10/12/2026"}, "noc_filed_on"),
                            ({"first_furnished_on": "2026-10-02", "last_furnished_on": "2026-10-01"}, "last_furnished_on")):
            with self.assertRaises(domain.GateError) as e:
                compliance.set_job_liens(state, "j1", body, "Pat")
            self.assertIn(field, e.exception.fields)
        with self.assertRaises(domain.GateError):
            compliance.set_job_liens(state, "nope", {}, "Pat")


class Documents(unittest.TestCase):
    def test_no_tax_ids(self):
        state = company()
        for body in ({"type": "license", "number": "123-45-6789"}, {"type": "bond", "number": "12-3456789"},
                     {"type": "other", "note": "EIN on file"}, {"type": "w9", "number": "A1"}, {"type": "payroll", "number": "7"}):
            with self.assertRaises(domain.GateError, msg=body):
                compliance.add_doc(state, body, "Pat")
        doc = compliance.add_doc(state, {"type": "license", "number": "CN210123", "issuer": "Georgia SOS", "expires_on": "2027-06-30"}, "Pat")
        self.assertEqual(doc["number"], "CN210123")

    def test_statuses_and_renewal(self):
        state = company()
        old = compliance.add_doc(state, {"type": "coi", "issuer": "Acme Mutual", "expires_on": "2026-12-10"}, "Pat")
        v = compliance.view(state, TODAY)
        self.assertEqual(v["docs"][0]["status"], "expiring")
        self.assertEqual(v["docs"][0]["days_left"], 13)
        compliance.add_doc(state, {"type": "coi", "issuer": "Acme Mutual", "expires_on": "2027-12-10"}, "Pat")
        v = compliance.view(state, TODAY)
        self.assertEqual({d["id"]: d["status"] for d in v["docs"]}[old["id"]], "replaced")
        self.assertEqual(compliance.doc_status({"expires_on": "2026-11-26"}, TODAY, 30), "expired")
        self.assertEqual(compliance.doc_status({}, TODAY, 30), "no_expiry")

    def test_requirements_and_the_soft_gate(self):
        state = company()
        compliance.add_requirement(state, {"type": "coi", "payer_id": "p1", "additional_insured": True}, "Pat")
        j = state["jobs"][0]
        self.assertIn("none is on file", compliance.payapp_warnings(state, j, TODAY)[0])
        # The company's own COI doesn't name Northline.
        compliance.add_doc(state, {"type": "coi", "expires_on": "2027-01-01"}, "Pat")
        self.assertEqual(len(compliance.payapp_warnings(state, j, TODAY)), 1)
        # One naming another client doesn't either.
        compliance.add_doc(state, {"type": "coi", "payer_id": "p2", "additional_insured": True, "expires_on": "2027-01-01"}, "Pat")
        self.assertEqual(len(compliance.payapp_warnings(state, j, TODAY)), 1)
        compliance.add_doc(state, {"type": "coi", "payer_id": "p1", "additional_insured": True, "expires_on": "2026-11-01"}, "Pat")
        self.assertIn("expired Nov 1, 2026", compliance.payapp_warnings(state, j, TODAY)[0])
        compliance.add_doc(state, {"type": "coi", "payer_id": "p1", "additional_insured": True, "expires_on": "2027-11-01"}, "Pat")
        self.assertEqual(compliance.payapp_warnings(state, j, TODAY), [])
        self.assertEqual(compliance.view(state, TODAY)["requirements"][0]["status"], "met")
        with self.assertRaises(domain.GateError):
            compliance.add_requirement(state, {"type": "coi"}, "Pat")

    def test_remind_days(self):
        state = company()
        self.assertEqual(compliance.remind_days(state), [30, 14, 3])
        self.assertEqual(compliance.set_remind_days(state, {"remind_days": ["7", 60, 7]}, "Pat"), [60, 7])
        for bad in ([], [0], [500], ["x"], [1, 2, 3, 4, 5, 6], "30"):
            with self.assertRaises(domain.GateError):
                compliance.set_remind_days(state, {"remind_days": bad}, "Pat")


class Reminders(unittest.TestCase):
    def texts(self, state, day):
        now = datetime(day.year, day.month, day.day, 15, tzinfo=timezone.utc)
        return {i["key"]: i for i in reminders.build(state, now) if i["kind"] == "compliance"}

    def test_each_reminder_day_comes_round_once(self):
        state = company()
        doc = compliance.add_doc(state, {"type": "coi", "expires_on": "2026-12-27"}, "Pat")
        self.assertEqual(self.texts(state, D("2026-11-20")), {})   # 37 days
        self.assertIn(f"compliance:doc:{doc['id']}:30", self.texts(state, D("2026-11-27")))
        self.assertIn(f"compliance:doc:{doc['id']}:14", self.texts(state, D("2026-12-13")))
        self.assertIn(f"compliance:doc:{doc['id']}:3", self.texts(state, D("2026-12-26")))
        expired = self.texts(state, D("2026-12-28"))[f"compliance:doc:{doc['id']}:expired"]
        self.assertEqual((expired["job"], expired["path"], expired["job_id"]), ("Company documents", "/jobs/compliance", ""))
        compliance.add_doc(state, {"type": "coi", "expires_on": "2027-12-27"}, "Pat")
        self.assertEqual(self.texts(state, D("2026-12-28")), {}, "the renewal answers it")

    def test_requirements_and_lien_dates(self):
        state = company()
        compliance.add_requirement(state, {"type": "w9", "payer_id": "p1"}, "Pat")
        compliance.add_requirement(state, {"type": "w9", "payer_id": "p2"}, "Pat")   # no open job: no reminder
        items = self.texts(state, TODAY)
        self.assertEqual([i["text"] for i in items.values()], ["Northline needs a W-9; none is on file."])
        state["jobs"][0]["invoices"] = [{"id": "i1", "number": "PA-1", "status": "sent", "amount_cents": 50_000}]
        compliance.set_job_liens(state, "j1", {"last_furnished_on": "2026-09-10"}, "Pat")   # Dec 9
        items = self.texts(state, TODAY)
        lien = items["compliance:lien:j1:lien_claim:14"]
        self.assertIn("file by Dec 9", lien["text"])
        self.assertIn("44-14-361.1", lien["text"])
        self.assertIn("attorney", lien["text"])
        state["jobs"][0].update(invoices=[], contract_cents=0)
        self.assertNotIn("compliance:lien:j1:lien_claim:14", self.texts(state, TODAY), "nothing owed, no lien reminder")

    def test_the_new_kind_starts_in_the_app_for_everyone(self):
        self.assertEqual(reminders._chosen({"dates": ["email"]}, "compliance"), ["app"])
        self.assertEqual(reminders._chosen({"compliance": []}, "compliance"), [])
        self.assertEqual(reminders._chosen({}, "dates"), [])


class Http(ServerCase):
    def setUp(self):
        super().setUp()
        self.cookie, _ = self.signup(f"comp{id(self)}@example.com", "Sweetgum")

    def test_documents_files_and_the_sensitive_rule(self):
        c = self.cookie
        res, out = self.request("POST", "/api/compliance/docs", {"type": "w9"}, cookie=c)
        self.assertEqual(res.status, 200, out)
        doc_id = out["doc_id"]
        self.assertEqual(out["compliance"]["docs"][0]["sensitive"], True)
        res, out = self.request("POST", f"/api/compliance/docs/{doc_id}/file", raw=b"%PDF-1.4 w9", cookie=c,
                                headers={"Content-Type": "application/pdf", "X-Filename": "w9.pdf"})
        self.assertEqual(res.status, 200, out)
        file_id = out["compliance"]["docs"][0]["file_id"]
        meta = next(m for m in out["files"] if m["id"] == file_id)
        self.assertEqual((meta["kind"], meta["sensitive"]), ("other", True))
        path = importer.file_path(out["company"].get("id") or self.company_id(c), meta)
        self.assertTrue(path.is_file())
        res, body = self.request("GET", f"/api/compliance/docs/{doc_id}/file", cookie=c)
        self.assertEqual((res.status, body), (200, b"%PDF-1.4 w9"))
        # A member without the company permission can't open it, by either address.
        member = self.join(c, f"m{id(self)}@example.com")
        self.assertEqual(self.request("GET", f"/api/compliance/docs/{doc_id}/file", cookie=member)[0].status, 403)
        self.assertEqual(self.request("GET", f"/api/files/{file_id}", cookie=member)[0].status, 403)
        self.assertEqual(self.request("POST", "/api/compliance/docs", {"type": "coi"}, cookie=member)[0].status, 403)
        self.assertEqual(self.request("GET", "/api/compliance", cookie=member)[0].status, 200)
        res, out = self.request("POST", f"/api/compliance/docs/{doc_id}/delete", {}, cookie=c)
        self.assertEqual(res.status, 200, out)
        self.assertEqual(out["compliance"]["docs"], [])
        self.assertFalse(path.exists(), "the W-9's file goes with it")

    def test_w9_and_payroll_take_sensitive_docs_not_the_company_permission(self):
        # Integration (Nov 27): compliance's private files follow dev's "See payroll and sensitive
        # documents" (profiles.sensitive_file_ids), by every address, the export with files too.
        import io
        import zipfile
        c = self.cookie
        files = {}
        for kind in ("w9", "payroll", "coi"):
            _, out = self.request("POST", "/api/compliance/docs", {"type": kind}, cookie=c)
            doc_id = out["doc_id"]
            res, out = self.request("POST", f"/api/compliance/docs/{doc_id}/file", raw=b"%PDF-1.4 " + kind.encode(), cookie=c,
                                    headers={"Content-Type": "application/pdf", "X-Filename": f"{kind}.pdf"})
            self.assertEqual(res.status, 200, out)
            files[kind] = (doc_id, next(d["file_id"] for d in out["compliance"]["docs"] if d["id"] == doc_id))
        state = server.load_company(self.company_id(c))
        self.assertTrue({files["w9"][1], files["payroll"][1]} <= profiles.sensitive_file_ids(state))
        self.assertNotIn(files["coi"][1], profiles.sensitive_file_ids(state))
        for name, perms in (("Paperwork", ["company"]), ("Payroll", ["sensitive_docs"]), ("Books", ["data"])):
            res, out = self.request("POST", "/api/team/profiles", {"name": name, "perms": perms}, cookie=c)
            self.assertEqual(res.status, 200, out)
        ids = {p["name"]: p["id"] for p in out["profiles"]}

        def opens(cookie):
            return {kind: (self.request("GET", f"/api/files/{fid}", cookie=cookie)[0].status,
                           self.request("GET", f"/api/compliance/docs/{doc_id}/file", cookie=cookie)[0].status)
                    for kind, (doc_id, fid) in files.items()}
        company_only = self.join(c, f"p{id(self)}@example.com", ids["Paperwork"])
        self.assertEqual(opens(company_only), {"w9": (403, 403), "payroll": (403, 403), "coi": (200, 200)},
                         "editing company details doesn't open payroll or a W-9")
        sensitive = self.join(c, f"s{id(self)}@example.com", ids["Payroll"])
        self.assertEqual(opens(sensitive), {"w9": (200, 200), "payroll": (200, 200), "coi": (200, 200)})
        books = self.join(c, f"b{id(self)}@example.com", ids["Books"])
        res, raw = self.request("GET", "/api/export/with-files.zip", cookie=books)
        self.assertEqual(res.status, 200)
        names = zipfile.ZipFile(io.BytesIO(raw)).namelist()
        has = lambda fid: any(n.startswith(f"files/{fid}-") for n in names)
        self.assertEqual((has(files["w9"][1]), has(files["payroll"][1]), has(files["coi"][1])), (False, False, True))
        # The page is told which to show without a link.
        _, snap = self.request("GET", "/api/state", cookie=company_only)
        self.assertEqual({f["id"] for f in snap["files"] if f.get("sensitive")}, {files["w9"][1], files["payroll"][1]})

    def company_id(self, cookie):
        return self.request("GET", "/api/auth/me", cookie=cookie)[1]["user"]["company_id"]

    def join(self, owner, addr, profile_id="member"):
        res, out = self.request("POST", "/api/team/invite", {"email": addr, "profile_id": profile_id}, cookie=owner)
        self.assertEqual(res.status, 200, out)
        res, joined = self.request("POST", "/api/auth/accept-invite", {"accept_terms": True, "token": out["invite_path"].split("#invite=")[1],
                                                                       "name": "Mo", "password": "member password 1"})
        self.assertEqual(res.status, 200, joined)
        return res.getheader("Set-Cookie").split(";")[0]

    def test_refusals_requirements_settings_and_lien_dates(self):
        c = self.cookie
        res, out = self.request("POST", "/api/compliance/docs", {"type": "license", "number": "123-45-6789"}, cookie=c)
        self.assertEqual(res.status, 409)
        self.assertIn("number", out["fields"])
        res, out = self.request("POST", "/api/compliance/docs/cdoc-00000000/file", raw=b"x", cookie=c, headers={"X-Filename": "a.pdf"})
        self.assertEqual(res.status, 409)
        _, state = self.request("POST", "/api/jobs", {"name": "Birch", "customer": "Northline GC", "role": "GC", "approver": "Nia", "phone": "4045550177",
                                                      "ap_email": "ap@northline.example", "contract_cents": 2_000_000, "street": "5 Birch Rd", "city": "Atlanta",
                                                      "state": "GA", "zip": "30301", "client_type": "gc"}, cookie=c)
        j = state["jobs"][-1]
        res, out = self.request("POST", "/api/compliance/requirements", {"type": "coi", "payer_id": j["payer_id"], "additional_insured": True}, cookie=c)
        self.assertEqual(res.status, 200, out)
        self.assertIn(j["id"], out["compliance"]["warnings"])
        req_id = out["compliance"]["requirements"][0]["id"]
        res, out = self.request("POST", "/api/compliance/settings", {"remind_days": [45, 10]}, cookie=c)
        self.assertEqual((res.status, out["compliance"]["remind_days"]), (200, [45, 10]))
        res, out = self.request("POST", f"/api/compliance/liens/{j['id']}", {"noc_filed_on": "2026-11-01", "tier": "lower"}, cookie=c)
        self.assertEqual(res.status, 200, out)
        picture = next(x for x in out["compliance"]["jobs"] if x["job_id"] == j["id"])
        self.assertEqual(picture["state"], "GA")
        self.assertEqual(rows(picture)["notice_to_contractor"]["status"] in {"due_soon", "upcoming", "passed"}, True)
        res, out = self.request("POST", f"/api/compliance/requirements/{req_id}/delete", {}, cookie=c)
        self.assertEqual((res.status, out["compliance"]["requirements"]), (200, []))
        res, out = self.request("GET", "/api/reminders", cookie=c)
        self.assertIn("compliance", {k["key"] for k in out["kinds"]})
        self.assertEqual(out["prefs"]["compliance"], ["app"])


if __name__ == "__main__":
    unittest.main()
