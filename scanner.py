"""Pump-to-DLMM opportunity scanner. Stdlib only."""
from __future__ import annotations
import json, math, random, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dlmm_lp_engine import Candle, math_engine, classify_regime, range_engine, bootstrap_monte_carlo, risk_engine

UA="pool-dlmm-dashboard/1.0"; DEX_URL="https://api.dexscreener.com"; TIMEOUT=8
import os

GMGN_WEB_URL="https://gmgn.ai/sol/token/"

def gmgn_url(token):
    return GMGN_WEB_URL + urllib.parse.quote(str(token or ""), safe="")

def gmgn_rows(limit=100):
    return []
def get_json(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"application/json;version=20230203"})
    with urllib.request.urlopen(req,timeout=TIMEOUT) as r:return json.loads(r.read().decode())

def clamp(x,a,b):return max(a,min(b,x))
def pct(x):return float(x or 0)
def score01(x):return clamp(float(x),0,1)
def safe_div(a,b,d=0):return a/b if b else d

FORMULA_ENGINE_VERSION = "POOL-INTEL-3"


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

def lp_opportunity_score(h1, h24, m5, volume_1h, liquidity, txns, buy_ratio, strategy="balanced"):
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

    # Each LP mode has a different volatility sweet spot.
    movement = 0.45 * abs_move + 0.25 * short_move + 0.30 * abs(h24)
    target_vol = {"conservative": 10, "balanced": 18, "aggressive": 28}.get(strategy, 18)
    volatility_quality = clamp(1 - abs(movement - target_vol) / 45, 0, 1)

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

    weights = {
        "conservative": (0.18, 0.30, 0.17, 0.20, 0.05, 0.10),
        "balanced":     (0.25, 0.25, 0.15, 0.15, 0.10, 0.10),
        "aggressive":   (0.32, 0.18, 0.10, 0.10, 0.15, 0.15),
    }.get(strategy, (0.25, 0.25, 0.15, 0.15, 0.10, 0.10))
    score = 100 * (
        weights[0] * fee_potential +
        weights[1] * range_quality +
        weights[2] * volatility_quality +
        weights[3] * liquidity_quality +
        weights[4] * activity +
        weights[5] * directional_risk
    )
    # Balanced LP prefers two-sided flow. Do not destroy the score for momentum.
    score *= 0.92 + 0.08 * balance
    return clamp(score, 0, 100), components


def fresh_entry_score(p, meme_score, age_h, h1, h6, h24, v1, v24, liq, txns, buy_ratio):
    """Fresh Meme Entry Score: young + active + early momentum."""
    if age_h < 0.5 or age_h > 168:
        return 0, ["outside fresh window"]

    # Main LP discovery zone is 1-3 days. Very young launches remain
    # eligible, but they do not receive the same freshness bonus.
    if age_h < 6:
        freshness = 0.60
    elif age_h < 24:
        freshness = 0.60 + 0.25 * ((age_h - 6) / 18)
    elif age_h <= 48:
        freshness = 1.00
    elif age_h <= 72:
        freshness = 0.90
    elif age_h <= 96:
        freshness = 0.65
    else:
        freshness = 0.65 * (1 - ((age_h - 96) / 72))
    freshness = clamp(freshness, 0, 1)

    v1_liq = safe_div(v1, max(liq, 1), 0)
    v24_liq = safe_div(v24, max(liq, 1), 0)
    turnover = clamp(
        0.55 * score01(math.log1p(v1_liq) / math.log1p(8)) +
        0.45 * score01(math.log1p(v24_liq) / math.log1p(25)), 0, 1
    )

    h1_signal = score01((h1 - 1) / 24)
    h6_signal = score01((h6 - 5) / 90)
    h24_signal = score01((h24 - 10) / 180)
    momentum = 0.42 * h1_signal + 0.33 * h6_signal + 0.25 * h24_signal

    activity = score01(math.log1p(txns) / math.log1p(1200))
    pressure = score01((buy_ratio - 0.43) / 0.24)
    liquidity_band = (
        score01(math.log1p(max(liq, 0)) / math.log1p(250_000))
        if liq <= 250_000 else
        score01(1 - (liq - 250_000) / 1_500_000)
    )
    meme_identity = score01(meme_score / 70)

    score = 100 * (
        0.20 * freshness + 0.24 * turnover + 0.22 * momentum +
        0.13 * activity + 0.10 * pressure + 0.06 * liquidity_band +
        0.05 * meme_identity
    )

    runaway_penalty = (
        0.18 * score01((h1 - 35) / 80) +
        0.10 * score01((h24 - 250) / 750)
    )
    score *= (1 - runaway_penalty)

    reasons=[]
    if age_h <= 24: reasons.append("fresh <24h")
    elif age_h <= 72: reasons.append("young <3d")
    else: reasons.append("recent <7d")
    if v1_liq >= 0.25: reasons.append("volume ignition")
    if h1 >= 3 or h6 >= 10: reasons.append("momentum")
    if buy_ratio >= 0.56: reasons.append("buy pressure")
    if txns >= 100: reasons.append("active flow")
    if h24 >= 30: reasons.append("already moving")
    if h1 > 35: reasons.append("runaway penalty")
    return clamp(score, 0, 100), reasons


def rank_pair(p, strategy="balanced"):
    ch=p.get("priceChange") or {}; vol=p.get("volume") or {}; tx=p.get("txns") or {}
    h1=float(ch.get("h1") or 0); m5=float(ch.get("m5") or 0)
    h6=float(ch.get("h6") or 0); h24=float(ch.get("h24") or 0)
    v1=float(vol.get("h1") or 0); v24=float(vol.get("h24") or 0)
    liq=float((p.get("liquidity") or {}).get("usd") or 0)
    t1=tx.get("h1") or {}; buys=int(t1.get("buys") or 0); sells=int(t1.get("sells") or 0)
    txns=buys+sells
    created=p.get("pairCreatedAt") or int(time.time()*1000); age_h=max(0,(time.time()*1000-created)/3600000)
    buy_ratio=safe_div(buys,buys+sells,.5); vol_liq=v1/max(liq,1)

    lp_score, lp_components = lp_opportunity_score(h1, h24, m5, v1, liq, txns, buy_ratio, strategy=strategy)
    meme_score,meme_reasons=memecoin_score(p)
    fresh_score,fresh_reasons=fresh_entry_score(p,meme_score,age_h,h1,h6,h24,v1,v24,liq,txns,buy_ratio)

    discovery_score=clamp(0.68*fresh_score + 0.20*lp_score + 0.12*meme_score, 0, 100)
    return discovery_score,{
      "price":float(p.get("priceUsd") or 0),"h1":h1,"m5":m5,
      "h6":float(ch.get("h6") or 0),"h24":h24,"v1":v1,
      "v24":float(vol.get("h24") or 0),"liquidity":liq,"buy_ratio":buy_ratio,
      "vol_liq":vol_liq,"age_h":age_h,"pair":p.get("pairAddress"),"dex":p.get("dexId"),
      "url":p.get("url"),"base":p.get("baseToken",{}).get("symbol"),
      "name":p.get("baseToken",{}).get("name"),"quote":p.get("quoteToken",{}).get("symbol"),
      "score":discovery_score,"lp_score":lp_score,"lp_strategy":strategy.upper(),
      "lp_components":lp_components,
      "memecoin_score":meme_score,"meme_reasons":meme_reasons,
      "fresh_score":fresh_score,"fresh_reasons":fresh_reasons,
      "meteora":str(p.get("dexId","")).lower() in {"meteora","meteora-dlmm","meteora-dlmm2"},
      "token":(p.get("baseToken") or {}).get("address"),
      "gmgn_url":gmgn_url((p.get("baseToken") or {}).get("address"))
    }

def fetch_search_pairs():
    """Fast DexScreener-only meme discovery with a bounded fallback.

    Discovery never runs LP math, ranking, Monte Carlo, or intelligence.
    The search response is used directly; if search is unavailable, a small
    DexScreener token-profile/boost fallback expands only a bounded set.
    """
    pairs = {}
    diag = {
        "search_queries_total": 0,
        "search_queries_ok": 0,
        "search_pairs_raw": 0,
        "solana_pairs": 0,
        "fallback_tokens": 0,
        "fallback_pairs": 0,
        "errors": [],
    }

    def record_error(stage, detail):
        if len(diag["errors"]) < 12:
            diag["errors"].append({
                "stage": stage,
                "error": str(detail)[:180],
            })

    def add_pair(p):
        if not isinstance(p, dict) or p.get("chainId") != "solana":
            return
        address = p.get("pairAddress")
        if address:
            pairs[address] = p

    def search(q):
        try:
            return q, get_json(
                DEX_URL + "/latest/dex/search?" +
                urllib.parse.urlencode({"q": q})
            )
        except Exception as exc:
            return q, exc

    # Broad meme keywords. DexScreener itself is responsible for discovery.
    queries = ["meme", "pump", "pepe", "bonk", "doge", "wif", "cat", "inu"]
    diag["search_queries_total"] = len(queries)

    with ThreadPoolExecutor(max_workers=8) as pool:
        for q, data in pool.map(search, queries):
            if isinstance(data, Exception):
                record_error("search:"+q, data)
                continue
            diag["search_queries_ok"] += 1
            raw = data.get("pairs") or [] if isinstance(data, dict) else []
            diag["search_pairs_raw"] += len(raw)
            for p in raw:
                add_pair(p)

    # If search returned no usable Solana pairs, use only DexScreener's
    # public discovery feeds as a bounded recovery path.
    if not pairs:
        token_ids = set()
        for path in (
            "/token-profiles/latest/v1",
            "/token-boosts/latest/v1",
            "/token-boosts/top/v1",
        ):
            try:
                data = get_json(DEX_URL + path)
                for item in data if isinstance(data, list) else []:
                    if item.get("chainId") == "solana" and item.get("tokenAddress"):
                        token_ids.add(str(item["tokenAddress"]))
            except Exception as exc:
                record_error("fallback:"+path, exc)

        tokens = list(token_ids)[:24]
        diag["fallback_tokens"] = len(tokens)

        def expand(token):
            try:
                return token, get_json(
                    DEX_URL + "/token-pairs/v1/solana/" +
                    urllib.parse.quote(token, safe="")
                )
            except Exception as exc:
                return token, exc

        with ThreadPoolExecutor(max_workers=8) as pool:
            for token, data in pool.map(expand, tokens):
                if isinstance(data, Exception):
                    record_error("pair:"+token, data)
                    continue
                for p in data if isinstance(data, list) else []:
                    add_pair(p)
                    diag["fallback_pairs"] += 1

    diag["solana_pairs"] = len(pairs)
    return list(pairs.values()), diag

