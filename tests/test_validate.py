"""The field rules (G1, domain/validate.py): each rule on the shared case table
(tests/validate_cases.txt, which tests/js/validate.test.mjs reads too), the 409 shape, and
addresses composed and split."""

import unittest
from pathlib import Path

import domain
from domain import validate as v

CASES = Path(__file__).resolve().parent / "validate_cases.txt"


def cases():
    for line in CASES.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            parts = line.split("\t")
            yield parts[0], parts[1], parts[2], (parts[3] if len(parts) > 3 else None)


class RuleTests(unittest.TestCase):
    def test_every_case_in_the_table(self):
        seen = set()
        for rule, value, want, phone in cases():
            seen.add(rule)
            got = v.check_field(rule, value)
            self.assertEqual(got == {}, want == "ok", f"{rule} {value!r}")
            if phone:
                self.assertEqual(v.normalize_phone(value), phone)
        self.assertEqual(seen, set(v.FIELD_RULES), "every rule has cases")

    def test_blank_passes_unless_required(self):
        self.assertEqual(v.check_field("zip", "  "), {})
        self.assertEqual(v.check_field("zip", "", "job_zip", required=True), {"job_zip": v.rule_message("zip")})

    def test_check_fields_names_each_box(self):
        errors = v.check_fields({"phone": "555", "zip": "30303", "email": "x"}, {"phone": "phone", "zip": "zip", "email": "email", "licence": "licence"})
        self.assertEqual(set(errors), {"phone", "email"})
        self.assertEqual(v.check_fields({}, {"name": "name"}, required=["name"]), {"name": v.rule_message("name")})

    def test_refuse_fields_is_a_409_with_fields(self):
        v.refuse_fields({})
        with self.assertRaises(domain.GateError) as cm:
            v.refuse_fields({"phone": "bad"})
        self.assertEqual(cm.exception.fields, {"phone": "bad"})

    def test_states_are_the_usps_codes(self):
        self.assertEqual(len(v.US_STATES), 56)
        self.assertEqual(len(set(v.US_STATES)), 56)
        self.assertEqual(v.normalize_state("ga."), "GA")

    def test_domain_reexports_the_rules(self):
        self.assertIs(domain.check_field, v.check_field)
        self.assertEqual(v._EMAIL_RE.pattern, domain.EMAIL_RE.pattern)


class AddressTests(unittest.TestCase):
    def test_compose(self):
        self.assertEqual(v.compose_address({"street": "120 Peachtree St NE", "city": "Atlanta", "state": "ga", "zip": "30303"}),
                         "120 Peachtree St NE, Atlanta, GA 30303")
        self.assertEqual(v.compose_address({"street": "2 Elm St", "city": "", "state": "", "zip": ""}), "2 Elm St")

    def test_split_reads_the_usual_ways_of_writing_it(self):
        want = {"street": "120 Peachtree St NE", "city": "Atlanta", "state": "GA", "zip": "30303"}
        for text in ["120 Peachtree St NE, Atlanta, GA 30303", "120 Peachtree St NE, Atlanta GA 30303",
                     "120 Peachtree St NE\nAtlanta, GA 30303", " 120 Peachtree St NE , Atlanta , ga 30303 "]:
            self.assertEqual(v.split_address(text), want, text)
        self.assertEqual(v.split_address("3895 Cherokee St NW, Kennesaw, GA")["zip"], "")
        self.assertIsNone(v.split_address("3895 Cherokee St NW"))
        self.assertIsNone(v.split_address("1 Main St, Toronto, ON M5V 2T6"))

    def test_split_and_compose_round_trip(self):
        text = "45 Oak Street, Suite 2, Decatur, GA 30030-1234"
        self.assertEqual(v.compose_address(v.split_address(text)), text)

    def test_a_legacy_line_that_does_not_split_goes_in_the_street_box(self):
        self.assertEqual(v.parse_legacy_address("Lot 7, Vesta subdivision"),
                         {"street": "Lot 7, Vesta subdivision", "city": "", "state": "", "zip": ""})
        self.assertEqual(v.parse_legacy_address("2 Elm St, Rome, GA 30161")["city"], "Rome")

    def test_check_address(self):
        self.assertEqual(v.check_address({"street": "2 Elm St", "city": "Rome", "state": "GA", "zip": "30161"}), {})
        self.assertEqual(set(v.check_address({"street": "2 Elm St", "city": "", "state": "Georgia", "zip": "301"}, prefix="site_")),
                         {"site_city", "site_state", "site_zip"})
        self.assertEqual(v.check_address({}, required=False), {})


if __name__ == "__main__":
    unittest.main()
