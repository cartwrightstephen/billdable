"""The helper's "I don't know that yet" rule (Round 1 hunt, h6_helper.py): a shipped question with a
word tacked on ("today", "in Duracash", "my GC is asking") still gets its answer. The rule is
measured against the ranking without it: every shipped phrasing as written, and under 5% of them
with an add-on, may lose the answer they had."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import helper

# As h6_helper.py measures it: what people tack on, some of it filler and some of it not.
ADD_ONS = (" for the Elm job", " on Peachtree Clinic", " my GC is asking", " today", " please", " in Duracash", " for a sub job", " quickly")


class FillerWords(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = [{**e, "origin": "shipped"} for e in helper._read(helper.SHIPPED_KB)]
        cls.phrasings = [(e["id"], p) for e in cls.kb for p in e.get("questions", [])]
        idf = helper._idf(cls.kb)
        cls._idf, helper._idf = helper._idf, lambda kb: idf   # the same knowledge base every time

    @classmethod
    def tearDownClass(cls):
        helper._idf = cls._idf

    def right(self, q, eid, rule=True):
        real = helper._covers
        if not rule:
            helper._covers = lambda *a: True
        try:
            score, entry = helper.rank(q, self.kb)[0]
        finally:
            helper._covers = real
        return entry["id"] == eid and score >= helper.ANSWER_AT

    def test_shipped_phrasings_as_written_keep_their_answer(self):
        lost = [p for eid, p in self.phrasings if self.right(p, eid, rule=False) and not self.right(p, eid)]
        self.assertEqual(lost, [])

    def test_with_an_add_on_few_lose_it_and_fewer_get_another_answer(self):
        # Only the app's name is filler (hunt-r1b, h1_helper.py): "asking" and "today" can be what a
        # question is about, so " my GC is asking" and " today" may cost an answer, which then says
        # "I don't know that yet" rather than answering something else.
        lost, wrong, total = [], [], len(self.phrasings)   # each as written counts too, as h6 counts it
        for eid, p in self.phrasings:
            for add in ADD_ONS:
                q = p.rstrip("?") + add + "?"
                total += 1
                if self.right(q, eid, rule=False) and not self.right(q, eid):
                    lost.append(q)
                    if helper.rank(q, self.kb)[0][0] >= helper.ANSWER_AT:
                        wrong.append(q)
        self.assertLess(len(lost) / total, 0.10, lost[:10])
        self.assertLess(len(wrong) / total, 0.005, wrong)
        self.assertFalse([q for q in lost if q.endswith(" in Duracash?") or q.endswith(" please?") or q.endswith(" quickly?")],
                         "filler words alone never cost the answer")

    def test_asking_and_today_say_what_a_question_is_about(self):
        for q in ("Why is the app asking me which waiver it is?", "Can I record yesterday's work today?", "Why is Duracash asking me to look at the file?"):
            score, entry = helper.rank(q, self.kb)[0]
            self.assertTrue(score < helper.ANSWER_AT or entry["id"] == "which-waiver", (q, entry["id"], score))
        score, entry = helper.rank("Why is the app asking me which waiver it is?", self.kb)[0]
        self.assertEqual((entry["id"], score >= helper.ANSWER_AT), ("which-waiver", True))


if __name__ == "__main__":
    unittest.main()
