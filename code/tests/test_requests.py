"""Tests for request-intent classification. Run from the repo root: python3 -m unittest discover -s code/tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ghostaudit.data import read_csv  # noqa: E402
from ghostaudit.requests import AMBIGUOUS, DELETION, NON_DELETION, classify_request  # noqa: E402


class RealRequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = {r["case_id"]: r["request_text"] for r in read_csv("deletion_requests.csv")}
        cls.samples = {r["case_id"]: r["request_text"] for r in read_csv("sample_deletion_requests.csv")}

    def test_explicit_non_deletion_requests(self):
        # each text states it, e.g. case_539 "This is an information request, not a request to erase it."
        non_deletion = {c for c, t in self.main.items() if classify_request(t).intent == NON_DELETION}
        self.assertEqual(non_deletion, {f"case_{n}" for n in range(533, 543)})
        self.assertIn("not a request to erase", " ".join(classify_request(self.main["case_539"]).cues))

    def test_every_other_main_request_asks_for_deletion(self):
        others = [c for c, t in self.main.items() if classify_request(t).intent != NON_DELETION]
        self.assertEqual(len(others), 508)
        self.assertTrue(all(classify_request(self.main[c]).intent == DELETION for c in others))

    def test_deletion_templates_in_five_languages(self):
        for case_id, lang in (("case_54", "pt"), ("case_84", "fr"), ("case_78", "ja"), ("case_69", "es"), ("case_66", "ko")):
            with self.subTest(case=case_id):
                intent = classify_request(self.main[case_id])
                self.assertEqual(intent.intent, DELETION)
                self.assertTrue(any(c.startswith(f"deletion_{lang}:") for c in intent.cues), intent.cues)

    def test_samples_are_never_non_deletion(self):
        self.assertFalse([c for c, t in self.samples.items() if classify_request(t).intent == NON_DELETION])

    def test_verification_requests_count_as_deletion(self):
        # case_28 "Can you verify that my account was deleted?"; case_26 "confirm that the profile is gone"
        self.assertEqual(classify_request(self.samples["case_28"]).intent, DELETION)
        self.assertEqual(classify_request(self.samples["case_26"]).intent, DELETION)

    def test_negation_inside_a_deletion_request_is_not_a_refusal(self):
        # case_28 says "please do not treat that record as mine"; case_17 "must not be able to restore"
        for case_id in ("case_28", "case_17", "case_04", "case_31"):
            self.assertEqual(classify_request(self.samples[case_id]).intent, DELETION, case_id)


class SyntheticRequestTests(unittest.TestCase):
    def test_an_email_or_account_mention_alone_is_not_deletion(self):
        intent = classify_request("SYNTHETIC: my account email is ada@example.test, please update my address.")
        self.assertEqual(intent.intent, AMBIGUOUS)

    def test_refusals_beat_deletion_words(self):
        self.assertEqual(classify_request("SYNTHETIC: Do not delete anything, I only want a copy.").intent, NON_DELETION)
        self.assertEqual(classify_request("SYNTHETIC: Please remove me from marketing lists only.").intent, NON_DELETION)

    def test_other_languages_and_closures(self):
        self.assertEqual(classify_request("SYNTHETIC: Bitte Löschung meines Kontos.").intent, DELETION)
        self.assertEqual(classify_request("SYNTHETIC: Please close my account.").intent, DELETION)


if __name__ == "__main__":
    unittest.main()
