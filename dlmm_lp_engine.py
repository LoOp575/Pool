"""Meteora DLMM LP Position Engine.
OHLCV -> math -> regime -> adaptive range -> bins -> liquidity -> risk -> rebalance.
"""
from __future__ import annotations
import math, random, statistics
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, List, Sequence, Tuple

EPS=1e-12
def clamp(x,lo,hi): return max(lo,min(hi,x))
def safe_div(a,b,default=0.0): return a/b if abs(b)>EPS else default
def mean(xs,default=0.0): return statistics.fmean(xs) if xs else default
def stdev(xs): return statistics.stdev(xs) if len(xs)>=2 else 0.0

def log_returns(prices):
    if len(prices)<2 or any(p<=0 or not math.isfinite(p) for p in prices): raise ValueError("prices must contain >=2 finite positive values")
    return [math.log(prices[i]/prices[i-1]) for i in range(1,len(prices))]

def atr(high,low,close,period=14):
    if not close: return 0.0
    tr=[high[0]-low[0]]
    for i in range(1,len(close)): tr.append(max(high[i]-low[i],abs(high[i]-close[i-1]),abs(low[i]-close[i-1])))
    period=max(2,min(period,len(tr))); a=mean(tr[:period])
    for x in tr[period:]: a=(a*(period-1)+x)/period
    return a

def permutation_entropy(prices,order=3):
    if len(prices)<order: return 0.5
    counts={}; total=0
    for i in range(len(prices)-order+1):
        w=prices[i:i+order]; pat=tuple(sorted(range(order),key=lambda j:(w[j],j)))
        counts[pat]=counts.get(pat,0)+1; total+=1
    h=-sum((n/total)*math.log2(n/total) for n in counts.values())
    return clamp(h/math.log2(math.factorial(order)),0,1)

def z_score(price,reference):
    logs=[math.log(p) for p in reference if p>0]
    return safe_div(math.log(price)-mean(logs),stdev(logs)) if len(logs)>=2 else 0.0

def volume_pressure(returns,volumes):
    if len(returns)!=len(volumes) or not returns: return 0.0
    return clamp(safe_div(sum(r*v for r,v in zip(returns,volumes)),sum(abs(r)*v for r,v in zip(returns,volumes))),-1,1)

def trend_strength(returns):
    if len(returns)<3: return 0.0
    return clamp(safe_div(abs(sum(returns)),math.sqrt(sum(r*r for r in returns))),0,1)

def mean_reversion_force(z,trend): return -math.tanh(z/2)*(1-clamp(trend,0,1))

def liquidity_force(price,high,low,atr_pct):
    if high<=low or price<=0:return 0.0
    u=max(atr_pct,1e-9); dh=max(high-price,0)/price/u; dl=max(price-low,0)/price/u
    return clamp(1/(1+dh*dh)-1/(1+dl*dl),-1,1)

class Regime(str,Enum): CHOPPY="CHOPPY"; NORMAL="NORMAL"; TRENDING="TRENDING"; EXTREME="EXTREME"
class Distribution(str,Enum): SPOT="SPOT"; CURVE="CURVE"; BID_ASK="BID-ASK"

@dataclass(frozen=True)
class Candle: timestamp:int; open:float; high:float; low:float; close:float; volume:float
@dataclass(frozen=True)
class PoolConfig:
    active_bin_id:int; bin_step_bps:float; min_bin_id:int|None=None; max_bin_id:int|None=None
@dataclass
class MathSnapshot:
    price:float; atr:float; atr_pct:float; volatility:float; volatility_ratio:float; z_score:float
    trend_strength:float; volume_pressure:float; entropy:float; liquidity_force:float
    mean_reversion_force:float; rvol:float
@dataclass
class RangePlan:
    center_price:float; lower_price:float; upper_price:float; width_pct:float; multipliers:Dict[str,float]
@dataclass
class BinPlan:
    lower_bin:int; active_bin:int; upper_bin:int; lower_price:float; active_price:float; upper_price:float; bin_step_ratio:float
@dataclass
class BinLiquidity:
    bin_id:int; price:float; weight:float; token_a_weight:float; token_b_weight:float
@dataclass
class MonteCarloResult:
    paths:int; horizon_bars:int; p_below:float; p_above:float; p_out_of_range:float
    expected_terminal_price:float; p5:float; p50:float; p95:float
@dataclass
class RiskPlan:
    risk_score:float; probability_out_of_range:float; il_proxy:float
    expected_fee_yield:float; fee_to_il_ratio:float; notes:List[str]
@dataclass
class RebalancePlan: rebalance:bool; urgency:str; reasons:List[str]
@dataclass
class DLMMPosition:
    math:MathSnapshot; regime:Regime; range:RangePlan; bins:BinPlan
    distribution:Distribution; liquidity:List[BinLiquidity]; monte_carlo:MonteCarloResult
    risk:RiskPlan; rebalance:RebalancePlan
    def to_dict(self):
        return {"math":asdict(self.math),"regime":self.regime.value,"range":asdict(self.range),
                "bins":asdict(self.bins),"distribution":self.distribution.value,
                "liquidity":[asdict(x) for x in self.liquidity],"monte_carlo":asdict(self.monte_carlo),
                "risk":asdict(self.risk),"rebalance":asdict(self.rebalance)}

def extract(candles):
    if len(candles)<30: raise ValueError("minimal 30 candle untuk DLMM engine")
    return ([c.open for c in candles],[c.high for c in candles],[c.low for c in candles],
            [c.close for c in candles],[c.volume for c in candles])

def math_engine(candles,atr_period=14):
    _,highs,lows,closes,volumes=extract(candles); rs=log_returns(closes); price=closes[-1]
    a=atr(highs,lows,closes,atr_period); atr_pct=safe_div(a,price)
    sigma=stdev(rs); cur=stdev(rs[-min(30,len(rs)):])
    base=[stdev(rs[i-29:i+1]) for i in range(29,len(rs))]
    baseline=statistics.median(base) if base else sigma
    vr=safe_div(cur,baseline,1.0); rvol=safe_div(mean(volumes[-3:]),mean(volumes[:-3]),1.0)
    rsw=rs[-min(72,len(rs)):]; trend=trend_strength(rsw); z=z_score(price,closes[:-1])
    n=min(len(rs),len(volumes)-1); vp=volume_pressure(rs[-n:],volumes[1:][-n:])
    ent=permutation_entropy(closes[-min(120,len(closes)):]); mr=mean_reversion_force(z,trend)
    lf=liquidity_force(price,max(highs[-72:]),min(lows[-72:]),atr_pct)
    return MathSnapshot(price,a,atr_pct,sigma,vr,z,trend,vp,ent,lf,mr,rvol)

def classify_regime(m):
    if abs(m.z_score)>=2.75 or m.volatility_ratio>=2.0 or m.atr_pct>=0.12:return Regime.EXTREME
    if m.trend_strength>=0.62 and m.entropy<=0.78:return Regime.TRENDING
    if m.trend_strength<=0.32 and m.entropy>=0.68:return Regime.CHOPPY
    return Regime.NORMAL

def range_engine(m,regime,closes):
    fair=math.exp(mean([math.log(p) for p in closes[-min(72,len(closes)):]]))
    mrw=clamp(abs(m.mean_reversion_force),0,.75); center=m.price*(1-mrw)+fair*mrw
    tm={Regime.CHOPPY:.85,Regime.NORMAL:1,Regime.TRENDING:1.30,Regime.EXTREME:1.65}[regime]
    vm=clamp(.85+.35*m.volatility_ratio,.75,1.70); em=.80+.65*m.entropy; mm=1-.22*abs(m.mean_reversion_force)
    width=m.atr_pct*tm*vm*em*mm
    skew=clamp(.12*(m.volume_pressure+m.liquidity_force),-.20,.20)
    lower=center*(1-width*(1+skew)); upper=center*(1+width*(1-skew))
    return RangePlan(center,lower,upper,width,{"atr_pct":m.atr_pct,"trend":tm,"volatility":vm,"entropy":em,"mean_reversion":mm,"directional_skew":skew})

def price_at_bin(active_price,active_bin,bin_id,step_bps):
    return active_price*(1+step_bps/10000)**(bin_id-active_bin)

def bin_engine(r,pool):
    if pool.bin_step_bps<=0: raise ValueError("bin_step_bps must be > 0")
    step=math.log1p(pool.bin_step_bps/10000)
    lo=pool.active_bin_id+math.floor(math.log(r.lower_price/r.center_price)/step)
    hi=pool.active_bin_id+math.ceil(math.log(r.upper_price/r.center_price)/step)
    lo=min(lo,pool.active_bin_id); hi=max(hi,pool.active_bin_id)
    if pool.min_bin_id is not None: lo=max(lo,pool.min_bin_id)
    if pool.max_bin_id is not None: hi=min(hi,pool.max_bin_id)
    ratio=1+pool.bin_step_bps/10000
    return BinPlan(lo,pool.active_bin_id,hi,price_at_bin(r.center_price,pool.active_bin_id,lo,pool.bin_step_bps),
                   r.center_price,price_at_bin(r.center_price,pool.active_bin_id,hi,pool.bin_step_bps),ratio)

def liquidity_distribution(b,m,distribution,max_bins=201):
    ids=list(range(b.lower_bin,b.upper_bin+1)); half=max_bins//2
    if len(ids)>max_bins: ids=list(range(max(b.lower_bin,b.active_bin-half),min(b.upper_bin,b.active_bin+half)+1))
    width=max(abs(b.upper_bin-b.lower_bin),1); skew=clamp(.8*m.volume_pressure+.6*m.liquidity_force,-1,1)
    raw=[]
    for bid in ids:
        x=(bid-b.active_bin)/width
        if distribution==Distribution.SPOT:w=math.exp(-5*x*x)
        elif distribution==Distribution.CURVE:w=max(0,1-abs(x)*1.35)**2
        else:w=math.exp(-7*(x-.22*skew)**2)
        raw.append(max(w,1e-12))
    total=sum(raw); out=[]
    for bid,w in zip(ids,raw):
        price=b.active_price*b.bin_step_ratio**(bid-b.active_bin); weight=w/total
        ta=clamp(.5-.5*math.tanh((price/b.active_price-1)*12),0,1); tb=1-ta
        out.append(BinLiquidity(bid,price,weight,ta*weight,tb*weight))
    return out

def bootstrap_monte_carlo(closes,lower,upper,horizon_bars=24,paths=2000,seed=7):
    rs=log_returns(closes); rng=random.Random(seed); terminals=[]; below=above=0
    for _ in range(paths):
        p=closes[-1]; mn=mx=p
        for _ in range(horizon_bars):
            p*=math.exp(rng.choice(rs)); mn=min(mn,p); mx=max(mx,p)
        terminals.append(p); below+=mn<lower; above+=mx>upper
    terminals.sort(); n=len(terminals)
    return MonteCarloResult(paths,horizon_bars,below/n,above/n,(below+above)/n,mean(terminals),
                            terminals[max(0,int(.05*n)-1)],terminals[max(0,int(.50*n)-1)],terminals[max(0,int(.95*n)-1)])

def risk_engine(m,r,mc,fee_apr=0,horizon_days=1):
    q=safe_div(mc.expected_terminal_price,r.center_price,1); il=2*math.sqrt(q)/(1+q)-1
    fy=max(fee_apr,0)*horizon_days/365; ratio=safe_div(fy,abs(il),float("inf"))
    score=100*(.45*mc.p_out_of_range+.20*clamp(m.volatility_ratio/2,0,1)+.15*abs(m.z_score)/3+.10*m.trend_strength+.10*(1-abs(m.mean_reversion_force)))
    notes=[]
    if mc.p_out_of_range>=.35:notes.append("probabilitas out-of-range tinggi")
    if m.volatility_ratio>=1.5:notes.append("volatilitas meningkat")
    if abs(m.z_score)>=2:notes.append("harga jauh dari mean")
    if fee_apr>0 and ratio<1:notes.append("fee proyeksi belum menutup IL proxy")
    return RiskPlan(clamp(score,0,100),mc.p_out_of_range,il,fy,ratio,notes)

def rebalance_engine(m,r,mc,regime):
    reasons=[]; boundary=min(abs(m.price-r.lower_price)/m.price,abs(r.upper_price-m.price)/m.price)
    if boundary<=.20*r.width_pct: reasons.append("harga mendekati boundary range")
    if regime==Regime.EXTREME: reasons.append("regime EXTREME")
    if m.volatility_ratio>=1.75: reasons.append("volatilitas melonjak")
    if mc.p_out_of_range>=.30: reasons.append("P(out-of-range) tinggi")
    if abs(m.volume_pressure)>=.75 and m.trend_strength>=.60: reasons.append("tekanan volume + trend kuat")
    urgency="HIGH" if len(reasons)>=3 or mc.p_out_of_range>=.50 else "MEDIUM" if reasons else "NONE"
    return RebalancePlan(bool(reasons),urgency,reasons)

def choose_distribution(m,regime):
    if regime==Regime.CHOPPY and abs(m.z_score)<1:return Distribution.CURVE
    if regime==Regime.TRENDING or abs(m.volume_pressure)>.65:return Distribution.BID_ASK
    return Distribution.SPOT

def analyze(candles,pool,fee_apr=0,horizon_bars=24,mc_paths=2000,distribution=None):
    _,_,_,closes,_=extract(candles); m=math_engine(candles); regime=classify_regime(m)
    r=range_engine(m,regime,closes); b=bin_engine(r,pool); dist=distribution or choose_distribution(m,regime)
    liq=liquidity_distribution(b,m,dist); mc=bootstrap_monte_carlo(closes,r.lower_price,r.upper_price,horizon_bars,mc_paths)
    risk=risk_engine(m,r,mc,fee_apr,horizon_bars); rb=rebalance_engine(m,r,mc,regime)
    return DLMMPosition(m,regime,r,b,dist,liq,mc,risk,rb)

def format_report(p,top_bins=9):
    m,r,b,mc,risk,rb=p.math,p.range,p.bins,p.monte_carlo,p.risk,p.rebalance
    ranked=sorted(p.liquidity,key=lambda x:x.weight,reverse=True)[:top_bins]
    lines=["=== METEORA DLMM LP ENGINE ===",f"REGIME       : {p.regime.value}",f"PRICE        : {m.price:.10g}",
           f"ATR / ATR%   : {m.atr:.10g} / {m.atr_pct*100:.3f}%",f"VOL / RATIO  : {m.volatility:.6f} / {m.volatility_ratio:.2f}x",
           f"Z-SCORE      : {m.z_score:.3f}",f"TREND        : {m.trend_strength:.3f}",f"VOL PRESSURE : {m.volume_pressure:+.3f}",
           f"ENTROPY      : {m.entropy:.3f}",f"LIQ FORCE    : {m.liquidity_force:+.3f}",f"MEAN REVERT  : {m.mean_reversion_force:+.3f}",
           "",f"RANGE        : {r.lower_price:.10g} -> {r.center_price:.10g} -> {r.upper_price:.10g}",f"WIDTH        : {r.width_pct*100:.3f}%",
           f"BINS         : {b.lower_bin} -> {b.active_bin} -> {b.upper_bin}",f"DISTRIBUTION : {p.distribution.value}","",
           f"MC P(OUT)    : {mc.p_out_of_range*100:.2f}%",f"MC P(BELOW)  : {mc.p_below*100:.2f}% | P(ABOVE): {mc.p_above*100:.2f}%",
           f"RISK SCORE   : {risk.risk_score:.1f}/100",f"IL PROXY     : {risk.il_proxy*100:.3f}%",f"FEE YIELD    : {risk.expected_fee_yield*100:.4f}%",
           "",f"REBALANCE    : {'YES' if rb.rebalance else 'NO'} ({rb.urgency})",f"REASONS      : {', '.join(rb.reasons) if rb.reasons else 'range masih sehat'}","",
           "TOP LIQUIDITY BINS:"]
    lines += [f"  {x.bin_id:>8} price={x.price:.10g} weight={x.weight*100:6.2f}%" for x in ranked]
    return "\\n".join(lines)
