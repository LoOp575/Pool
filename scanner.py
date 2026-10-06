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

FORMULA_ENGINE_VERSION = "POOL-INTEL-2"


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
    """Broad Solana discovery with token-level diversity."""
    pairs={}
    queries=["pump","meme","pepe","dog","cat","ai","inu","bonk","wif","frog","shib","moon","solana"]

    def one(q):
        try:
            data=get_json(DEX_URL+"/latest/dex/search?"+urllib.parse.urlencode({"q":q}))
            return data.get("pairs") or []
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=4) as pool:
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

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(pools,t) for t in dict.fromkeys(seeds[:60])]
        for f in as_completed(futures):
            for p in f.result():
                if p.get("chainId")=="solana" and p.get("pairAddress"):
                    pairs[p["pairAddress"]]=p

    return list(pairs.values())



def _poisson_sample(rng, lam):
    """Exact Poisson sampler for the Merton jump-count process."""
    lam = max(0.0, float(lam))
    if lam == 0.0:
        return 0
    if lam < 30.0:
        limit = math.exp(-lam)
        k = 0
        p = 1.0
        while p > limit:
            k += 1
            p *= rng.random()
        return k - 1
    # Stable normal approximation for very large Poisson means.
    return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))


def snapshot_monte_carlo(price, m5, h1, h6, h24, volume_1h, volume_24h, liquidity,
                          buys, sells, transactions, lower, upper, horizon_bars=96, paths=2000):
    """
    Merton (1976) jump-diffusion Monte Carlo driven by the live DexScreener state.

    The stochastic process is the original Merton structure:
        dS/S = (mu - lambda*k)dt + sigma*dW + (Y - 1)dN
        Y = exp(N(mu_J, sigma_J^2))
        k = E[Y - 1] = exp(mu_J + sigma_J^2/2) - 1
        N ~ Poisson(lambda*dt)

    We estimate the model parameters from the current snapshot because this
    repository intentionally has no synthetic/historical candles. The
    parameter-estimation layer is Pool-specific; the Merton simulation itself
    follows the standard jump-diffusion equation.
    """
    if price <= 0:
        raise ValueError("Harga live tidak valid.")

    total = max(1, int(buys) + int(sells))
    buy_ratio = clamp(safe_div(buys, total, 0.5), 0, 1)
    buy_pressure = (buy_ratio - 0.5) * 2.0

    volume_liq = safe_div(float(volume_1h or 0), max(float(liquidity or 0), 1), 0)
    volume_24_liq = safe_div(float(volume_24h or 0), max(float(liquidity or 0), 1), 0)
    volume_acceleration = safe_div(float(volume_1h or 0), max(float(volume_24h or 0) / 24.0, 1.0), 0)
    volume_force = clamp(
        0.65 * score01(math.log1p(max(volume_liq, 0)) / math.log1p(20)) +
        0.35 * score01(math.log1p(max(volume_24_liq, 0)) / math.log1p(60)),
        0, 1
    )
    acceleration_force = score01(volume_acceleration / 3.0)
    activity_force = score01(math.log1p(max(int(transactions), 0)) / math.log1p(1000))

    # Thin liquidity increases jump intensity and jump dispersion.
    liquidity_force = 1 - score01(
        math.log1p(max(float(liquidity or 0), 0)) / math.log1p(2_000_000)
    )

    momentum = clamp(
        0.40 * math.tanh(float(m5 or 0) / 8.0) +
        0.30 * math.tanh(float(h1 or 0) / 25.0) +
        0.20 * math.tanh(float(h6 or 0) / 60.0) +
        0.10 * math.tanh(float(h24 or 0) / 180.0),
        -1, 1
    )
    directional_pressure = clamp(
        0.58 * buy_pressure + 0.42 * momentum, -1, 1
    )

    # Pool-specific calibration layer: current state -> Merton parameters.
    # These are annualized parameters required by the continuous-time model.
    dt = 15.0 / (365.0 * 24.0 * 60.0)
    steps_per_year = 1.0 / dt

    sigma_step = clamp(
        0.003 +
        0.010 * volume_force +
        0.006 * activity_force +
        0.004 * acceleration_force +
        0.012 * liquidity_force +
        0.006 * abs(momentum),
        0.003, 0.045
    )
    sigma = sigma_step * math.sqrt(steps_per_year)

    # Physical-measure drift estimate. Merton's compensator below removes the
    # expected jump contribution, so mu remains the total expected drift.
    mu_step = clamp(
        0.0045 * directional_pressure +
        0.0015 * buy_pressure * volume_force,
        -0.008, 0.008
    )
    mu = mu_step * steps_per_year

    # Merton jump parameters: Poisson intensity and lognormal jump size.
    # The state layer only estimates them; the simulation uses the exact
    # Poisson/lognormal structure from the model.
    jump_prob_step = clamp(
        0.008 * volume_force +
        0.006 * acceleration_force +
        0.010 * liquidity_force +
        0.008 * abs(directional_pressure),
        0, 0.035
    )
    lambda_year = -math.log(max(1e-12, 1.0 - jump_prob_step)) * steps_per_year
    mu_j = clamp(0.012 * directional_pressure, -0.06, 0.06)
    sigma_j = clamp(0.018 + 0.020 * liquidity_force, 0.018, 0.05)
    kappa = math.exp(mu_j + 0.5 * sigma_j * sigma_j) - 1.0

    rng = random.Random()
    terminals = []
    below = above = 0
    first_escape_bars = []

    for _ in range(max(1, int(paths))):
        px = price
        escaped = None
        for bar in range(max(1, int(horizon_bars))):
            # Standard Merton discretization:
            # S(t+dt) = S(t) * exp((mu-lambda*kappa-0.5*sigma^2)dt
            #                         + sigma*sqrt(dt)*Z) * product(Y_i)
            n_jumps = _poisson_sample(rng, lambda_year * dt)
            z = rng.gauss(0.0, 1.0)
            log_return = (
                (mu - lambda_year * kappa - 0.5 * sigma * sigma) * dt
                + sigma * math.sqrt(dt) * z
            )
            if n_jumps:
                for _jump in range(n_jumps):
                    log_return += rng.gauss(mu_j, sigma_j)
            px *= math.exp(log_return)

            if escaped is None and (px < lower or px > upper):
                escaped = bar + 1

        terminals.append(px)
        if px < lower:
            below += 1
        elif px > upper:
            above += 1
        if escaped is not None:
            first_escape_bars.append(escaped)

    terminals.sort()
    n = len(terminals)
    q = lambda p: terminals[min(n - 1, max(0, int(round((n - 1) * p))))]

    p_below = below / n
    p_above = above / n
    result = {
        "paths": n,
        "horizon_bars": int(horizon_bars),
        "horizon_minutes": int(horizon_bars) * 15,
        "p_below": p_below,
        "p_inside": max(0, 1 - p_below - p_above),
        "p_above": p_above,
        "p_out_of_range": p_below + p_above,
        "expected_terminal_price": sum(terminals) / n,
        "p05": q(0.05),
        "p50": q(0.50),
        "p95": q(0.95),
        "current_price": price,
        "historical_candles": 0,
        "historical_source": "DexScreener live snapshot",
        "model": "Merton 1976 jump-diffusion Monte Carlo",
        "model_equation": "dS/S=(mu-lambda*kappa)dt+sigma*dW+(Y-1)dN",
        "confidence": "LOW",
        "inputs": {
            "m5_pct": float(m5 or 0),
            "h1_pct": float(h1 or 0),
            "h6_pct": float(h6 or 0),
            "h24_pct": float(h24 or 0),
            "buy_ratio": buy_ratio,
            "buy_pressure": buy_pressure,
            "volume_force": volume_force,
            "volume_24_liq": volume_24_liq,
            "volume_acceleration": volume_acceleration,
            "acceleration_force": acceleration_force,
            "activity_force": activity_force,
            "liquidity_force": liquidity_force,
            "momentum": momentum,
            "directional_pressure": directional_pressure,
            "dt_years": dt,
            "drift_annualized": mu,
            "volatility_annualized": sigma,
            "jump_intensity_annualized": lambda_year,
            "jump_mean_log": mu_j,
            "jump_volatility_log": sigma_j,
            "jump_expected_multiplier_minus_one": kappa,
            "jump_probability_per_step": jump_prob_step,
            "drift_compensator_annualized": lambda_year * kappa,
            "calibration": "Pool snapshot state -> Merton parameters"
        },
        "range": {"lower": lower, "upper": upper},
    }
    if first_escape_bars:
        result["mean_first_escape_bars"] = sum(first_escape_bars) / len(first_escape_bars)
        result["p_ever_out_of_range"] = len(first_escape_bars) / n
    else:
        result["mean_first_escape_bars"] = None
        result["p_ever_out_of_range"] = 0.0
    return result

def dex_pair(pair_address):
    """Fetch one live Solana pair directly from DexScreener."""
    url = f"{DEX_URL}/latest/dex/pairs/solana/{urllib.parse.quote(str(pair_address), safe='')}"
    data = get_json(url)
    pairs = data.get("pairs") or []
    if not pairs:
        raise RuntimeError("Pair tidak ditemukan di DexScreener.")
    # Prefer the exact pair address when the endpoint returns multiple markets.
    for p in pairs:
        if p.get("pairAddress") == pair_address:
            return p
    return pairs[0]


def _live_range(price, h1, h6, h24):
    """Conservative live range proxy using only current DexScreener movement."""
    move = max(abs(h1) / 100, abs(h6) / 100, abs(h24) / 100, 0.01)
    width = clamp(0.018 + 0.22 * move, 0.025, 0.55)
    directional = clamp((0.45 * h1 + 0.30 * h6 + 0.25 * h24) / 100, -0.8, 0.8)
    center = price * (1 - 0.08 * directional)
    lower = center * (1 - width * (1 + max(directional, 0) * 0.20))
    upper = center * (1 + width * (1 - max(-directional, 0) * 0.20))
    return {
        "lower": lower,
        "center": center,
        "upper": upper,
        "width_pct": width,
        "multipliers": {
            "source": "DexScreener live proxy",
            "movement": move,
            "directional_bias": directional,
        },
    }


def _live_analysis(p, strategy="balanced"):
    """Run the same custom formulas on the live candidate snapshot."""
    discovery_score, row = rank_pair(p, strategy)
    return {
        "formula_engine": FORMULA_ENGINE_VERSION,
        "mode": "LIVE_SNAPSHOT",
        "score": round(discovery_score, 2),
        "fresh_score": round(row["fresh_score"], 2),
        "lp_score": round(row["lp_score"], 2),
        "meme_score": round(row["memecoin_score"], 2),
        "lp_components": row["lp_components"],
        "fresh_reasons": row["fresh_reasons"],
        "meme_reasons": row["meme_reasons"],
        "history_required": [
            "ATR", "historical volatility", "volatility_ratio", "z_score",
            "trend_strength", "entropy", "mean_reversion_force"
        ],
        "prediction_status": "LIVE_SNAPSHOT_MERTON_ACTIVE",
        "prediction_note": "Monte Carlo aktif sebagai snapshot-state scenario model; historical calibration belum tersedia.",
    }, row


def _live_review(pair, horizon_bars=96, mc_paths=2000):
    p=dex_pair(pair)
    analysis,row=_live_analysis(p)
    mc=None
    mc_error=None
    ch = p.get("priceChange") or {}
    vol = p.get("volume") or {}
    tx = p.get("txns") or {}
    liq = float((p.get("liquidity") or {}).get("usd") or 0)
    price = float(p.get("priceUsd") or 0)
    h1 = float(ch.get("h1") or 0)
    h6 = float(ch.get("h6") or 0)
    h24 = float(ch.get("h24") or 0)
    v1 = float(vol.get("h1") or 0)
    v24 = float(vol.get("h24") or 0)
    t1 = tx.get("h1") or {}
    buys = int(t1.get("buys") or 0)
    sells = int(t1.get("sells") or 0)
    total_tx = buys + sells
    buy_ratio = safe_div(buys, total_tx, 0.5)
    vol_liq = safe_div(v1, liq, 0)
    volume_24_liq = safe_div(v24, liq, 0)
    volume_acceleration = safe_div(v1, max(v24 / 24.0, 1.0), 0)
    volume_persistence = clamp(
        0.55 * score01(volume_24_liq / 0.15) +
        0.45 * score01(vol_liq / 0.03), 0, 1
    )

    directional = clamp(
        0.42 * abs(h1) / 40 +
        0.28 * abs(h6) / 80 +
        0.20 * abs(h24) / 150 +
        0.10 * abs(buy_ratio - 0.5) / 0.5, 0, 1
    )
    activity = score01(math.log1p(total_tx) / math.log1p(1000))
    liquidity_quality = score01(math.log1p(max(liq, 0)) / math.log1p(2_000_000))
    fee_potential = score01(
        0.70 * math.log1p(max(vol_liq, 0)) / math.log1p(20) +
        0.30 * math.log1p(max(volume_24_liq, 0)) / math.log1p(60)
    )
    risk_score = clamp(100 * (
        0.48 * directional +
        0.22 * (1 - liquidity_quality) +
        0.18 * score01(abs(h1) / 40) +
        0.12 * abs(buy_ratio - 0.5) / 0.5
    ), 0, 100)

    if abs(h1) >= 20 or abs(h6) >= 50:
        regime = "HIGH_VOLATILITY"
    elif abs(h1) >= 8 or abs(h6) >= 20:
        regime = "MOMENTUM"
    elif abs(h1) <= 3 and abs(h6) <= 10:
        regime = "QUIET"
    else:
        regime = "NORMAL"

    range_plan = _live_range(price, h1, h6, h24)
    try:
        mc=snapshot_monte_carlo(price,float(ch.get("m5") or 0),h1,h6,h24,
                                v1,v24,liq,buys,sells,total_tx,
                                range_plan["lower"],range_plan["upper"],
                                max(1, int(horizon_bars)), max(200, int(mc_paths)))
    except Exception as exc:
        mc_error=str(exc)
    mc_out = float(mc.get("p_out_of_range", 0)) if mc else None
    out_proxy = mc_out if mc_out is not None else clamp(
        0.45 * abs(h1) / 100 +
        0.35 * abs(h6) / 100 +
        0.20 * abs(h24) / 100, 0, 0.95
    )
    rebalance = out_proxy >= 0.30 or risk_score >= 60
    urgency = "HIGH" if out_proxy >= 0.50 or risk_score >= 75 else "MEDIUM" if rebalance else "NONE"

    return {
        "price": price,
        "formula_engine": FORMULA_ENGINE_VERSION,
        "regime": regime,
        "analysis": analysis,
        "confidence": "LOW · LIVE SNAPSHOT" if mc else "LIVE",
        "data_source": "DexScreener",
        "pair": p.get("pairAddress"),
        "token": (p.get("baseToken") or {}).get("address"),
        "dex": p.get("dexId"),
        "math": {
            "price": price,
            "atr": None,
            "atr_pct": None,
            "volatility": None,
            "volatility_ratio": None,
            "z_score": None,
            "trend_strength": None,
            "volume_pressure": round((buy_ratio - 0.5) * 2, 4),
            "entropy": None,
            "liquidity_force": None,
            "mean_reversion_force": None,
            "rvol": None,
        },
        "live_metrics": {
            "h1": h1, "h6": h6, "h24": h24,
            "v1": v1, "v24": v24,
            "liquidity": liq,
            "buy_ratio": buy_ratio,
            "transactions_1h": total_tx,
            "vol_liq": vol_liq,
            "volume_24_liq": volume_24_liq,
            "volume_acceleration": volume_acceleration,
            "volume_persistence": volume_persistence,
            "price_usd": price,
            "buys_1h": buys,
            "sells_1h": sells,
            "buy_pressure": round((buy_ratio - 0.5) * 2, 4),
        },
        "range": range_plan,
        "monte_carlo": mc,
        "monte_carlo_status": "ACTIVE" if mc else "UNAVAILABLE",
        "monte_carlo_error": mc_error,
        "risk": {
            "score": risk_score,
            "out_of_range": out_proxy,
            "out_of_range_source": "MERTON_MONTE_CARLO" if mc else "LIVE_MOVEMENT_PROXY",
            "il_proxy": None,
            "fee_yield": None,
            "fee_il_ratio": None,
            "notes": [
                "Risk memakai data live DexScreener.",
                "Historical volatility belum tersedia dari DexScreener.",
                "Monte Carlo memakai Merton jump-diffusion pada state live, bukan OHLCV historis.",
                "Parameter Merton dikalibrasi heuristik dari state live; confidence LOW.",
            ],
        },
        "rebalance": {
            "rebalance": rebalance,
            "urgency": urgency,
            "reasons": (
                ["range live proxy terlalu tertekan"]
                if out_proxy >= 0.30 else
                ["directional pressure tinggi"]
                if risk_score >= 60 else
                []
            ),
        },
        "history": int(mc.get("historical_candles",0)) if mc else 0,
        "history_status": "NO_REAL_HISTORY",
        "model_status": "SNAPSHOT_SCENARIO" if mc else "LIVE_ONLY",
        "bin": None,
        "bin_note": "Active bin + bin step harus diambil dari metadata pool Meteora sebelum bin ID dihitung.",
    }


def scan(limit=40, only_meteora=False, strategy="balanced"):
    """DexScreener fresh-meme scanner: young + active + ignition."""
    candidates=[]
    funnel={"discovered":0,"meme_score":0,"liquidity":0,"age_1_7d":0,"fallback_mode":False,
            "active_volume":0,"history_checked":0,"history_available":0,
            "pump_30pct":0,"volume_persistence":0,"not_faded":0,
            "fallback_24h":0,"final_before_dedupe":0,"final":0,
            "fresh_qualified":0,"ignition":0,"not_runaway":0}
    discovered=fetch_search_pairs()
    funnel["discovered"]=len(discovered)

    for p in discovered:
        try:
            meme_score,_=memecoin_score(p)
            if meme_score < 20: continue
            funnel["meme_score"] += 1

            _,row=rank_pair(p,strategy)
            if row["liquidity"] < 1000: continue
            funnel["liquidity"] += 1

            age_h=row["age_h"]; h1=row["h1"]; h6=row["h6"]; h24=row["h24"]
            v1=row["v1"]; v24=row["v24"]; liq=row["liquidity"]
            if not 0.5 <= age_h <= 168: continue
            funnel["age_1_7d"] += 1

            active=(v1 >= max(750,liq*0.015) or v24 >= max(5000,liq*0.08))
            if not active: continue
            funnel["active_volume"] += 1

            if h24 >= 30: funnel["pump_30pct"] += 1

            # Recent-volume acceleration proxy from DexScreener's 1h vs 24h volume.
            volume_accel=safe_div(v1,max(v24/24,1),0)
            row["volume_acceleration"]=round(volume_accel,3)

            ignition=(
                (h1 >= 2 and v1 >= max(1000,liq*0.02)) or
                (h6 >= 8 and h24 >= 15 and v1 >= max(500,liq*0.01)) or
                (h24 >= 30 and v1 >= max(1500,liq*0.03))
            )
            if ignition: funnel["ignition"] += 1

            not_runaway=not (h1 >= 70 and h24 >= 250)
            if not_runaway: funnel["not_runaway"] += 1
            if not not_runaway: continue

            persistence=clamp(
                0.55*score01(v24/max(liq*0.15,1))+
                0.45*score01(v1/max(liq*0.03,1)),0,1)
            row["volume_persistence"]=round(persistence,3)
            if persistence >= 0.45:
                funnel["volume_persistence"] += 1
                funnel["not_faded"] += 1

            if row["fresh_score"] < 32: continue
            funnel["fresh_qualified"] += 1

            if only_meteora and not row["meteora"]: continue

            row["source"]="DexScreener"
            row["history_available"]=False
            row["data_confidence"]="LIVE"
            row["pump_pct"]=max(0,h24/100)
            row["drawdown_from_peak"]=0
            row["pump_age_h"]=0
            row["pump_stage"]="IGNITION" if ignition and h24 < 30 else "EARLY_PUMP" if h24 < 100 else "PUMPED_24H"
            row["post_pump_score"]=row["fresh_score"]
            row["post_pump_edge"]=round(row["fresh_score"],2)
            row["score"]=clamp(0.78*row["fresh_score"]+0.14*row["lp_score"]+0.08*meme_score,0,100)
            candidates.append(row)
        except Exception:
            continue

    # Controlled fallback keeps the scanner useful during thin market periods.
    # It is only activated when the strict LP-discovery funnel returns zero rows.
    if not candidates and discovered:
        funnel["fallback_mode"] = True
        for p in discovered:
            try:
                meme_score,_=memecoin_score(p)
                if meme_score < 15:
                    continue
                _,row=rank_pair(p,strategy)
                if row["liquidity"] < 500:
                    continue
                age_h=row["age_h"]; h1=row["h1"]; h24=row["h24"]
                v1=row["v1"]; v24=row["v24"]; liq=row["liquidity"]
                if not 6 <= age_h <= 168:
                    continue
                t1=p.get("txns") or {}
                h1tx=t1.get("h1") or {}
                txns=int(h1tx.get("buys") or 0)+int(h1tx.get("sells") or 0)
                active=(v1 >= max(300,liq*0.0075) or
                        v24 >= max(2500,liq*0.04) or txns >= 50)
                if not active:
                    continue
                if h1 >= 100 and h24 >= 500:
                    continue
                if row["fresh_score"] < 22:
                    continue
                if only_meteora and not row["meteora"]:
                    continue

                row["scan_tier"]="FALLBACK"
                row["source"]="DexScreener"
                row["history_available"]=False
                row["data_confidence"]="LIVE"
                row["volume_acceleration"]=round(safe_div(v1,max(v24/24,1),0),3)
                row["volume_persistence"]=round(clamp(
                    0.55*score01(v24/max(liq*0.15,1))+
                    0.45*score01(v1/max(liq*0.03,1)),0,1),3)
                row["pump_pct"]=max(0,h24/100)
                row["pump_measurement"]="24H_CHANGE_PROXY"
                row["drawdown_from_peak"]=None
                row["pump_age_h"]=None
                row["pump_stage"]="FALLBACK_DISCOVERY"
                row["post_pump_score"]=row["fresh_score"]
                row["post_pump_edge"]=round(row["fresh_score"],2)
                row["score"]=clamp(
                    0.72*row["fresh_score"]+0.18*row["lp_score"]+0.10*meme_score,
                    0,100)
                candidates.append(row)
            except Exception:
                continue
    else:
        funnel["fallback_mode"] = False

    funnel["fallback_24h"]=len(candidates)
    funnel["final_before_dedupe"]=len(candidates)
    by_token={}
    for x in candidates:
        key=x.get("token") or ("pair:"+str(x.get("pair") or ""))
        old=by_token.get(key)
        if old is None or (x["score"],x["fresh_score"],x["v1"])>(old["score"],old["fresh_score"],old["v1"]):
            by_token[key]=x
    funnel["final"]=len(by_token)
    rows=sorted(by_token.values(),key=lambda x:(x["score"],x["fresh_score"],x["lp_score"],x["v1"],x["liquidity"]),reverse=True)[:limit]

    return {
        "generated_at":int(time.time()),"count":len(rows),"rows":rows,
        "source":"DexScreener" if rows else "none",
        "filters":{
            "min_liquidity_usd_dex":1000,"memecoin_score_min":20,
            "unique_tokens":True,"memecoin_only":True,
            "age_hours_min":0.5,"age_hours_max":168,"fresh_score_min":32,
            "ignition_preferred":True,"runaway_excluded":True,
            "historical_provider":False,"gmgn_enabled":False,
            "gmgn_mode":"web-reference-only","only_meteora":only_meteora,
            "strategy":strategy},
        "funnel":funnel,
    }

def review_pair(pair_address, fee_apr=0, horizon_bars=96, mc_paths=2000):
    """Review a pool from live DexScreener data without historical OHLCV."""
    return _live_review(pair_address, horizon_bars=horizon_bars, mc_paths=mc_paths)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--meteora", action="store_true")
    ap.add_argument("--strategy", choices=["conservative", "balanced", "aggressive"], default="balanced")
    a = ap.parse_args()
    print(json.dumps(scan(a.limit, a.meteora, a.strategy), indent=2))
