import unittest
from dlmm_lp_engine import Candle,PoolConfig,Regime,atr,permutation_entropy,bin_engine,range_engine,analyze,format_report,bootstrap_monte_carlo,MathSnapshot

class TestDLMM(unittest.TestCase):
    def test_atr(self):
        self.assertGreater(atr([101,103,104,105],[99,100,101,102],[100,102,103,104]),0)
    def test_entropy(self):
        e=permutation_entropy(list(range(20)))
        self.assertGreaterEqual(e,0); self.assertLessEqual(e,1)
    def test_bins(self):
        m=MathSnapshot(100,2,.02,.01,1,0,.5,0,.5,0,0,1)
        r=range_engine(m,Regime.NORMAL,[100+i*.1 for i in range(60)])
        b=bin_engine(r,PoolConfig(1000,25))
        self.assertLessEqual(b.lower_bin,b.active_bin); self.assertGreaterEqual(b.upper_bin,b.active_bin)
    def test_pipeline(self):
        candles=[]; p=100
        for i in range(120):
            p*=1+(0.001 if i%7 else -0.002)
            candles.append(Candle(i,p*.999,p*1.002,p*.997,p,1000+i*2))
        x=analyze(candles,PoolConfig(1000,25),mc_paths=100)
        self.assertGreaterEqual(x.risk.risk_score,0); self.assertLessEqual(x.risk.risk_score,100)

    def _memecoin_candles(self):
        # Volatile memecoin-like series: violent swings, ATR% very large.
        candles=[]; p=1.0
        for i in range(140):
            p*= 1+(0.35 if i%11==0 else -0.28 if i%7==0 else 0.04*((i%5)-2))
            p=max(p,1e-6)
            candles.append(Candle(i,p*0.92,p*1.15,p*0.88,p,5000+i*10))
        return candles

    def test_memecoin_range_stays_positive(self):
        # Regression: ATR% memecoin bisa >100%, dulu menghasilkan lower price
        # negatif lalu bin_engine melempar math domain error.
        m=MathSnapshot(1.0,0.9,0.9,0.5,2.0,0.0,0.5,0.0,0.9,0.0,0.0,1.0)
        r=range_engine(m,Regime.EXTREME,[1+0.05*i for i in range(80)])
        self.assertGreater(r.lower_price,0)
        self.assertGreater(r.upper_price,r.center_price)
        b=bin_engine(r,PoolConfig(1000,25))
        self.assertLessEqual(b.lower_bin,b.active_bin)

    def test_memecoin_pipeline_runs(self):
        x=analyze(self._memecoin_candles(),PoolConfig(1000,25),mc_paths=300)
        self.assertGreater(x.range.lower_price,0)
        self.assertLess(x.range.lower_price,x.range.upper_price)
        self.assertGreaterEqual(x.monte_carlo.p_out_of_range,x.monte_carlo.p_below+x.monte_carlo.p_above-1e-9)

    def test_monte_carlo_terminal_split_is_a_distribution(self):
        closes=[100*(1.01**((i%9)-4)) for i in range(120)]
        mc=bootstrap_monte_carlo(closes,95.0,105.0,horizon_bars=48,paths=400)
        total=mc.p_below+mc.p_above
        self.assertLessEqual(total,1.0+1e-9)
        self.assertGreaterEqual(mc.p_out_of_range,total-1e-9)

    def test_bootstrap_reports_first_escape_timing(self):
        # Range sempit -> hampir semua jalur menyentuh batas; escape tercatat.
        closes=[100*(1.01**((i%9)-4)) for i in range(120)]
        tight=bootstrap_monte_carlo(closes,99.0,101.0,horizon_bars=48,paths=300)
        self.assertGreater(tight.p_out_of_range,0.5)
        self.assertIsNotNone(tight.mean_first_escape_bars)
        self.assertLessEqual(tight.mean_first_escape_bars,48)
        # Range sangat lebar -> tak ada yang keluar -> timing None.
        wide=bootstrap_monte_carlo(closes,1.0,1e9,horizon_bars=48,paths=100)
        self.assertEqual(wide.p_out_of_range,0)
        self.assertIsNone(wide.mean_first_escape_bars)

    def test_fee_yield_uses_horizon_in_days(self):
        # 96 bar x 15 menit = 1 hari -> yield = fee_apr/365.
        x=analyze(self._memecoin_candles(),PoolConfig(1000,25),fee_apr=365,
                   horizon_bars=96,mc_paths=200)
        self.assertAlmostEqual(x.risk.expected_fee_yield,1.0,places=9)

    def test_format_report_has_real_newlines(self):
        x=analyze(self._memecoin_candles(),PoolConfig(1000,25),mc_paths=200)
        report=format_report(x)
        self.assertGreater(len(report.splitlines()),10)
        self.assertNotIn("\\n",report)

if __name__=="__main__": unittest.main()
