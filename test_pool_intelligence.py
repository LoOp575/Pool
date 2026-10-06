import inspect
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import scanner
from dlmm_lp_engine import Candle
from scanner import (
    _final_intelligence,
    _live_review,
    fetch_ohlcv,
    parse_ohlcv,
    scan,
    snapshot_monte_carlo,
)


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
            "p_ever_out_of_range": 0.18, "p_survive_range": 0.82,
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
            "p_ever_out_of_range": 0.62, "p_survive_range": 0.38,
            "p05": 0.5, "p50": 0.9, "p95": 1.5,
        }
        r = {"lower": 0.8, "center": 1.0, "upper": 1.25, "width_pct": .25}
        out = _final_intelligence({}, row, mc, r)
        self.assertEqual(out["decision"], "TUNGGU")
        self.assertTrue(out["gates"]["out_of_range_probability"] >= .35)


def _fake_dex_pair():
    """Pasangan DexScreener palsu untuk test review tanpa jaringan."""
    return {
        "chainId": "solana",
        "dexId": "pumpswap",
        "pairAddress": "FAKEPAIR",
        "baseToken": {"address": "FAKETOKEN", "symbol": "FAKE", "name": "Fake Cat"},
        "quoteToken": {"symbol": "SOL"},
        "priceUsd": "0.001",
        "priceChange": {"m5": 1.2, "h1": 5.0, "h6": 12.0, "h24": 30.0},
        "volume": {"h1": 120000, "h6": 700000, "h24": 2000000},
        "txns": {
            "h1": {"buys": 300, "sells": 200},
            "h6": {"buys": 1500, "sells": 1100},
            "h24": {"buys": 6000, "sells": 5000},
        },
        "liquidity": {"usd": 90000},
        "pairCreatedAt": (time.time() - 48 * 3600) * 1000,
        "url": "https://dexscreener.com/solana/FAKEPAIR",
    }


def _fake_candles(n=140):
    """Deret candle memecoin sintetis (untuk test offline; bukan data pasar)."""
    candles = []
    p = 1.0
    for i in range(n):
        p *= 1 + (0.03 if i % 9 == 0 else -0.02 if i % 7 == 0 else 0.01 * ((i % 5) - 2))
        p = max(p, 1e-9)
        candles.append(Candle(i, p * 0.99, p * 1.03, p * 0.97, p, 1000 + i * 10))
    return candles


def _ohlcv_rows(n, step_seconds=300):
    """Payload gaya GeckoTerminal: [ts, o, h, l, c, v], terbaru dulu."""
    rows = []
    p = 1.0
    for i in range(n):
        p *= 1 + (0.02 if i % 6 == 0 else -0.015 if i % 5 == 0 else 0.005)
        ts = 1_700_000_000 + (n - i) * step_seconds
        rows.append([ts, p * 0.99, p * 1.02, p * 0.98, p, 500 + i * 7])
    return rows


class TestOhlcv(unittest.TestCase):
    def test_parse_ohlcv_sorts_and_skips_bad_rows(self):
        rows = [
            [3000, 1.0, 1.2, 0.9, 1.1, 10],
            "bad",
            [2000, 1.0, 1.1, 0.9, 1.0, 5],
            [1000, 0, 1.1, 0.9, 1.0, 5],           # harga 0 -> dilewati
            [4000, float("nan"), 1, 1, 1, 1],       # NaN -> dilewati
            [2500, 1.0, 1.3, 0.8, 1.2, -1],         # volume < 0 -> dilewati
            None,
        ]
        candles = parse_ohlcv(rows)
        self.assertEqual([c.timestamp for c in candles], [2000, 3000])
        self.assertTrue(all(c.close > 0 for c in candles))

    def test_fetch_ohlcv_never_raises_on_network_failure(self):
        orig = scanner._ext_json
        scanner._ext_json = lambda *a, **k: (_ for _ in ()).throw(OSError("offline"))
        try:
            candles, diag = fetch_ohlcv("solana", "PAIR123", "TOKEN123")
            empty, diag2 = fetch_ohlcv("solana", "")
        finally:
            scanner._ext_json = orig
        self.assertEqual(candles, [])
        self.assertTrue(diag["error"])
        self.assertEqual(empty, [])
        self.assertTrue(diag2["error"])

    def test_fetch_ohlcv_cascade_falls_back_to_search_and_finer_aggregate(self):
        """Pool tidak ditemukan langsung + pool muda -> cari via search, aggregate 15 -> 5."""
        def fake_ext(url, timeout=0):
            if "/search/pools" in url:
                return {"data": [{
                    "id": "solana_PAIR2",
                    "attributes": {"address": "PAIR2"},
                    "relationships": {"base_token": {"data": {"id": "solana_TOKEN123"}}},
                }]}
            if "/ohlcv/minute" in url:
                if "/pools/PAIR123/" in url:
                    raise OSError("404 pool tidak ada")
                if "aggregate=15" in url:
                    return {"data": {"attributes": {"ohlcv_list": _ohlcv_rows(13, 900)}}}
                return {"data": {"attributes": {"ohlcv_list": _ohlcv_rows(40, 300)}}}
            raise OSError("unexpected url: " + url)

        orig = scanner._ext_json
        scanner._ext_json = fake_ext
        try:
            candles, diag = fetch_ohlcv("solana", "PAIR123", "TOKEN123")
        finally:
            scanner._ext_json = orig
        self.assertEqual(len(candles), 40)
        self.assertEqual(diag["network"], "solana")
        self.assertEqual(diag["pool"], "PAIR2")
        self.assertEqual(diag["aggregate"], 5)
        self.assertIsNone(diag["error"])
        self.assertEqual(diag["candles"], 40)


class TestReviewHistoricalPath(unittest.TestCase):
    """Review dengan OHLCV asli harus mengaktifkan jalur A (tanpa jaringan)."""

    def _run(self, candles, diag):
        orig_pair, orig_fetch = scanner.dex_pair, scanner.fetch_ohlcv
        scanner.dex_pair = lambda a: _fake_dex_pair()
        scanner.fetch_ohlcv = lambda c, a, t=None: (candles, diag)
        try:
            return _live_review("FAKEPAIR", horizon_bars=96, mc_paths=400)
        finally:
            scanner.dex_pair, scanner.fetch_ohlcv = orig_pair, orig_fetch

    def test_real_ohlcv_activates_historical_path(self):
        candles = _fake_candles(140)
        diag = {"source": "GeckoTerminal", "network": "solana", "pool": "FAKEPAIR",
                "aggregate": 15, "candles": len(candles), "error": None}
        out = self._run(candles, diag)

        self.assertEqual(out["history"], 140)
        self.assertEqual(out["history_status"], "REAL_OHLCV_GECKOTERMINAL")
        self.assertTrue(out["confidence"].startswith("MEDIUM"))
        self.assertEqual(out["math"]["atr"] is not None, True)
        self.assertGreater(out["math"]["atr"], 0)
        self.assertGreater(out["range"]["lower"], 0)
        self.assertLess(out["range"]["lower"], out["range"]["upper"])

        mc = out["monte_carlo"]
        self.assertAlmostEqual(mc["p_below"] + mc["p_inside"] + mc["p_above"], 1.0, places=9)
        self.assertIn("bootstrap", mc["model"])
        self.assertAlmostEqual(mc["p_out_of_range"], mc["p_ever_out_of_range"])
        self.assertGreaterEqual(mc["p_ever_out_of_range"],
                                mc["p_below"] + mc["p_above"] - 1e-9)
        self.assertIsNotNone(mc["mean_first_escape_bars"])

        # Merton snapshot tetap dibawa sebagai pembanding.
        self.assertIsNotNone(out["monte_carlo_merton"])
        self.assertEqual(out["monte_carlo_status"], "BOOTSTRAP_HISTORICAL")
        self.assertEqual(out["analysis"]["prediction_status"], "HISTORICAL_BOOTSTRAP_ACTIVE")
        self.assertEqual(out["intelligence"]["data_quality"]["confidence"], "MEDIUM")
        self.assertTrue(out["intelligence"]["data_quality"]["real_ohlcv_available"])
        self.assertIn(out["intelligence"]["regime"],
                      {"CHOPPY", "NORMAL", "TRENDING", "EXTREME"})
        self.assertIn(out["decision"], {"MASUK", "TUNGGU", "REBALANCE"})
        # Fee yield terisi (fee APR 0 karena bukan pool Meteora -> tetap valid).
        self.assertIsNotNone(out["risk"]["fee_yield"])
        self.assertEqual(out["risk"]["out_of_range_source"],
                         "BOOTSTRAP_P_EVER_OUT_OF_RANGE")

    def test_snapshot_path_unchanged_without_ohlcv(self):
        diag = {"source": "GeckoTerminal", "network": None, "pool": None,
                "aggregate": None, "candles": 0, "error": "pool muda: 12 candle < 30"}
        out = self._run([], diag)

        self.assertEqual(out["history"], 0)
        self.assertEqual(out["history_status"], "NO_REAL_HISTORY")
        self.assertEqual(out["confidence"], "LOW · LIVE SNAPSHOT")
        self.assertIsNone(out["math"]["atr"])
        self.assertIn("Merton", out["monte_carlo"]["model"])
        self.assertEqual(out["monte_carlo_status"], "ACTIVE")
        self.assertEqual(out["model_status"], "FINAL_INTELLIGENCE_SNAPSHOT")
        self.assertFalse(out["intelligence"]["data_quality"]["real_ohlcv_available"])
        self.assertEqual(out["range"]["multipliers"]["source"], "DexScreener live proxy")

    def test_review_survives_fetch_raising(self):
        orig_pair, orig_fetch = scanner.dex_pair, scanner.fetch_ohlcv
        scanner.dex_pair = lambda a: _fake_dex_pair()
        scanner.fetch_ohlcv = lambda c, a, t=None: (_ for _ in ()).throw(OSError("offline"))
        try:
            out = _live_review("FAKEPAIR", horizon_bars=96, mc_paths=300)
        finally:
            scanner.dex_pair, scanner.fetch_ohlcv = orig_pair, orig_fetch
        self.assertEqual(out["history"], 0)
        self.assertTrue(out["confidence"].startswith("LOW"))
        self.assertIn(out["decision"], {"MASUK", "TUNGGU", "REBALANCE"})


class TestProbabilityContract(unittest.TestCase):
    def test_merton_out_of_range_is_ever_touched(self):
        mc = snapshot_monte_carlo(
            1.0, 1.0, 2.0, 5.0, 12.0,
            10000, 120000, 50000, 120, 100, 220,
            0.8, 1.25, horizon_bars=24, paths=300
        )
        self.assertAlmostEqual(mc["p_out_of_range"], mc["p_ever_out_of_range"])
        self.assertGreaterEqual(
            mc["p_out_of_range"], mc["p_below"] + mc["p_above"] - 1e-9
        )


class TestScannerContract(unittest.TestCase):
    def test_scan_accepts_web_terminal_arguments(self):
        """web_terminal.py calls scan(limit, meteora); the API calls scan()."""
        params = list(inspect.signature(scan).parameters)
        self.assertEqual(params[:2], ["limit", "meteora"])


class TestDashboardScript(unittest.TestCase):
    def test_dashboard_script_parses(self):
        """A broken <script> silently kills the whole dashboard (regression)."""
        if shutil.which("node") is None:
            self.skipTest("node tidak tersedia")
        html = (Path(__file__).resolve().parent / "static" / "index.html").read_text(encoding="utf-8")
        script = html.split("<script>", 1)[1].split("</script>", 1)[0]
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
            fh.write(script)
            path = fh.name
        proc = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        Path(path).unlink(missing_ok=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_dashboard_renders_scan_and_review(self):
        """Run the dashboard script against realistic API payloads (stub DOM)."""
        if shutil.which("node") is None:
            self.skipTest("node tidak tersedia")
        script = Path(__file__).resolve().parent / "test_dashboard_ui.js"
        proc = subprocess.run(
            ["node", str(script), str(Path(__file__).resolve().parent / "static" / "index.html")],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
