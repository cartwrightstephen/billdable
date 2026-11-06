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


class Kb(unittest.TestCase):
    """The shipped knowledge base, with one idf for every ranking."""

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


class FillerWords(Kb):
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


class AskingAsAWord(Kb):
    """r1c, M6: "asking" stays a word, so the app asking can be what a question is about, but someone
    else asking is only how it is put, and an answer that needs words ("which", "tap"...) is not the
    answer to a question without them. Scored, not filler."""

    def top(self, q):
        score, entry = helper.rank(q, self.kb)[0]
        return entry["id"], score

    def test_my_client_or_my_gc_asking_is_how_the_question_is_put(self):
        self.assertEqual(self.top("my client is asking for retainage, how do I ask")[0], "release-retainage")
        self.assertEqual(self.top("client asking about change orders")[0], "change-orders")
        for q in ("my client is asking for retainage, how do I ask", "client asking about change orders"):
            self.assertGreaterEqual(self.top(q)[1], helper.ANSWER_AT, q)

    def test_which_waiver_is_only_the_answer_to_a_question_about_which_waiver(self):
        for q in ("Why is my GC asking for a waiver", "my GC is asking for a lien waiver", "Who is asking?", "What is asking for retainage?"):
            eid, score = self.top(q)
            self.assertFalse(eid == "which-waiver" and score >= helper.ANSWER_AT, (q, eid, score))
        for q in ("Why is the app asking me which waiver it is?", "Why is the app asking me to tap conditional or unconditional?", "What do I tap for the waiver?",
                  "How do I change which waiver I said it is?"):
            eid, score = self.top(q)
            self.assertEqual((eid, score >= helper.ANSWER_AT), ("which-waiver", True), q)

    def test_the_three_earlier_questions_still_do_not_get_a_wrong_answer(self):
        for q in ("Can I record yesterday's work today?", "Why is Duracash asking me to look at the file?"):
            self.assertLess(self.top(q)[1], helper.SUGGEST_AT, q)   # "I don't know that yet"

    def test_the_answer_says_where_the_question_appears_and_how_to_change_an_answer(self):
        answer = next(e for e in self.kb if e["id"] == "which-waiver")["answer"]
        for words in ("once for each file", "Review & send", "closeout page", "To change an answer", "couldn't tell"):
            self.assertIn(words, answer)

    def test_edited_in_the_app_it_keeps_the_words_it_needs(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            orig = (helper.LOCAL_KB, helper.LOG_PATH)
            helper.LOCAL_KB, helper.LOG_PATH = Path(tmp) / "helper_answers.json", Path(tmp) / "helper_log.json"
            try:
                helper.save_answer({"id": "which-waiver", "questions": ["Which waiver do I tap?"], "answer": "Edited."}, "founder")
                edited = next(e for e in helper.load_kb() if e["id"] == "which-waiver")
                self.assertEqual((edited["answer"], sorted(edited["needs"])), ("Edited.", ["answer", "conditional", "lien", "tap", "unconditional", "which"]))
            finally:
                helper.LOCAL_KB, helper.LOG_PATH = orig


if __name__ == "__main__":
    unittest.main()
