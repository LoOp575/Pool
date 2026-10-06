"""Pump-to-DLMM opportunity scanner. Stdlib only."""
from __future__ import annotations
import json, math, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed

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

MEME_BLOCKLIST = {
    "SOL","WSOL","USDC","USDT","USDE","DAI","USD1","PYUSD","FDUSD",
    "BTC","WBTC","ETH","WETH","JITOSOL","MSOL","JUP","JTO","RAY","ORCA",
    "PYTH","LINK","UNI","AAVE","WEN","DRIFT","JUPSOL"
}

MEME_WORDS = {
    "meme","pepe","dog","doge","cat","frog","inu","shib","wif","bonk",
    "wojak","chad","trump","moon","pump","baby","goat","mog","popcat",
    "slerf","pnut","brett","based","kitty","ape","bear","bull",
    "elon","samoyed","floki","toshi","bobo","turbo","andy","giga","sigma",
    "npc","ponke","michi","myro","nosana","wen"
}

UTILITY_WORDS = {
    "protocol","network","finance","swap","dex","oracle","wallet","staking",
    "governance","bridge","index","vault","dao","market","exchange","lend",
    "liquid","yield","restake","perps","perpetual","infrastructure"
}

def _token_text(p):
    base=p.get("baseToken") or {}
    symbol=str(base.get("symbol") or "").strip()
    name=str(base.get("name") or "").strip()
    return symbol, name

def memecoin_score(p):
    """Heuristic score 0..100 for meme-like Solana launches."""
    symbol, name = _token_text(p)
    sym=symbol.lower()
    nm=name.lower()
    hay=(sym+" "+nm).strip()
    dex=str(p.get("dexId") or "").lower()

    if not symbol or symbol.upper() in MEME_BLOCKLIST:
        return 0, ["blocked asset"]

    score=0.0
    reasons=[]

    if any(w in hay for w in MEME_WORDS):
        score += 42
        reasons.append("meme keyword")

    if "pump" in dex and "fun" in dex:
        score += 42
        reasons.append("pump.fun origin")

    if len(symbol) <= 6:
        score += 5
        reasons.append("short ticker")
    if any(ch.isdigit() for ch in symbol):
        score += 2

    ch=p.get("priceChange") or {}
    vol=p.get("volume") or {}
    tx=p.get("txns") or {}
    v1=float(vol.get("h1") or 0)
    liq=float((p.get("liquidity") or {}).get("usd") or 0)
    t1=tx.get("h1") or {}
    buys=int(t1.get("buys") or 0)
    sells=int(t1.get("sells") or 0)
    txns=buys+sells
    created=p.get("pairCreatedAt") or 0
    age_h=max(0,(time.time()*1000-created)/3600000) if created else 999
    h1=abs(float(ch.get("h1") or 0))
    h24=abs(float(ch.get("h24") or 0))

    if age_h <= 24 and txns >= 30:
        score += 18
        reasons.append("new + active")
    elif age_h <= 72 and txns >= 50:
        score += 12
        reasons.append("young + active")
    elif age_h <= 168 and txns >= 100:
        score += 6
        reasons.append("recent + active")

    if liq and liq <= 500_000 and v1 >= max(500, liq*0.10):
        score += 10
        reasons.append("small-liquidity/high-turnover")

    if txns >= 200:
        score += 6
        reasons.append("high transaction activity")

    if h1 >= 8 or h24 >= 15:
        score += 6
        reasons.append("momentum")

    if any(w in hay for w in UTILITY_WORDS):
        score -= 30
        reasons.append("utility keyword")

    return clamp(score,0,100), reasons

def is_memecoin_pair(p):
    score,_=memecoin_score(p)
    return score >= 35

def lp_opportunity_score(h1, h24, m5, volume_1h, liquidity, txns, buy_ratio):
    """
    Balanced LP score 0..100.

    This is a discovery-stage proxy, not an estimated fee APR:
    - Fee potential: turnover relative to liquidity.
    - Range quality: rewards activity without extreme directional movement.
    - Volatility quality: moderate movement beats both dead and explosive markets.
    - Liquidity quality: larger pools get more stability weight.
    - Activity: transaction density.
    - Directional risk: strong one-way moves are penalized.
    """
    vol_liq = safe_div(volume_1h, max(liquidity, 1), 0)
    fee_potential = score01(math.log1p(max(vol_liq, 0)) / math.log1p(20))

    abs_move = abs(h1)
    short_move = abs(m5)
    # Sweet spot for a balanced LP: active but not already running away.
    range_quality = (
        0.55 * (1 - score01(max(abs_move - 4, 0) / 46)) +
        0.25 * (1 - score01(max(short_move - 2, 0) / 18)) +
        0.20 * (1 - score01(max(abs(h24) - 20, 0) / 120))
    )
    range_quality = clamp(range_quality, 0, 1)

    # Moderate volatility is useful for fees; extreme movement raises escape/IL risk.
    movement = 0.45 * abs_move + 0.25 * short_move + 0.30 * abs(h24)
    volatility_quality = clamp(1 - abs(movement - 18) / 45, 0, 1)

    liquidity_quality = score01(math.log1p(max(liquidity, 0)) / math.log1p(2_000_000))
    activity = score01(math.log1p(max(txns, 0)) / math.log1p(10_000))

    balance = 1 - min(abs(buy_ratio - 0.5) / 0.5, 1)
    directional_risk = 1 - score01(
        max(abs(h1) - 8, 0) / 42 +
        max(abs(m5) - 5, 0) / 25
    )

    components = {
        "fee_potential": round(100 * fee_potential, 2),
        "range_quality_proxy": round(100 * range_quality, 2),
        "volatility_quality": round(100 * volatility_quality, 2),
        "liquidity_quality": round(100 * liquidity_quality, 2),
        "activity": round(100 * activity, 2),
        "directional_safety": round(100 * directional_risk, 2),
    }

    score = 100 * (
        0.25 * fee_potential +
        0.25 * range_quality +
        0.15 * volatility_quality +
        0.15 * liquidity_quality +
        0.10 * activity +
        0.10 * directional_risk
    )
    # Balanced LP prefers two-sided flow. Do not destroy the score for momentum.
    score *= 0.92 + 0.08 * balance
    return clamp(score, 0, 100), components


def rank_pair(p):
    ch=p.get("priceChange") or {}; vol=p.get("volume") or {}; tx=p.get("txns") or {}
    h1=float(ch.get("h1") or 0); m5=float(ch.get("m5") or 0)
    h24=float(ch.get("h24") or 0)
    v1=float(vol.get("h1") or 0); liq=float((p.get("liquidity") or {}).get("usd") or 0)
    t1=tx.get("h1") or {}; buys=int(t1.get("buys") or 0); sells=int(t1.get("sells") or 0)
    txns=buys+sells
    created=p.get("pairCreatedAt") or int(time.time()*1000); age_h=max(0,(time.time()*1000-created)/3600000)
    buy_ratio=safe_div(buys,buys+sells,.5); vol_liq=v1/max(liq,1)

    lp_score, lp_components = lp_opportunity_score(
        h1, h24, m5, v1, liq, txns, buy_ratio
    )
    meme_score,meme_reasons=memecoin_score(p)

    # Meme identity is a gate/ranking tie-breaker, not the main LP objective.
    discovery_score=clamp(0.82*lp_score + 0.18*meme_score, 0, 100)

    return discovery_score,{
      "price":float(p.get("priceUsd") or 0),"h1":h1,"m5":m5,
      "h6":float(ch.get("h6") or 0),"h24":h24,"v1":v1,
      "v24":float(vol.get("h24") or 0),"liquidity":liq,"buy_ratio":buy_ratio,
      "vol_liq":vol_liq,"age_h":age_h,"pair":p.get("pairAddress"),"dex":p.get("dexId"),
      "url":p.get("url"),"base":p.get("baseToken",{}).get("symbol"),
      "name":p.get("baseToken",{}).get("name"),"quote":p.get("quoteToken",{}).get("symbol"),
      "score":discovery_score,"lp_score":lp_score,"lp_strategy":"BALANCED",
      "lp_components":lp_components,
      "memecoin_score":meme_score,"meme_reasons":meme_reasons,
      "meteora":str(p.get("dexId","")).lower() in {"meteora","meteora-dlmm","meteora-dlmm2"},
      "token":(p.get("baseToken") or {}).get("address"),
      "gmgn_url":gmgn_url((p.get("baseToken") or {}).get("address"))
    }

def fetch_search_pairs():
    """Broad Solana discovery with token-level diversity."""
    pairs={}
    queries=["pump","meme","pepe","dog","cat","ai","inu","bonk","wif","frog","shib","moon","solana"]

    def one(q):
        try:
            data=get_json(DEX_URL+"/latest/dex/search?"+urllib.parse.urlencode({"q":q}))
            return data.get("pairs") or []
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures=[pool.submit(one,q) for q in queries]
        for f in as_completed(futures):
            for p in f.result():
                if p.get("chainId")=="solana" and p.get("pairAddress"):
                    pairs[p["pairAddress"]]=p

    # Also collect token-profile/boost tokens, then resolve their best pools
    # concurrently. This adds diversity beyond the handful returned by search.
    seeds=[]
    try:
        for path in ["/token-profiles/latest/v1","/token-boosts/latest/v1","/token-boosts/top/v1"]:
            data=get_json(DEX_URL+path)
            for x in data if isinstance(data,list) else []:
                if x.get("chainId")=="solana" and x.get("tokenAddress"):
                    seeds.append(x["tokenAddress"])
    except Exception:
        pass

    def pools(token):
        try:
            return get_json(
                DEX_URL+"/token-pairs/v1/solana/"+urllib.parse.quote(token,safe="")
            ) or []
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures=[pool.submit(pools,t) for t in dict.fromkeys(seeds[:60])]
        for f in as_completed(futures):
            for p in f.result():
                if p.get("chainId")=="solana" and p.get("pairAddress"):
                    pairs[p["pairAddress"]]=p

    return list(pairs.values())


def scan(limit=40,only_meteora=False):
    candidates=[]

    for p in fetch_search_pairs():
        try:
            meme_score,_=memecoin_score(p)
            if meme_score < 35:
                continue
            score,row=rank_pair(p)
            if row["liquidity"] < 500:
                continue
            row["source"]="DexScreener"
            if only_meteora and not row["meteora"]:
                continue
            candidates.append(row)
        except Exception:
            continue

    by_token={}
    for x in candidates:
        token=x.get("token") or ""
        key=token or ("pair:"+str(x.get("pair") or ""))
        old=by_token.get(key)
        if old is None or (x["score"],x["v1"],x["liquidity"]) > (old["score"],old["v1"],old["liquidity"]):
            by_token[key]=x

    rows=sorted(
        by_token.values(),
        key=lambda x:(x["lp_score"],x["score"],x["memecoin_score"],x["v1"],x["liquidity"]),
        reverse=True
    )[:limit]

    sources=sorted(set(x.get("source","unknown") for x in rows))
    return {
        "generated_at":int(time.time()),
        "count":len(rows),
        "rows":rows,
        "source":" + ".join(sources) if sources else "none",
        "filters":{
            "min_1h_pump_dex":0,
            "min_1h_volume_usd_dex":0,
            "min_liquidity_usd_dex":500,
            "memecoin_score_min":35,
            "unique_tokens":True,
            "memecoin_only":True,
            "lp_strategy":"balanced-risk-adjusted",
            "lp_score_primary":True,
            "gmgn_enabled":False,
            "gmgn_mode":"web-reference-only",
            "only_meteora":only_meteora
        }
    }

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
