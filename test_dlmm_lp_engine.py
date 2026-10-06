import unittest
from dlmm_lp_engine import Candle,PoolConfig,Regime,atr,permutation_entropy,bin_engine,range_engine,MathSnapshot,analyze

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

if __name__=="__main__": unittest.main()
