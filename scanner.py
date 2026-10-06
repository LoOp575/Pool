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
    """Discover broad Solana token candidates from DexScreener feeds."""
    paths = (
        "/token-boosts/latest/v1",
        "/token-boosts/top/v1",
        "/token-profiles/latest/v1",
    )

    def one(path):
        try:
            data = get_json(DEX_URL + path)
            items = data if isinstance(data, list) else []
            return path, items, None
        except Exception as exc:
            return path, [], str(exc)[:180]

    out = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(one, paths))

    errors = []
    for path, items, err in results:
        if err:
            errors.append({"stage": path, "error": err})
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("chainId") != "solana":
                continue
            address = item.get("tokenAddress")
            if not address:
                continue
            address = str(address)
            out[address.lower()] = {
                "chainId": "solana",
                "tokenAddress": address,
            }

    # Keep the discovery request bounded for Vercel/serverless execution.
    return list(out.values())[:90], errors


def fetch_pairs_batch(tokens):
    """Resolve candidates through DexScreener's multi-token endpoint."""
    if not tokens:
        return [], 0, []

    batches = [tokens[i:i + 30] for i in range(0, len(tokens), 30)]

    def one(batch):
        addresses = ",".join(t["tokenAddress"] for t in batch)
        try:
            data = get_json(
                DEX_URL + "/latest/dex/tokens/" +
                urllib.parse.quote(addresses, safe=",")
            )
            pairs = data.get("pairs") or [] if isinstance(data, dict) else []
            return pairs, None
        except Exception as exc:
            return [], str(exc)[:180]

    all_pairs = []
    failed = 0
    errors = []
    # Parallel batches prevent a slow feed from making the Vercel function
    # look like a blank scanner.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for pairs, err in pool.map(one, batches):
            all_pairs.extend(pairs)
            if err:
                failed += 1
                if len(errors) < 8:
                    errors.append({"stage": "token-batch", "error": err})

    return all_pairs, failed, errors


def fetch_pairs_fallback(tokens):
    """Bounded per-token fallback using another DexScreener pair endpoint."""
    if not tokens:
        return [], []

    def one(token):
        try:
            data = get_json(
                DEX_URL + "/token-pairs/v1/solana/" +
                urllib.parse.quote(token["tokenAddress"], safe="")
            )
            return data if isinstance(data, list) else [], None
        except Exception as exc:
            return [], str(exc)[:180]

    out = []
    errors = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, tokens[:48]))
    for pairs, err in results:
        out.extend(pairs)
        if err and len(errors) < 8:
            errors.append({"stage": "pair-fallback", "error": err})
    return out, errors


def fetch_dexscreener_pairs():
    """Single-purpose DexScreener discovery. No coin-name keyword search."""
    diag = {
        "feed_tokens": 0,
        "expanded_tokens": 0,
        "expanded_pairs": 0,
        "fallback_pairs": 0,
        "solana_pairs": 0,
        "errors": [],
    }

    tokens, feed_errors = fetch_seed_tokens()
    diag["feed_tokens"] = len(tokens)
    diag["expanded_tokens"] = len(tokens)
    diag["errors"].extend(feed_errors[:8])

    pairs, failed_batches, batch_errors = fetch_pairs_batch(tokens)
    diag["expanded_pairs"] = len(pairs)
    diag["errors"].extend(batch_errors[:8])

    # Only spend extra requests when the main token endpoint failed or
    # returned nothing. This keeps the scanner fast on Vercel.
    fallback = []
    fallback_errors = []
    if not pairs or failed_batches:
        fallback, fallback_errors = fetch_pairs_fallback(tokens[:24])
        diag["fallback_pairs"] = len(fallback)
        diag["errors"].extend(fallback_errors[:8])
        pairs.extend(fallback)

    unique = {}
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        if pair.get("chainId") != "solana":
            continue
        address = pair.get("pairAddress")
        if address:
            unique[str(address)] = pair

    diag["solana_pairs"] = len(unique)
    diag["batch_failures"] = failed_batches
    return list(unique.values()), diag

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
    """DexScreener discovery pipeline. Never searches by coin name/keyword."""
    diag={
        "search_queries_total":0,
        "search_queries_ok":0,
        "search_pairs_raw":0,
        "feed_tokens":0,
        "expanded_tokens":0,
        "expanded_pairs":0,
        "fallback_pairs":0,
        "solana_pairs":0,
        "errors":[],
    }
    tokens=fetch_seed_tokens()
    diag["feed_tokens"]=len(tokens)
    diag["expanded_tokens"]=len(tokens)

    pairs,failed_batches=fetch_pairs_batch(tokens)
    diag["expanded_pairs"]=len(pairs)
    if failed_batches:
        diag["errors"].append({
            "stage":"token-batch",
            "error":f"{failed_batches} batch request(s) failed"
        })

    # Still DexScreener, but use its per-token pair endpoint as a safety net.
    if not pairs:
        fallback=fetch_pairs_fallback(tokens)
        diag["fallback_pairs"]=len(fallback)
        pairs.extend(fallback)

    out={}
    for p in pairs:
        if isinstance(p,dict) and p.get("chainId")=="solana" and p.get("pairAddress"):
            out[p["pairAddress"]]=p
    diag["solana_pairs"]=len(out)
    return list(out.values()),diag


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
        "p_survive_range": 0.0,
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
    result["p_survive_range"] = 1.0 - result["p_ever_out_of_range"]
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


def _final_intelligence(p, row, mc, range_plan, strategy="balanced"):
    """Single decision layer for the LP objective.

    Scanner scores discovery only. This layer combines the live market state,
    Merton range-survival scenario, LP quality proxies, and safety gates into
    one human-facing decision. No historical OHLCV or Meteora bin metadata is
    invented here.
    """
    lm = row["live_metrics"]
    lp = float(row["lp_score"])
    fee = float(row["lp_components"].get("fee_potential", 0))
    range_quality = float(row["lp_components"].get("range_quality_proxy", 0))
    directional_safety = float(row["lp_components"].get("directional_safety", 0))
    inside = float(mc.get("p_survive_range", 1.0 - mc.get("p_ever_out_of_range", 1))) if mc else 0.0
    escape = float(mc.get("p_ever_out_of_range", mc.get("p_out_of_range", 1))) if mc else 1.0
    below = float(mc.get("p_below", 0)) if mc else 0.0
    above = float(mc.get("p_above", 0)) if mc else 0.0

    reasons = []
    warnings = []
    hard_wait = []

    if lp < 50:
        hard_wait.append("kualitas LP belum cukup")
    if escape >= 0.35:
        hard_wait.append("range terlalu mudah ditembus")
    if inside < 0.55:
        hard_wait.append("peluang bertahan di range rendah")
    if directional_safety < 40:
        hard_wait.append("gerakan terlalu satu arah")

    if fee >= 70:
        reasons.append("aktivitas volume mendukung peluang fee")
    if inside >= 0.65:
        reasons.append("sebagian besar simulasi bertahan di range")
    elif inside >= 0.55:
        reasons.append("peluang bertahan di range masih layak")
    if directional_safety >= 70:
        reasons.append("tekanan arah relatif aman")
    if abs(above - below) < 0.12:
        reasons.append("gerakan dua arah lebih cocok untuk LP")
    if range_quality >= 70:
        reasons.append("kualitas range mendukung fee capture")

    h1 = float(lm.get("h1", 0))
    h6 = float(lm.get("h6", 0))
    h24 = float(lm.get("h24", 0))
    if abs(h1) >= 20 or abs(h6) >= 50:
        warnings.append("momentum sedang agresif")
    if float(lm.get("volume_acceleration", 0)) >= 3:
        warnings.append("volume sedang berakselerasi")
    if float(lm.get("buy_ratio", 0.5)) >= 0.68 or float(lm.get("buy_ratio", 0.5)) <= 0.32:
        warnings.append("arus buyer/seller terlalu berat sebelah")
    if escape >= 0.25:
        warnings.append("probabilitas range keluar mulai tinggi")

    # The decision score is deliberately gated by survival. A high-fee,
    # high-volatility pool must not win merely because it is active.
    survival = clamp(100 * inside, 0, 100)
    risk_safety = 100 * (1 - escape)
    final_score = clamp(
        0.28 * lp +
        0.20 * fee +
        0.18 * range_quality +
        0.18 * survival +
        0.16 * directional_safety,
        0, 100
    )

    if hard_wait:
        decision = "TUNGGU"
        action_reason = " / ".join(hard_wait[:2])
    elif escape >= 0.30 or float(row["risk"]["score"]) >= 60:
        decision = "REBALANCE"
        action_reason = "risiko range sudah meningkat"
    elif final_score >= 70 and escape < 0.25 and inside >= 0.55:
        decision = "MASUK"
        action_reason = "fee opportunity cukup kuat dengan range yang masih bertahan"
    else:
        decision = "TUNGGU"
        action_reason = "belum ada keunggulan yang cukup kuat"

    # Rebalance has priority only when a position is assumed to already exist.
    # For a fresh candidate, the same condition is presented as WAIT rather
    # than pretending an LP position is currently open.
    if decision == "REBALANCE" and not row.get("position_active", False):
        decision = "TUNGGU"
        action_reason = "range berisiko; jangan buka posisi baru sekarang"

    regime = (
        "EXTREME" if escape >= 0.35 or abs(h1) >= 25 else
        "TRENDING" if abs(h1) >= 8 or abs(h6) >= 20 else
        "CHOPPY" if abs(h1) <= 3 and abs(h6) <= 10 else
        "NORMAL"
    )

    return {
        "decision": decision,
        "score": round(final_score, 2),
        "regime": regime,
        "action_reason": action_reason,
        "reasons": reasons[:5],
        "warnings": warnings[:5],
        "gates": {
            "lp_score": round(lp, 2),
            "fee_opportunity": round(fee, 2),
            "range_quality": round(range_quality, 2),
            "directional_safety": round(directional_safety, 2),
            "inside_probability": round(inside, 4),
            "out_of_range_probability": round(escape, 4),
            "risk_safety": round(risk_safety, 2),
        },
        "range": range_plan,
        "probability": {
            "below": below,
            "inside": inside,
            "above": above,
            "ever_out_of_range": escape,
            "p05": mc.get("p05") if mc else None,
            "p50": mc.get("p50") if mc else None,
            "p95": mc.get("p95") if mc else None,
        },
        "data_quality": {
            "source": "DexScreener live snapshot",
            "historical_candles": 0,
            "confidence": "LOW",
            "real_ohlcv_available": False,
            "meteora_bin_metadata_available": False,
            "fee_is_actual": False,
        },
        "strategy": str(strategy).upper(),
    }


def _live_review(pair, horizon_bars=96, mc_paths=2000):
    """Run the complete live Pool Intelligence pipeline."""
    p = dex_pair(pair)
    analysis, row = _live_analysis(p)
    ch = p.get("priceChange") or {}
    vol = p.get("volume") or {}
    tx = p.get("txns") or {}
    liq = float((p.get("liquidity") or {}).get("usd") or 0)
    price = float(p.get("priceUsd") or 0)
    h1 = float(ch.get("h1") or 0)
    m5 = float(ch.get("m5") or 0)
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

    range_plan = _live_range(price, h1, h6, h24)
    mc = None
    mc_error = None
    try:
        mc = snapshot_monte_carlo(
            price, m5, h1, h6, h24, v1, v24, liq, buys, sells, total_tx,
            range_plan["lower"], range_plan["upper"],
            max(1, int(horizon_bars)), max(200, int(mc_paths))
        )
    except Exception as exc:
        mc_error = str(exc)

    # Keep the legacy risk fields for UI compatibility, but derive them from
    # the same Monte Carlo result used by Final Intelligence.
    mc_out = (
        float(mc.get("p_ever_out_of_range", mc.get("p_out_of_range", 1)))
        if mc else 1.0
    )
    liquidity_quality = score01(math.log1p(max(liq, 0)) / math.log1p(2_000_000))
    directional = clamp(
        0.42 * abs(h1) / 40 +
        0.28 * abs(h6) / 80 +
        0.20 * abs(h24) / 150 +
        0.10 * abs(buy_ratio - 0.5) / 0.5, 0, 1
    )
    risk_score = clamp(100 * (
        0.48 * mc_out +
        0.22 * (1 - liquidity_quality) +
        0.18 * score01(abs(h1) / 40) +
        0.12 * abs(buy_ratio - 0.5) / 0.5
    ), 0, 100)

    row["live_metrics"] = {
        "h1": h1, "h6": h6, "h24": h24, "m5": m5,
        "v1": v1, "v24": v24, "liquidity": liq,
        "buy_ratio": buy_ratio, "transactions_1h": total_tx,
        "vol_liq": vol_liq, "volume_24_liq": volume_24_liq,
        "volume_acceleration": volume_acceleration,
        "volume_persistence": volume_persistence,
        "price_usd": price, "buys_1h": buys, "sells_1h": sells,
        "buy_pressure": round((buy_ratio - 0.5) * 2, 4),
    }
    row["risk"] = {"score": risk_score, "out_of_range": mc_out}

    intelligence = _final_intelligence(
        p, row, mc, range_plan, strategy=row.get("lp_strategy", "BALANCED")
    )

    # Final action for a fresh candidate can only be MASUK or TUNGGU.
    # REBALANCE is exposed when the caller supplies an active position later.
    rebalance_reasons = []
    if mc_out >= 0.30:
        rebalance_reasons.append("P(out-of-range) tinggi")
    if risk_score >= 60:
        rebalance_reasons.append("risiko pasar tinggi")
    if abs(buy_ratio - 0.5) >= 0.25 and abs(h1) >= 8:
        rebalance_reasons.append("tekanan arah kuat")
    rebalance = bool(rebalance_reasons)

    return {
        "price": price,
        "formula_engine": FORMULA_ENGINE_VERSION,
        "regime": intelligence["regime"],
        "analysis": analysis,
        "intelligence": intelligence,
        "decision": intelligence["decision"],
        "confidence": "LOW · LIVE SNAPSHOT" if mc else "LIVE",
        "data_source": "DexScreener",
        "pair": p.get("pairAddress"),
        "token": (p.get("baseToken") or {}).get("address"),
        "dex": p.get("dexId"),
        "math": {
            "price": price, "atr": None, "atr_pct": None,
            "volatility": None, "volatility_ratio": None, "z_score": None,
            "trend_strength": None, "volume_pressure": round((buy_ratio - 0.5) * 2, 4),
            "entropy": None, "liquidity_force": None,
            "mean_reversion_force": None, "rvol": None,
        },
        "live_metrics": row["live_metrics"],
        "range": range_plan,
        "monte_carlo": mc,
        "monte_carlo_status": "ACTIVE" if mc else "UNAVAILABLE",
        "monte_carlo_error": mc_error,
        "risk": {
            "score": risk_score,
            "out_of_range": mc_out,
            "out_of_range_source": "MERTON_P_EVER_OUT_OF_RANGE" if mc else "UNAVAILABLE",
            "il_proxy": None, "fee_yield": None, "fee_il_ratio": None,
            "notes": [
                "Risk, range, and decision use the same live snapshot state.",
                "Historical OHLCV belum tersedia; ATR/Z-score/entropy asli tidak diisi.",
                "Monte Carlo memakai Merton jump-diffusion dengan kalibrasi state live.",
                "Fee opportunity adalah proxy turnover, bukan fee APR aktual.",
            ],
        },
        "rebalance": {
            "rebalance": rebalance,
            "urgency": "HIGH" if mc_out >= 0.50 or risk_score >= 75 else "MEDIUM" if rebalance else "NONE",
            "reasons": rebalance_reasons,
        },
        "history": 0,
        "history_status": "NO_REAL_HISTORY",
        "model_status": "FINAL_INTELLIGENCE_SNAPSHOT" if mc else "LIVE_ONLY",
        "bin": None,
        "bin_note": "Bin Meteora belum dihitung tanpa active bin + bin step metadata pool.",
    }


def scan(limit=40, only_meteora=False, strategy="balanced"):
    """Fast DexScreener filter. Scanner discovers; Review analyzes."""
    limit = min(max(int(limit), 1), 100)
    discovered, discovery_diag = fetch_dexscreener_pairs()

    funnel = {
        "discovered": len(discovered),
        "solana": 0,
        "token_data": 0,
        "age": 0,
        "volume": 0,
        "activity": 0,
        "final": 0,
        "row_errors": 0,
        "row_error_samples": [],
        "discovery": discovery_diag,
    }

    by_token = {}

    for p in discovered:
        try:
            if p.get("chainId") != "solana":
                continue
            funnel["solana"] += 1

            base = p.get("baseToken") or {}
            token = str(base.get("address") or "").strip()
            symbol = str(base.get("symbol") or "").strip()
            name = str(base.get("name") or "").strip()
            pair = str(p.get("pairAddress") or "").strip()
            if not token or not symbol or not pair:
                continue
            funnel["token_data"] += 1

            created = p.get("pairCreatedAt")
            if not created:
                continue
            age_h = max(
                0.0,
                (time.time() * 1000 - float(created)) / 3600000
            )
            if not 0.25 <= age_h <= 168:
                continue
            funnel["age"] += 1

            volume = p.get("volume") or {}
            txns = p.get("txns") or {}
            changes = p.get("priceChange") or {}

            v1 = float(volume.get("h1") or 0)
            v6 = float(volume.get("h6") or 0)
            v24 = float(volume.get("h24") or 0)

            t1 = txns.get("h1") or {}
            t6 = txns.get("h6") or {}
            t24 = txns.get("h24") or {}

            buys_1h = int(t1.get("buys") or 0)
            sells_1h = int(t1.get("sells") or 0)
            buys_6h = int(t6.get("buys") or 0)
            sells_6h = int(t6.get("sells") or 0)
            buys_24h = int(t24.get("buys") or 0)
            sells_24h = int(t24.get("sells") or 0)

            total_1h = buys_1h + sells_1h
            total_6h = buys_6h + sells_6h
            total_24h = buys_24h + sells_24h

            # The scanner only needs live flow. No score, formula, or
            # memecoin identity is calculated here.
            if max(v1, v6, v24) <= 0:
                continue
            funnel["volume"] += 1

            if max(total_1h, total_6h, total_24h) <= 0:
                continue
            funnel["activity"] += 1

            liquidity = float((p.get("liquidity") or {}).get("usd") or 0)
            price = float(p.get("priceUsd") or 0)
            h1 = float(changes.get("h1") or 0)
            h6 = float(changes.get("h6") or 0)
            h24 = float(changes.get("h24") or 0)
            buy_ratio = safe_div(buys_1h, total_1h, 0.5)

            row = {
                "token": token,
                "base": symbol,
                "name": name,
                "quote": (p.get("quoteToken") or {}).get("symbol"),
                "pair": pair,
                "dex": p.get("dexId"),
                "url": p.get("url"),
                "price": price,
                "age_h": round(age_h, 2),
                "age_days": round(age_h / 24, 2),
                "volume_1h": v1,
                "volume_6h": v6,
                "volume_24h": v24,
                "buys_1h": buys_1h,
                "sells_1h": sells_1h,
                "buys_6h": buys_6h,
                "sells_6h": sells_6h,
                "buys_24h": buys_24h,
                "sells_24h": sells_24h,
                "transactions_1h": total_1h,
                "transactions_6h": total_6h,
                "transactions_24h": total_24h,
                "buy_ratio_1h": round(buy_ratio, 4),
                "liquidity": liquidity,
                "h1": h1,
                "h6": h6,
                "h24": h24,
                "pump_signal": max(h1, h6, h24) > 0,
                "pump_window": (
                    "1H" if h1 > 0 else
                    "6H" if h6 > 0 else
                    "24H" if h24 > 0 else None
                ),
                "scan_tier": "DEXSCREENER_FLOW",
                "scanner_role": "FILTER_ONLY",
                "source": "DexScreener",
                "history_available": False,
                "data_confidence": "LIVE",
                "meteora": str(p.get("dexId") or "").lower() in {
                    "meteora", "meteora-dlmm", "meteora-dlmm2"
                },
                "gmgn_url": gmgn_url(token),
            }

            if only_meteora and not row["meteora"]:
                continue

            old = by_token.get(token)
            new_key = (total_1h, v1, total_6h, v6, v24)
            old_key = (
                old["transactions_1h"],
                old["volume_1h"],
                old["transactions_6h"],
                old["volume_6h"],
                old["volume_24h"],
            ) if old else None
            if old is None or new_key > old_key:
                by_token[token] = row

        except Exception as exc:
            funnel["row_errors"] += 1
            if len(funnel["row_error_samples"]) < 8:
                funnel["row_error_samples"].append(str(exc)[:180])

    rows = sorted(
        by_token.values(),
        key=lambda x: (
            x["transactions_1h"],
            x["volume_1h"],
            x["transactions_6h"],
            x["volume_6h"],
            x["volume_24h"],
        ),
        reverse=True,
    )[:limit]

    funnel["final"] = len(rows)

    return {
        "generated_at": int(time.time()),
        "count": len(rows),
        "rows": rows,
        "source": "DexScreener" if rows else "none",
        "filters": {
            "discovery_source": "DexScreener API",
            "age_hours_min": 0.25,
            "age_hours_max": 168,
            "requires_live_volume": True,
            "requires_live_activity": True,
            "scanner_role": "FILTER_ONLY",
        },
        "funnel": funnel,
        "discovery_status": (
            "OK" if discovery_diag.get("solana_pairs", 0)
            else "NO_SOLANA_PAIRS"
        ),
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
