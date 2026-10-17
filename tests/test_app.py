import json
import os
import tempfile
import unittest
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = str(Path(_tmp) / "test.sqlite3")
os.environ["ADMIN_PASSWORD"] = "letmein"
os.environ.pop("LLM_API_KEY", None)

import app as web  # noqa: E402

SAMPLE = json.loads((Path(web.BASE_DIR) / "sample_data" / "september_review.json").read_text())


def form_data(sample):
    data = {k: sample[k] for k in ("period", "business_unit", "amount_threshold", "pct_threshold", "analyst_notes")}
    for key in ("category", "forecast", "actual", "preferred_direction", "units_forecast", "units_actual", "evidence_tag", "row_note"):
        data[key] = [r.get(key, "") for r in sample["rows"]]
    return data


class AppTests(unittest.TestCase):
    def setUp(self):
        self.client = web.app.test_client()

    def test_pages_load(self):
        for path in ("/", "/?sample=1", "/reports", "/admin/login"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_full_pipeline_saves_and_shows_report(self):
        res = self.client.post("/analyze", data=form_data(SAMPLE))
        self.assertEqual(res.status_code, 302)
        page = self.client.get(res.headers["Location"]).get_data(as_text=True)
        self.assertIn("Website sales", page)
        self.assertIn("Calculated only", page)       # no key in tests
        self.assertIn("Key driver", page)

    def test_validation_errors_keep_input(self):
        bad = form_data(SAMPLE)
        bad["forecast"][0] = "abc"
        bad["period"] = "Sept"
        res = self.client.post("/analyze", data=bad)
        self.assertEqual(res.status_code, 422)
        body = res.get_data(as_text=True)
        self.assertIn("Forecast and Actual must be numbers", body)
        self.assertIn("YYYY-MM", body)
        self.assertIn("Influencer fees", body)

    def test_admin_requires_password_and_seed_enables_recurrence(self):
        self.assertEqual(self.client.get("/admin").status_code, 302)
        self.client.post("/admin/login", data={"password": "wrong"})
        self.assertEqual(self.client.get("/admin").status_code, 302)
        self.client.post("/admin/login", data={"password": "letmein"})
        self.assertEqual(self.client.get("/admin").status_code, 200)
        self.client.post("/admin/seed-demo")
        res = self.client.post("/analyze", data=form_data(SAMPLE))
        page = self.client.get(res.headers["Location"]).get_data(as_text=True)
        self.assertIn("Recurring pattern", page)

    def test_review_is_saved(self):
        loc = self.client.post("/analyze", data=form_data(SAMPLE)).headers["Location"]
        rid = int(loc.rsplit("/", 1)[1])
        self.client.post(f"/reports/{rid}/review", data={"review": "rejected", "comment": "Missed marketplace returns"})
        self.assertEqual(web.db.get_report(rid)["review"], "rejected")

    def test_unknown_report_is_404(self):
        self.assertEqual(self.client.get("/reports/99999").status_code, 404)


if __name__ == "__main__":
    unittest.main()
