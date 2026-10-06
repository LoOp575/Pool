"""Pump-to-DLMM opportunity scanner. Stdlib only."""
from __future__ import annotations
import json, math, time, urllib.request, urllib.parse

UA="pool-dlmm-dashboard/1.0"; DEX_URL="https://api.dexscreener.com"; TIMEOUT=8
import os

GMGN_WEB_URL="https://gmgn.ai/sol/token/"

def gmgn_url(token):
    return GMGN_WEB_URL + urllib.parse.quote(str(token or ""), safe="")

def gmgn_rows(limit=100):
    return []
def get_json(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=TIMEOUT) as r:return json.loads(r.read().decode())

def clamp(x,a,b):return max(a,min(b,x))
def pct(x):return float(x or 0)
def score01(x):return clamp(float(x),0,1)
def safe_div(a,b,d=0):return a/b if b else d

def fetch_seed_tokens():
    """Broad Solana discovery using documented public DexScreener endpoints."""
    out={}
    paths=["/token-profiles/latest/v1","/token-boosts/latest/v1","/token-boosts/top/v1",
           "/community-takeovers/latest/v1","/ads/latest/v1"]
    for path in paths:
        try:
            data=get_json(DEX_URL+path)
            for x in data if isinstance(data,list) else []:
                if x.get("chainId")=="solana" and x.get("tokenAddress"):
                    out[x["tokenAddress"]]=x
        except Exception:
            pass

    # Search is a much wider discovery net than profiles/boosts alone.
    # Each query returns a fresh set of pairs; dedup happens below.
    for q in ["SOL","USDC","USDT","pump","meme","dog","cat","pepe","ai","inu","moon"]:
        try:
            data=get_json(DEX_URL+"/latest/dex/search?"+urllib.parse.urlencode({"q":q}))
            for p in (data.get("pairs") or []) if isinstance(data,dict) else []:
                if p.get("chainId")=="solana":
                    token=(p.get("baseToken") or {}).get("address")
                    if token:
                        out[token]={"chainId":"solana","tokenAddress":token}
        except Exception:
            pass
    return list(out.values())

def fetch_pairs(token):
    try:return get_json(f"{DEX_URL}/token-pairs/v1/solana/{urllib.parse.quote(token,safe='')}") or []
    except Exception:return []

def continuation_score(price_change,volume,liquidity,txns,buys,sells,age_h,price_change_5m=0):
    pump=score01((price_change-.12)/1.20)
    vol=score01(math.log1p(max(volume,0))/math.log1p(5_000_000))
    liq=score01(math.log1p(max(liquidity,0))/math.log1p(1_000_000))
    activity=score01(math.log1p(max(txns,0))/math.log1p(10_000))
    buy_ratio=safe_div(buys,buys+sells,.5); pressure=score01((buy_ratio-.35)/.30)
    age=score01(1-math.exp(-max(age_h,0)/12))
    cooldown=1 if price_change_5m<=.08 else score01(1-(price_change_5m-.08)/.20)
    return 100*(.28*pump+.23*vol+.12*liq+.12*activity+.13*pressure+.07*age+.05*cooldown)

def rank_pair(p):
    ch=p.get("priceChange") or {}; vol=p.get("volume") or {}; tx=p.get("txns") or {}
    h1=float(ch.get("h1") or 0); m5=float(ch.get("m5") or 0)
    v1=float(vol.get("h1") or 0); liq=float((p.get("liquidity") or {}).get("usd") or 0)
    t1=tx.get("h1") or {}; buys=int(t1.get("buys") or 0); sells=int(t1.get("sells") or 0)
    created=p.get("pairCreatedAt") or int(time.time()*1000); age_h=max(0,(time.time()*1000-created)/3600000)
    buy_ratio=safe_div(buys,buys+sells,.5); vol_liq=v1/max(liq,1)
    s=continuation_score(h1,v1,liq,buys+sells,buys,sells,age_h,m5)
    s-=clamp(max(0,m5-12)/35,0,1)*15
    s-=clamp((25_000-liq)/25_000,0,1)*20
    return clamp(s,0,100),{"price":float(p.get("priceUsd") or 0),"h1":h1,"m5":m5,
      "h6":float(ch.get("h6") or 0),"h24":float(ch.get("h24") or 0),"v1":v1,
      "v24":float(vol.get("h24") or 0),"liquidity":liq,"buy_ratio":buy_ratio,
      "vol_liq":vol_liq,"age_h":age_h,"pair":p.get("pairAddress"),"dex":p.get("dexId"),
      "url":p.get("url"),"base":p.get("baseToken",{}).get("symbol"),
      "quote":p.get("quoteToken",{}).get("symbol"),"score":clamp(s,0,100),
      "meteora":str(p.get("dexId","")).lower() in {"meteora","meteora-dlmm","meteora-dlmm2"}, "token":(p.get("baseToken") or {}).get("address"), "gmgn_url":gmgn_url((p.get("baseToken") or {}).get("address"))}

def scan(limit=40,only_meteora=False):
    candidates=[]
    for seed in fetch_seed_tokens():
        for p in fetch_pairs(seed.get("tokenAddress")):
            if p.get("chainId")!="solana":continue
            score,row=rank_pair(p)
            if row["v1"]<500 or row["liquidity"]<1_000:continue
            row["source"]="DexScreener"
            if only_meteora and not row["meteora"]:continue
            candidates.append(row)

    dedup={}
    for x in candidates:
        key=x.get("pair") or ("token:"+x.get("token",""))
        if key not in dedup or x["score"]>dedup[key]["score"]:
            dedup[key]=x
    rows=sorted(dedup.values(),key=lambda x:(x["meteora"],x["score"],x["v1"]),reverse=True)[:limit]
    sources=sorted(set(x.get("source","unknown") for x in rows))
    return {"generated_at":int(time.time()),"count":len(rows),"rows":rows,
            "source":" + ".join(sources) if sources else "none",
            "filters":{"min_1h_pump_dex":0,"min_1h_volume_usd_dex":500,
                       "min_liquidity_usd_dex":1000,"gmgn_enabled":False,"gmgn_mode":"web-reference-only",
                       "only_meteora":only_meteora}}

if __name__=="__main__":
 import argparse
 ap=argparse.ArgumentParser();ap.add_argument("--limit",type=int,default=40);ap.add_argument("--meteora",action="store_true");a=ap.parse_args()
 print(json.dumps(scan(a.limit,a.meteora),indent=2))


def fetch_ohlcv(pool_address, limit=200):
    """Fetch public OHLCV history for review. GeckoTerminal uses DexScreener pair/pool addresses."""
    base = "https://api.geckoterminal.com/api/v2/networks/solana/pools/"
    endpoints = [
        f"{base}{urllib.parse.quote(pool_address, safe='')}/ohlcv/minute?aggregate=5&limit={min(limit,1000)}",
        f"{base}{urllib.parse.quote(pool_address, safe='')}/ohlcv/hour?aggregate=1&limit={min(limit,1000)}",
    ]
    for url in endpoints:
        try:
            data = get_json(url)
            rows = ((data.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
            candles = []
            for row in rows:
                if len(row) < 6:
                    continue
                ts, open_, high, low, close, volume = row[:6]
                candles.append({
                    "timestamp": int(ts),
                    "open": float(open_),
                    "high": float(high),
                    "low": float(low),
                    "close": float(close),
                    "volume": float(volume),
                })
            if len(candles) >= 20:
                return list(reversed(candles))
        except Exception:
            pass
    raise RuntimeError("Riwayat OHLCV tidak tersedia untuk pool ini.")

def review_pair(pool_address, fee_apr=0, horizon_bars=24, mc_paths=1500):
    from dlmm_lp_engine import Candle, math_engine, classify_regime, range_engine, bootstrap_monte_carlo

    candles_raw = fetch_ohlcv(pool_address, 200)
    candles = [Candle(**x) for x in candles_raw]
    metrics = math_engine(candles)
    regime = classify_regime(metrics)
    closes = [x.close for x in candles]
    range_plan = range_engine(metrics, regime, closes)
    mc = bootstrap_monte_carlo(
        closes, range_plan.lower_price, range_plan.upper_price,
        horizon_bars=horizon_bars, paths=mc_paths
    )

    from dlmm_lp_engine import risk_engine, rebalance_engine
    risk = risk_engine(metrics, range_plan, mc, fee_apr=fee_apr, horizon_days=max(1, horizon_bars // 24))
    rebalance = rebalance_engine(metrics, range_plan, mc, regime)

    return {
        "price": metrics.price,
        "regime": regime.value,
        "math": {
            "price": metrics.price,
            "atr": metrics.atr,
            "atr_pct": metrics.atr_pct,
            "volatility": metrics.volatility,
            "volatility_ratio": metrics.volatility_ratio,
            "z_score": metrics.z_score,
            "trend_strength": metrics.trend_strength,
            "volume_pressure": metrics.volume_pressure,
            "entropy": metrics.entropy,
            "liquidity_force": metrics.liquidity_force,
            "mean_reversion_force": metrics.mean_reversion_force,
            "rvol": metrics.rvol,
        },
        "range": {
            "lower": range_plan.lower_price,
            "center": range_plan.center_price,
            "upper": range_plan.upper_price,
            "width_pct": range_plan.width_pct,
            "multipliers": range_plan.multipliers,
        },
        "monte_carlo": {
            "paths": mc.paths,
            "horizon_bars": mc.horizon_bars,
            "p_below": mc.p_below,
            "p_above": mc.p_above,
            "p_inside": max(0, 1 - mc.p_out_of_range),
            "p_out_of_range": mc.p_out_of_range,
            "expected_terminal_price": mc.expected_terminal_price,
            "p05": mc.p5,
            "p50": mc.p50,
            "p95": mc.p95,
        },
        "risk": {
            "score": risk.risk_score,
            "out_of_range": risk.probability_out_of_range,
            "il_proxy": risk.il_proxy,
            "fee_yield": risk.expected_fee_yield,
            "fee_il_ratio": risk.fee_to_il_ratio,
            "notes": risk.notes,
        },
        "rebalance": {
            "rebalance": rebalance.rebalance,
            "urgency": rebalance.urgency,
            "reasons": rebalance.reasons,
        },
        "history": len(candles),
        "bin": None,
        "bin_note": "Active bin + bin step harus diambil dari metadata pool Meteora sebelum bin ID dihitung.",
    }
