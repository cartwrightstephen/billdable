"""Reading dates, kinds, amounts and jobs from imported notes, file names and pastes (R5-01, R5-02, R5-05, R5-06, R5-07)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import importer

ROOT = Path(__file__).resolve().parents[1]


def jobs_state():
    return {"jobs": [
        {"id": "j1", "code": "KENN", "name": "Kennesaw Ortho Clinic", "address": "3895 Cherokee St NW, Kennesaw"},
        {"id": "j2", "code": "MILL", "name": "Mill Creek Clubhouse", "address": "2150 Mill Creek Pkwy, Marietta"},
    ], "payers": []}


class DatesAreDatesTests(unittest.TestCase):  # R5-01
    def test_a_job_code_after_the_date_keeps_it_a_date(self):
        self.assertEqual(importer.leading_date("10/16 VEST corridor tile done", 2026).isoformat(), "2026-10-16")
        self.assertEqual(importer.leading_date("1/2 MILL started wall tile", 2026).isoformat(), "2026-01-02")
        self.assertEqual(importer.leading_date("9/16 crew of 4 on site", 2026).isoformat(), "2026-09-16")

    def test_quantities_with_a_unit_are_not_dates(self):
        self.assertIsNone(importer.leading_date("3/4 inch pipe ran to the panel", 2026))
        self.assertIsNone(importer.leading_date("1/2 bath demo", 2026))
        self.assertIsNone(importer.leading_date('5/8" drywall hung', 2026))
        self.assertIsNone(importer.leading_date("15/16 of the tile set", 2026))

    def test_the_found_line_skips_sizes_and_counts_too(self):
        # The Inbox card's Found line read "3/4 inch" as Mar 4 and "May 2 guys" as May 2.
        self.assertEqual(importer.dates("10/5 layout. used 3/4 inch backer screws, 5/8\" board", 2026), ["2026-10-05"])
        self.assertEqual(importer.dates("10/9 pavers in\nMay 2 guys out sick", 2026), ["2026-10-09"])
        self.assertEqual(importer.dates("Paid May 15 on 1/2 MILL", 2026), ["2026-01-02", "2026-05-15"])

    def test_the_fraction_notes_file_splits_on_every_date(self):
        text = (ROOT / "testdata/test-contractor/app-issues/notes_date-read-as-fraction.txt").read_text()
        self.assertEqual([c["date"] for c in importer.split_notes(text, 2026)],
                         [None, "2026-10-15", "2026-10-16", "2026-01-02"])


class FileNamesTests(unittest.TestCase):  # R5-02
    def test_underscored_name_finds_the_code_and_the_kind(self):
        m = importer.match_job(jobs_state(), "", names="KENN_subcontract.pdf")
        self.assertEqual((m["job_id"], m["reason"]), ("j1", "mentions job code KENN"))
        self.assertEqual(importer.guess_kind("KENN_subcontract.pdf", "", [".pdf"]), "contract")

    def test_a_code_counts_in_any_case_as_a_whole_word(self):  # Oct 23 note #11
        self.assertEqual(importer.match_job(jobs_state(), "", names="13_Scan_kenn_subcontract.pdf")["job_id"], "j1")
        for text in ("tile for kenn today", "Kenn crew on site", "KENN: grout done"):
            m = importer.match_job(jobs_state(), text)
            self.assertEqual((m["job_id"], m["reason"]), ("j1", "mentions job code KENN"), text)
        # Inside another word it is not the code: "kennel" is not KENN, "millwork" is not MILL.
        self.assertIsNone(importer.match_job(jobs_state(), "drove past the kennel for millwork")["job_id"])
        self.assertIsNone(importer.match_job(jobs_state(), "", names="IMG_2231.jpg")["job_id"])

    def test_two_codes_in_lower_case_tie_and_capitals_win(self):
        state = jobs_state()
        state["jobs"].append({"id": "j3", "code": "VEST", "name": "Vesta Senior Living Phase 2", "address": ""})
        m = importer.match_job(state, "the kenn crew and the vest crew")
        self.assertIsNone(m["job_id"])
        self.assertEqual(sorted(m["candidates"][:m["tied"]]), ["j1", "j3"])
        self.assertEqual(importer.match_job(state, "the kenn crew went to MILL")["job_id"], "j2")

    def test_a_small_letter_code_that_is_a_word_of_a_job_name_is_not_the_code(self):
        # "mill" is a word of Mill Creek Clubhouse: "the mill shop" is not job MILL (R5-02's reason).
        self.assertIsNone(importer.match_job(jobs_state(), "Saw cuts at the mill shop")["job_id"])
        m = importer.match_job(jobs_state(), "the kenn crew and the mill shop")
        self.assertEqual((m["job_id"], m["reason"]), ("j1", "mentions job code KENN"))
        # In capitals, or in a file name, it is still the code.
        self.assertEqual(importer.match_job(jobs_state(), "Saw cuts at the MILL shop")["job_id"], "j2")
        self.assertEqual(importer.match_job(jobs_state(), "", names="mill_lobby.jpg")["job_id"], "j2")


def brightwater_state():
    """The test contractor's customers and open jobs (testdata/test-contractor/company.md)."""
    payers = [("tal", "Tallow Creek Builders", "ap@tallowcreekbuilders.example"), ("hv", "Harmon & Vale Construction", "payables@harmonvale.example"),
              ("iron", "Ironleaf General Contractors", "ap@ironleafgc.example"), ("wren", "Wrenfield Construction Group", "ap@wrenfieldcg.example")]
    jobs = [("KENN", "Kennesaw Ortho Clinic", "3895 Cherokee St NW, Kennesaw", "wren"),
            ("MILL", "Mill Creek Clubhouse", "2150 Mill Creek Pkwy, Marietta", "tal"),
            ("BROO", "Brookhaven Townhomes Bldg C", "1475 Dresden Dr NE, Brookhaven", "hv"),
            ("VEST", "Vesta Senior Living Phase 2", "3300 Chamblee Dunwoody Rd, Chamblee", "hv"),
            ("GLEN", "Glenwood Retail Shell", "1020 Glenwood Ave SE, Atlanta", "iron"),
            ("DECA", "Decatur Library Annex", "215 Sycamore St, Decatur", "wren"),
            ("PDRY", "Peachtree drywall", "", "iron")]
    return {"payers": [{"id": i, "name": n, "ap_email": e} for i, n, e in payers],
            "jobs": [{"id": c, "code": c, "name": n, "address": a, "payer_id": p} for c, n, a, p in jobs]}


class WeakSignsTests(unittest.TestCase):  # Oct 23 note #11, review
    """A code in small letters or one word of a job name is a weak sign: it never beats the
    customer's AP email the item came from, and never breaks a tie between that customer's jobs."""

    def test_an_everyday_word_from_a_customers_ap_email_stays_with_that_customer(self):
        cases = [
            ("Crew needs a safety vest on site Monday", "ap@tallowcreekbuilders.example", "MILL"),  # "vest" is not VEST
            ("shower pans for the ortho office", "ap@tallowcreekbuilders.example", "MILL"),  # not KENN's "Ortho"
            ("drywall delivery friday", "ap@tallowcreekbuilders.example", "MILL"),  # not "Peachtree drywall"
        ]
        for text, sender, job in cases:
            m = importer.match_job(brightwater_state(), text, sender)
            self.assertEqual((m["job_id"], m["reason"]), (job, "sent from Tallow Creek Builders's AP email"), text)

    def test_a_customer_with_two_jobs_stays_a_tie(self):
        cases = [
            ("Living room tile samples are here", "payables@harmonvale.example", ["BROO", "VEST"]),
            ("The retail space next door", "ap@wrenfieldcg.example", ["DECA", "KENN"]),
            ("Saw cuts at the mill shop", "payables@harmonvale.example", ["BROO", "VEST"]),
        ]
        for text, sender, tied in cases:
            m = importer.match_job(brightwater_state(), text, sender)
            self.assertIsNone(m["job_id"], text)
            self.assertEqual(sorted(m["candidates"][:m["tied"]]), tied, text)

    def test_a_small_letter_code_loses_to_the_ap_email(self):
        m = importer.match_job(brightwater_state(), "vest crew on site", "ap@tallowcreekbuilders.example")
        self.assertEqual(m["job_id"], "MILL")
        self.assertEqual(importer.match_job(brightwater_state(), "vest crew on site")["job_id"], "VEST")
        # In capitals the code still wins over the sender's job.
        self.assertEqual(importer.match_job(brightwater_state(), "VEST crew on site", "ap@tallowcreekbuilders.example")["job_id"], "VEST")

    def test_one_word_of_a_name_counts_only_written_with_a_capital(self):
        self.assertEqual(importer.match_job(brightwater_state(), "Vesta lobby photos")["job_id"], "VEST")
        self.assertIsNone(importer.match_job(brightwater_state(), "vesta lobby photos")["job_id"])
        self.assertIsNone(importer.match_job(brightwater_state(), "Drywall delivery Friday")["job_id"])
        # A file name counts in any case.
        self.assertEqual(importer.match_job(brightwater_state(), "", names="vesta_lobby.jpg")["job_id"], "VEST")


class JobNamePartTests(unittest.TestCase):  # Oct 23 note #11
    def state(self):
        return {"jobs": [
            {"id": "oak", "code": "OAK", "name": "Oak Street kitchen", "address": "418 Oak Street, Decatur"},
            {"id": "dec", "code": "DEC", "name": "Decatur addition", "address": "22 Ansley Street, Decatur"},
            {"id": "vest", "code": "VEST", "name": "Vesta Phase 2", "address": ""},
            {"id": "inm", "code": "INM", "name": "Inman Park bath", "address": "88 Hurt Street, Atlanta"},
            {"id": "grt", "code": "GRT", "name": "Grant Park addition", "address": "412 Boulevard, Atlanta"},
        ], "payers": []}

    def test_a_telling_part_of_the_name_finds_the_job(self):
        m = importer.match_job(self.state(), "Tile came for oak street, stacked in the garage")
        self.assertEqual(m["job_id"], "oak")
        self.assertEqual(m["reason"], "mentions Oak Street, part of the job name Oak Street kitchen")
        self.assertEqual(importer.match_job(self.state(), "Grant Park tile is in")["job_id"], "grt")
        self.assertEqual(importer.match_job(self.state(), "Vesta lobby photos")["job_id"], "vest")
        self.assertEqual(importer.match_job(self.state(), "", names="inman_park_waiver.pdf")["job_id"], "inm")

    def test_the_full_name_still_counts_more_than_a_part(self):
        m = importer.match_job(self.state(), "Invoice for the Oak Street kitchen job")
        self.assertEqual((m["job_id"], m["reason"]), ("oak", "mentions the job name Oak Street kitchen"))

    def test_words_any_job_could_have_find_nothing(self):
        for text in ("kitchen backsplash done", "the park was closed", "Phase 2 started", "addition framing done", "Street was blocked"):
            self.assertIsNone(importer.match_job(self.state(), text)["job_id"], text)

    def test_a_part_another_job_shares_is_no_match(self):
        # Decatur is in three jobs' names or addresses: it names none of them.
        self.assertIsNone(importer.match_job(self.state(), "Decatur supply run")["job_id"])
        state = self.state()
        state["jobs"].append({"id": "oak2", "code": "OSB", "name": "Oak Street bath", "address": "500 Oak Street, Decatur"})
        m = importer.match_job(state, "Tile came for oak street")
        self.assertIsNone(m["job_id"])
        # Each job's own full name still finds it.
        self.assertEqual(importer.match_job(state, "Oak Street bath tile came")["job_id"], "oak2")
        self.assertEqual(importer.match_job(state, "Oak Street kitchen tile came")["job_id"], "oak")

    def test_two_jobs_named_equally_are_not_guessed(self):
        m = importer.match_job(self.state(), "Grant Park and Inman Park both need grout")
        self.assertIsNone(m["job_id"])
        self.assertEqual((sorted(m["candidates"][:m["tied"]]), m["tied"]), (["grt", "inm"], 2))

    def test_a_closed_job_is_never_matched_by_a_part(self):
        state = self.state()
        state["jobs"][2]["closed"] = True
        self.assertIsNone(importer.match_job(state, "Vesta lobby photos")["job_id"])

    def test_street_number_and_name_match_the_address(self):
        m = importer.match_job(jobs_state(), "Tile delivered to 3895 Cherokee this morning")
        self.assertEqual(m["job_id"], "j1")
        self.assertIn("3895 Cherokee", m["reason"])

    def test_an_email_whose_only_attachment_is_a_picture_is_a_photo(self):
        self.assertEqual(importer.guess_kind("08_email_photo-attachment-from-foreman.eml IMG_2240.jpg",
                                             "Layout is snapped for the lobby.", [".eml", ".jpg"]), "photo")
        self.assertEqual(importer.guess_kind("note.eml scan.pdf IMG_1.jpg", "See attached.", [".eml", ".pdf", ".jpg"]), "note")
        self.assertEqual(importer.guess_kind("note.eml", "Call me.", [".eml"]), "note")
        self.assertEqual(importer.guess_kind("IMG_1.heic", "", ".heic"), "photo")

    def test_the_foreman_email_upload_is_a_photo_for_mill(self):
        raw = (ROOT / "testdata/test-contractor/import/08_email_photo-attachment-from-foreman.eml").read_bytes()
        parsed = importer.parse_email(raw)
        state = jobs_state()
        state["files"] = [{"id": "f1", "name": "08_email_photo-attachment-from-foreman.eml", "ext": ".eml"},
                          {"id": "f2", "name": parsed["attachments"][0]["filename"], "ext": ".jpg"}]
        item = importer.add_item(state, "upload", text=parsed["text"], sender=parsed["from"], subject=parsed["subject"],
                                 file_ids=["f1", "f2"])
        self.assertEqual((item["suggested_job_id"], item["kind"]), ("j2", "photo"))


class InboxCardTests(unittest.TestCase):  # R5-05, R5-06, R5-07
    def test_amounts_without_a_dollar_sign_need_a_comma_or_cents(self):
        self.assertEqual(importer.amounts("Total $2,340.00, then 1,250 and 412.50"), [234000, 125000, 41250])
        self.assertEqual(importer.amounts("4 crew on 10/12, 3.5 hours, call 404.555.1234, v1.25.3"), [])

    def test_a_tie_names_the_tied_jobs_first(self):
        state = {"jobs": [{"id": "j1", "code": "MILL", "name": "Mill Creek Clubhouse", "payer_id": "p1"},
                          {"id": "j2", "code": "VEST", "name": "Vesta Phase 2", "payer_id": "p1"},
                          {"id": "j3", "code": "OAKS", "name": "Oak Street", "payer_id": "p2"}],
                 "payers": [{"id": "p1", "name": "Harmon Vale", "ap_email": "ap@hv.example"}, {"id": "p2", "name": "Lee"}]}
        m = importer.match_job(state, "Need the backup", sender="ap@hv.example")
        self.assertIsNone(m["job_id"])
        self.assertEqual((sorted(m["candidates"]), m["tied"]), (["j1", "j2"], 2))

    def test_a_supplier_invoice_is_a_ticket_unless_it_names_a_pay_app(self):
        self.assertEqual(importer.guess_kind("ABC Supply invoice 4471.pdf", "", [".pdf"]), "ticket")
        self.assertEqual(importer.guess_kind("Invoice", "Backup for PA-VEST-02 attached"), "pay_app")
        self.assertEqual(importer.guess_kind("Pay application 3", ""), "pay_app")

    def test_a_forwarded_email_is_read_from_its_headers(self):
        text = ("FYI, see below.\n\n---------- Forwarded message ---------\nFrom: Kim Park <ap@park.example>\n"
                "Date: Mon, Oct 12, 2026 at 9:00 AM\nSubject: Backup for October\nTo: Pat <pat@pine.example>\n\nSend the tickets.")
        above, block = importer.pasted_headers(text)
        parsed = importer.parse_email(block.encode())
        self.assertEqual((above, parsed["from"], parsed["subject"], parsed["text"]),
                         ("FYI, see below.", "Kim Park <ap@park.example>", "Backup for October", "Send the tickets."))
        outlook = "From: Kim Park [mailto:ap@park.example]\nSent: Monday, October 12, 2026 9:00 AM\nSubject: RE: tickets\n\nok"
        self.assertEqual(importer.parse_email(importer.pasted_headers(outlook, strict=True)[1].encode())["from"], "Kim Park <ap@park.example>")
        self.assertIsNone(importer.pasted_headers("To: Mike\nbring the saw", strict=True))
        self.assertIsNone(importer.pasted_headers("\n".join(["line"] * 10 + ["From: late@x.example"])))


class UndoRefusalTests(unittest.TestCase):  # Oct 23 note #13
    """Undo is refused while paperwork uses the record, as before; the refusal now says where to go."""

    def state(self, status="draft", where="invoice", auto=False):
        attached = [{"id": "pk1", "label": "Lien waiver", "present": True, "required": True, "blocker": "Lien waiver is missing.",
                     "attachments": [{"name": "Waiver", "record_id": "rec1", "auto": auto}]}]
        job = {"id": "j1", "code": "OAK", "name": "Oak Street kitchen", "payments": [], "change_orders": [], "packet": [], "closeout_packet": [],
               "records": [{"id": "rec1", "label": "Waiver", "kind": "waiver"}], "daily_records": [],
               "invoices": [{"id": "inv1", "number": "PA-OAK-3", "status": status, "packet": []}]}
        if where == "invoice":
            job["invoices"][0]["packet"] = attached
        elif where == "job":
            job["packet"] = attached
        else:
            job["closeout_packet"] = attached
        return {"jobs": [job], "daily_records": [], "audit": [],
                "inbox": [{"id": "inb1", "status": "filed", "filed_job_id": "j1", "record_ids": ["rec1"]}]}

    def refusal(self, state):
        with self.assertRaises(importer.domain.GateError) as caught:
            importer.undo_item(state, "inb1", "owner")
        self.assertEqual(state["jobs"][0]["records"][0]["id"], "rec1", "nothing taken off")
        return caught.exception

    def test_a_draft_links_to_its_paperwork_page_where_remove_is(self):
        for status in ("draft", "awaiting_approval"):
            exc = self.refusal(self.state(status))
            self.assertEqual(str(exc), "The paperwork for PA-OAK-3 uses this record. Tap Remove on it there first.")
            self.assertEqual(exc.action, {"label": "Open the paperwork for PA-OAK-3", "view": "packet-detail", "job_id": "j1", "detail": "inv1"})

    def test_the_job_and_closeout_paperwork_link_to_their_pages(self):
        exc = self.refusal(self.state(where="job"))
        self.assertEqual((exc.action["view"], exc.action["detail"]), ("packet-detail", ""))
        exc = self.refusal(self.state(where="closeout"))
        self.assertEqual((str(exc), exc.action["detail"]), ("The closeout paperwork uses this record. Tap Remove on it there first.", "closeout"))

    def test_a_sent_pay_app_says_pull_it_back_and_links_to_it(self):
        for status in ("sent", "approved"):
            exc = self.refusal(self.state(status))
            self.assertIn("went to the customer with PA-OAK-3", str(exc))
            self.assertIn("pull it back", str(exc))
            self.assertNotIn("Remove", str(exc))
            self.assertEqual(exc.action, {"label": "Open PA-OAK-3", "view": "payapp-detail", "job_id": "j1", "detail": "inv1"})

    def test_someone_who_cannot_pull_back_is_told_to_ask_an_owner(self):  # review: pull back is owner-only
        for status in ("sent", "approved"):
            with self.assertRaises(importer.domain.GateError) as caught:
                importer.undo_item(self.state(status), "inb1", "member", can_pull_back=False)
            exc = caught.exception
            self.assertEqual(str(exc), "This record went to the customer with PA-OAK-3, so it stays on the job. "
                                       "Ask an owner to pull PA-OAK-3 back if it needs to change.")
            self.assertEqual(exc.action, {"label": "Open PA-OAK-3", "view": "payapp-detail", "job_id": "j1", "detail": "inv1"})
        # The draft's paperwork has Remove for everyone: same words either way.
        with self.assertRaises(importer.domain.GateError) as caught:
            importer.undo_item(self.state("draft"), "inb1", "member", can_pull_back=False)
        self.assertEqual(str(caught.exception), "The paperwork for PA-OAK-3 uses this record. Tap Remove on it there first.")

    def test_a_paid_or_pulled_back_pay_app_does_not_offer_pulling_back(self):
        state = self.state("sent")
        state["jobs"][0]["payments"] = [{"id": "pay1", "invoice_id": "inv1"}]
        for st in (self.state("partial"), self.state("paid"), state):
            exc = self.refusal(st)
            self.assertIn("a payment is recorded on it", str(exc))
            self.assertNotIn("pull it back", str(exc))
        pulled = self.state("void")
        pulled["jobs"][0]["invoices"][0]["sent_at"] = "2026-10-20T10:00:00Z"
        exc = self.refusal(pulled)
        self.assertIn("was pulled back, but what it sent stays as it was", str(exc))
        self.assertEqual(exc.action["view"], "payapp-detail")

    def test_a_deleted_draft_never_went_to_the_customer(self):
        # Delete draft sets status "void" but nothing was sent: Undo goes ahead, and the record
        # comes off the deleted draft's paperwork with it.
        state = self.state("void")
        importer.undo_item(state, "inb1", "owner")
        job = state["jobs"][0]
        self.assertEqual(job["records"], [])
        self.assertEqual(job["invoices"][0]["packet"][0]["attachments"], [])
        self.assertEqual(state["inbox"][0]["status"], "new")

    def test_attached_automatically_still_comes_off_with_the_record(self):
        state = self.state("draft", auto=True)
        importer.undo_item(state, "inb1", "owner")
        self.assertEqual((state["jobs"][0]["records"], state["inbox"][0]["status"]), ([], "new"))

    def test_attached_automatically_to_a_pay_app_that_went_out_still_stays(self):
        # What went with a sent pay app stays as it was sent, however it was attached.
        for status in ("sent", "approved", "partial", "paid", "void"):
            for can_pull_back in (True, False):
                state = self.state(status, auto=True)
                state["jobs"][0]["invoices"][0]["sent_at"] = "2026-10-20T10:00:00Z"
                with self.assertRaises(importer.domain.GateError) as caught:
                    importer.undo_item(state, "inb1", "member", can_pull_back=can_pull_back)
                self.assertIn("went to the customer with PA-OAK-3", str(caught.exception))
                job = state["jobs"][0]
                self.assertEqual(job["records"][0]["id"], "rec1")
                item = job["invoices"][0]["packet"][0]
                self.assertEqual((item["present"], len(item["attachments"])), (True, 1))
                self.assertEqual(state["inbox"][0]["status"], "filed")


if __name__ == "__main__":
    unittest.main()
