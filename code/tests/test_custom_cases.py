"""Runs the team-authored evaluation cases (code/evaluation/custom_cases.py) inside the test suite."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evaluation"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import custom_cases  # noqa: E402


class CustomCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = custom_cases.run_cases()

    def test_every_custom_case_passes(self):
        self.assertEqual(len(self.results), len(custom_cases.CASES))
        for result in self.results:
            with self.subTest(case=result["id"]):
                self.assertEqual(result["actual_status"], result["expected_status"])
                self.assertEqual(result["actual_steps"], result["expected_steps"])

    def test_comparison_detects_wrong_expectations(self):
        # negative control: corrupting an expectation must not still compare equal
        result = self.results[2]
        wrong = copy.deepcopy(result["expected_steps"])
        wrong[0]["evidence_id"] = "SYNTHETIC_other"
        self.assertNotEqual(result["actual_steps"], wrong)
        self.assertNotEqual(result["actual_steps"], list(reversed(result["expected_steps"])))

    def test_the_decoy_and_late_records_are_in_the_fixture_but_not_in_the_output(self):
        decoy_case, late_case = self.results[1], self.results[4]
        self.assertNotIn("search_9299", {s["resource_id"] for s in decoy_case["actual_steps"]})
        self.assertNotIn("search_9205", {s["resource_id"] for s in late_case["actual_steps"]})


if __name__ == "__main__":
    unittest.main()
