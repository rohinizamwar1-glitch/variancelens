import json
import os
import unittest
from unittest import mock

import llm_explainer
from finance_logic import analyze
from tests.helpers import row, SETTINGS


def fake_reply(payload):
    return mock.patch.object(llm_explainer, "call_llm", return_value=(json.dumps(payload), {"prompt_tokens": 900, "completion_tokens": 200}))


class GuardrailTests(unittest.TestCase):
    def setUp(self):
        os.environ["LLM_API_KEY"] = "test-key"
        self.analysis = analyze([row("Marketplace", 2800000, 2950000),
                                 row("Logistics", 520000, 610000, "lower", 8800, 8300, "rate_change", "New rate card")], SETTINGS)

    def tearDown(self):
        os.environ.pop("LLM_API_KEY", None)

    def test_invented_cause_is_overridden(self):
        reply = {"headline": "Marketplace sales beat forecast.", "cannot_conclude": "",
                 "drivers": [{"category": "Marketplace", "cause": "volume", "confidence": 85,
                              "explanation": "Marketplace grew due to the Big Billion Days sale.", "follow_up_question": ""}]}
        with fake_reply(reply):
            res = llm_explainer.generate(self.analysis, SETTINGS)
        d = next(x for x in res["drivers"] if x["category"] == "Marketplace")
        self.assertEqual(d["cause"], "unexplained")
        self.assertLessEqual(d["confidence"], 25)
        self.assertNotIn("Big Billion", d["explanation"])
        self.assertTrue(d["follow_up_question"])

    def test_invented_number_caps_confidence(self):
        reply = {"headline": "Costs rose.", "drivers": [{"category": "Logistics", "cause": "price", "confidence": 88,
                 "explanation": "Rate per shipment rose 23% to 73.49.", "follow_up_question": ""}]}
        with fake_reply(reply):
            res = llm_explainer.generate(self.analysis, SETTINGS)
        d = next(x for x in res["drivers"] if x["category"] == "Logistics")
        self.assertLessEqual(d["confidence"], 30)
        self.assertTrue(any("Numbers not found" in g for g in d["guard"]))

    def test_confidence_never_exceeds_ceiling(self):
        reply = {"headline": "Costs rose.", "drivers": [{"category": "Logistics", "cause": "price", "confidence": 99,
                 "explanation": "Cost per shipment went from 59.09 to 73.49 after the new rate card.", "follow_up_question": ""}]}
        with fake_reply(reply):
            res = llm_explainer.generate(self.analysis, SETTINGS)
        d = next(x for x in res["drivers"] if x["category"] == "Logistics")
        item = next(i for i in self.analysis["items"] if i["category"] == "Logistics")
        self.assertEqual(d["confidence"], item["confidence_ceiling"])
        self.assertEqual(d["cause"], "price")

    def test_skipped_driver_still_shown(self):
        with fake_reply({"headline": "x", "drivers": []}):
            res = llm_explainer.generate(self.analysis, SETTINGS)
        self.assertEqual({d["category"] for d in res["drivers"]}, {"Marketplace", "Logistics"})

    def test_unparseable_reply_falls_back(self):
        with mock.patch.object(llm_explainer, "call_llm", return_value=("Sorry, I can't help", {})):
            res = llm_explainer.generate(self.analysis, SETTINGS)
        self.assertEqual(res["status"], "fallback")

    def test_json_inside_code_fence_is_parsed(self):
        self.assertEqual(llm_explainer._extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_no_key_uses_fallback(self):
        os.environ.pop("LLM_API_KEY")
        res = llm_explainer.generate(self.analysis, SETTINGS)
        self.assertEqual(res["status"], "fallback")
        self.assertIn("LLM_API_KEY", res["error"])


if __name__ == "__main__":
    unittest.main()
