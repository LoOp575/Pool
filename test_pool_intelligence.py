import unittest

from scanner import _final_intelligence, snapshot_monte_carlo


class TestFinalIntelligence(unittest.TestCase):
    def base_row(self, lp=80, fee=80, rq=80, ds=80):
        return {
            "lp_score": lp,
            "lp_components": {
                "fee_potential": fee,
                "range_quality_proxy": rq,
                "directional_safety": ds,
            },
            "live_metrics": {
                "h1": 2, "h6": 5, "h24": 12,
                "volume_acceleration": 1.2,
                "buy_ratio": 0.52,
            },
            "risk": {"score": 20},
            "lp_strategy": "BALANCED",
        }

    def test_merton_probabilities(self):
        mc = snapshot_monte_carlo(
            1.0, 1.0, 2.0, 5.0, 12.0,
            10000, 120000, 50000, 120, 100, 220,
            0.8, 1.25, horizon_bars=24, paths=300
        )
        self.assertEqual(mc["paths"], 300)
        self.assertGreaterEqual(mc["p_ever_out_of_range"], 0)
        self.assertLessEqual(mc["p_ever_out_of_range"], 1)
        self.assertAlmostEqual(
            mc["p_below"] + mc["p_inside"] + mc["p_above"], 1.0, places=9
        )

    def test_final_intelligence_enters_only_with_survival(self):
        row = self.base_row()
        mc = {
            "p_below": 0.10, "p_inside": 0.70, "p_above": 0.20,
            "p_ever_out_of_range": 0.18,
            "p05": 0.9, "p50": 1.02, "p95": 1.16,
        }
        r = {"lower": 0.8, "center": 1.0, "upper": 1.25, "width_pct": .25}
        out = _final_intelligence({}, row, mc, r)
        self.assertEqual(out["decision"], "MASUK")
        self.assertGreaterEqual(out["score"], 70)

    def test_final_intelligence_blocks_range_break(self):
        row = self.base_row()
        mc = {
            "p_below": 0.20, "p_inside": 0.45, "p_above": 0.35,
            "p_ever_out_of_range": 0.62,
            "p05": 0.5, "p50": 0.9, "p95": 1.5,
        }
        r = {"lower": 0.8, "center": 1.0, "upper": 1.25, "width_pct": .25}
        out = _final_intelligence({}, row, mc, r)
        self.assertEqual(out["decision"], "TUNGGU")
        self.assertTrue(out["gates"]["out_of_range_probability"] >= .35)


if __name__ == "__main__":
    unittest.main()
