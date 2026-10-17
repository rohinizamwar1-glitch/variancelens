import unittest
from decimal import Decimal as D

from finance_logic import analyze, snapshot_for_history
from tests.helpers import row, SETTINGS


class LogicLayerTests(unittest.TestCase):
    def test_variance_pct_and_assessment_respect_direction(self):
        a = analyze([row("Revenue", 1000000, 920000), row("Rent", 200000, 180000, "lower")], SETTINGS)
        rev = next(i for i in a["items"] if i["category"] == "Revenue")
        rent = next(i for i in a["items"] if i["category"] == "Rent")
        self.assertEqual(rev["variance"], D("-80000"))
        self.assertEqual(rev["pct"], D("-8"))
        self.assertEqual(rev["assessment"], "Unfavourable")
        self.assertEqual(rent["assessment"], "Favourable")

    def test_materiality_tiers(self):
        a = analyze([row("Big", 1000000, 700000), row("Pct only", 100000, 110000),
                     row("Tiny", 500000, 505000)], SETTINGS)
        tiers = {i["category"]: i["tier"] for i in a["items"]}
        self.assertEqual(tiers["Big"], "High")
        self.assertEqual(tiers["Pct only"], "Medium")   # 10% but only 3% of gross movement
        self.assertEqual(tiers["Tiny"], "Low")          # 5,000 and 1% -> below both thresholds

    def test_pareto_key_drivers_reach_80_percent(self):
        a = analyze([row("A", 1000000, 500000), row("B", 1000000, 800000),
                     row("C", 1000000, 900000), row("D", 1000000, 940000)], SETTINGS)
        keys = [i["category"] for i in a["key_drivers"]]
        self.assertEqual(keys, ["A", "B"])  # 500k + 200k of 860k = 81%
        self.assertGreaterEqual(a["key_coverage"], D("0.8"))

    def test_price_volume_split_adds_up_and_picks_volume(self):
        a = analyze([row("Website sales", 4200000, 3610000, uf=6000, ua=5050)], SETTINGS)
        i = a["items"][0]
        pv = i["price_volume"]
        self.assertEqual(pv["volume_effect"] + pv["price_effect"], i["variance"])
        self.assertEqual(i["cause"], "volume")

    def test_no_evidence_means_unexplained_and_weak(self):
        i = analyze([row("Marketplace", 2800000, 2950000)], SETTINGS)["items"][0]
        self.assertEqual(i["cause"], "unexplained")
        self.assertEqual(i["evidence_band"], "weak")
        self.assertEqual(i["confidence_ceiling"], 40)
        self.assertEqual(i["allowed_causes"], ["unexplained"])

    def test_tag_conflicting_with_unit_data_is_flagged(self):
        # analyst says price changed, but price per unit is flat and volume fell
        i = analyze([row("Sales", 1000000, 800000, uf=1000, ua=800, tag="rate_change")], SETTINGS)["items"][0]
        self.assertTrue(i["conflict"])
        self.assertEqual(i["cause"], "volume")

    def test_recurring_miss_detected_from_history(self):
        past = analyze([row("Website sales", 4000000, 3700000)], SETTINGS)
        history = [snapshot_for_history(past)] * 3
        i = analyze([row("Website sales", 4200000, 3610000)], SETTINGS, history)["items"][0]
        self.assertTrue(i["recurring"])

    def test_data_quality_flags(self):
        a = analyze([row("Refunds", 10000, 250000, "lower"), row("Other income", 50000, -80000)], SETTINGS)
        self.assertEqual(len(a["data_flags"]), 3)

    def test_zero_forecast_has_no_percentage(self):
        i = analyze([row("New line", 0, 60000)], SETTINGS)["items"][0]
        self.assertIsNone(i["pct"])
        self.assertEqual(i["tier"], "High")


if __name__ == "__main__":
    unittest.main()
