"""Pump-to-DLMM opportunity scanner. Stdlib only."""
from __future__ import annotations
import json, math, time, urllib.request, urllib.parse

UA="pool-dlmm-dashboard/1.0"; DEX_URL="https://api.dexscreener.com"; TIMEOUT=8\nimport os, uuid

GMGN_URL="https://openapi.gmgn.ai"

def fetch_gmgn_rank(limit=100):
    key=os.getenv("GMGN_API_KEY","").strip()
    if not key:
        return []
    q={
        "chain":"sol","interval":"1h","limit":min(limit,100),
        "order_by":"volume","direction":"desc",
        "timestamp":int(time.time()),"client_id":str(uuid.uuid4())
    }
    url=GMGN_URL+"/v1/market/rank?"+urllib.parse.urlencode(q)
    try:
        data=get_json_auth(url,key)
        rank=((data.get("data") or {}).get("rank") or [])
        out=[]
        for x in rank:
            addr=x.get("address")
            if not addr: continue
            out.append({
                "token":addr,"base":x.get("symbol") or "?",
                "price":float(x.get("price") or 0),
                "h1":float(x.get("price_change_percent1h") or x.get("price_change_percent") or 0),
                "m5":float(x.get("price_change_percent5m") or 0),
                "h6":float(x.get("price_change_percent6h") or 0),
                "h24":float(x.get("price_change_percent24h") or 0),
                "v1":float(x.get("volume") or 0),
                "liquidity":float(x.get("liquidity") or 0),
                "buys":int(x.get("buys") or 0),"sells":int(x.get("sells") or 0),
                "dex":x.get("exchange") or x.get("launchpad_platform") or "GMGN",
                "platform":x.get("launchpad_platform") or "",
                "source":"GMGN",
            })
        return out
    except Exception:
        return []

def get_json_auth(url,key):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json","X-APIKEY":key})
    with urllib.request.urlopen(req,timeout=TIMEOUT) as r:return json.loads(r.read().decode())

def gmgn_rows(limit=100):
    rows=[]
    for x in fetch_gmgn_rank(limit):
        h1=x["h1"]; v1=x["v1"]; liq=x["liquidity"]
        if h1 < 8 or v1 < 10000 or liq < 5000:
            continue
        buy_ratio=safe_div(x["buys"],x["buys"]+x["sells"],.5)
        vol_liq=safe_div(v1,liq)
        pump=score01((h1-.08)/1.20)
        vol=score01(math.log1p(max(v1,0))/math.log1p(5_000_000))
        liq_s=score01(math.log1p(max(liq,0))/math.log1p(1_000_000))
        pressure=score01((buy_ratio-.35)/.30)
        score=100*(.34*pump+.32*vol+.16*liq_s+.18*pressure)
        rows.append({
            "price":x["price"],"h1":h1,"m5":x["m5"],"h6":x["h6"],"h24":x["h24"],
            "v1":v1,"v24":0,"liquidity":liq,"buy_ratio":buy_ratio,"vol_liq":vol_liq,
            "age_h":0,"pair":None,"token":x["token"],"dex":x["dex"],
            "url":"https://gmgn.ai/sol/token/"+x["token"],"base":x["base"],"quote":"SOL",
            "score":clamp(score,0,100),"meteora":"meteora" in (x["platform"]+" "+x["dex"]).lower(),
            "source":"GMGN","platform":x["platform"]
        })
    return rows



def get_json(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=TIMEOUT) as r:return json.loads(r.read().decode())

def clamp(x,a,b):return max(a,min(b,x))
def pct(x):return float(x or 0)
def score01(x):return clamp(float(x),0,1)
def safe_div(a,b,d=0):return a/b if b else d

def fetch_seed_tokens():
    out={}
    for path in ["/token-profiles/latest/v1","/token-boosts/latest/v1","/token-boosts/top/v1"]:
        try:
            data=get_json(DEX_URL+path)
            for x in data if isinstance(data,list) else []:
                if x.get("chainId")=="solana" and x.get("tokenAddress"):out[x["tokenAddress"]]=x
        except Exception:pass
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
      "meteora":str(p.get("dexId","")).lower() in {"meteora","meteora-dlmm","meteora-dlmm2"}}

def scan(limit=40,only_meteora=False):
    candidates=[]
    for seed in fetch_seed_tokens():
        for p in fetch_pairs(seed.get("tokenAddress")):
            if p.get("chainId")!="solana":continue
            score,row=rank_pair(p)
            if row["h1"]<15 or row["v1"]<20_000 or row["liquidity"]<10_000:continue
            row["source"]="DexScreener"
            if only_meteora and not row["meteora"]:continue
            candidates.append(row)

    # GMGN adds a second discovery universe; it is intentionally less strict
    # so tokens found only by GMGN can enter the common ranking.
    gm=gmgn_rows(max(100,limit*2))
    if only_meteora: gm=[x for x in gm if x["meteora"]]
    candidates.extend(gm)

    dedup={}
    for x in candidates:
        key=x.get("pair") or ("token:"+x.get("token",""))
        if key not in dedup or x["score"]>dedup[key]["score"]:
            dedup[key]=x
    rows=sorted(dedup.values(),key=lambda x:(x["meteora"],x["score"],x["v1"]),reverse=True)[:limit]
    sources=sorted(set(x.get("source","unknown") for x in rows))
    return {"generated_at":int(time.time()),"count":len(rows),"rows":rows,
            "source":" + ".join(sources) if sources else "none",
            "filters":{"min_1h_pump_dex":15,"min_1h_volume_usd_dex":20000,
                       "min_liquidity_usd_dex":10000,"gmgn_enabled":bool(os.getenv("GMGN_API_KEY")),
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
