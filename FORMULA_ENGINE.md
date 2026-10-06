# Pool Intelligence Formula Engine

## Core architecture

The Pool project is an LP intelligence system, not a generic coin picker.

```
DexScreener discovery
        ↓
Candidate scanner
        ↓
Live snapshot formulas
        ↓
Historical-data formulas
        ↓
Range / Monte Carlo / risk
        ↓
LP decision
```

## Stage 1: Scanner

The scanner answers only:

> Is this token interesting enough to analyze?

The primary freshness target is **1-3 days**. Tokens younger than 24h are still eligible when their live activity is strong. Tokens older than 3 days are progressively penalized.

Current scanner formulas include:

- Meme Identity Score
- Freshness Score
- Volume / Liquidity activity
- Initial momentum
- Buy / Sell pressure
- Runaway penalty
- LP Opportunity discovery score

The scanner must not pretend that snapshot data is historical data. A live-state Merton scenario is explicitly labeled as a snapshot model and is not presented as historical calibration.

## Stage 2: Live snapshot analysis

After a candidate is found, the same formulas are recalculated from the current DexScreener snapshot.

This stage can calculate:

- Freshness
- Meme identity
- Volume/liquidity
- Activity
- Buy/sell pressure
- Directional movement
- Liquidity quality
- Discovery LP score
- Live range proxy
- Live risk proxy

These are explicitly **LIVE** metrics.

## Stage 3: Historical analysis

Historical formulas require real candle observations.

Review now fetches **real OHLCV from GeckoTerminal** (public, no API key):

- Minute candles with an aggregate cascade `15 → 5 → 1` so young memecoin pools still reach the minimum.
- Minimum **30 candles**; below that the Review stays on the snapshot path.
- Fetch failure never breaks Review — it falls back to Jalur B and reports the reason in `ohlcv.error`.
- If the historical engine fails on the fetched candles, the snapshot path is used instead.

When active, Jalur A becomes the **primary** `range` / `monte_carlo` / `math` in the payload; the Merton snapshot result is kept alongside as `monte_carlo_merton` for comparison, and `confidence` becomes `MEDIUM · REAL OHLCV`.

The existing DLMM engine contains:

- ATR
- ATR%
- log-return volatility
- volatility ratio
- z-score
- trend strength
- volume pressure
- permutation entropy
- liquidity force
- mean-reversion force
- regime classification
- adaptive range
- bin planning
- Monte Carlo
- out-of-range probability
- IL proxy
- fee/IL ratio
- rebalance pressure

These formulas must receive actual candles.

### No synthetic history

Snapshot fields such as h1/h6/h24 must never be converted into fake candles just to make Monte Carlo produce a number.

If real OHLCV is unavailable:

- Historical range = unavailable (live range proxy is used)
- Monte Carlo = Merton snapshot scenario only
- historical volatility = unavailable
- ATR = unavailable
- entropy = unavailable
- historical probability = unavailable

The UI reports that state instead of manufacturing confidence. Real candles are only ever fetched from GeckoTerminal; they are never fabricated.

### Real fee metadata (Meteora pools)

For Meteora DLMM pools, Review additionally queries the public Meteora data API for **actual pool fee APR / APY, base fee and `bin_step`** (`fee_pool` in the payload). When present:

- `risk.fee_apr`, `risk.fee_yield`, `risk.il_proxy`, `risk.fee_il_ratio` are filled from the historical engine using the real APR.
- `data_quality.fee_is_actual = true`.

`bin_engine` is still **not** run from the Review: `bin_step` alone is not enough, and the active bin must never be guessed from price. It needs an on-chain `active_bin_id` (available in `dlmm_cli.py` when you supply it).

## Core LP philosophy

The project optimizes for:

**Fee opportunity + range survival + manageable volatility - directional risk - tail risk**

The target is not the coin with the biggest pump.

A highly aggressive one-way move can generate volume while simultaneously making an LP range difficult to survive.

## Monte Carlo status

The Review dashboard runs **two** Monte Carlo models and always labels which one is primary:

| Model | Data | When | Confidence |
|---|---|---|---|
| Bootstrap (Jalur A) | real OHLCV log-returns (GeckoTerminal) | ≥ 30 candles fetched | MEDIUM |
| Merton 1976 jump-diffusion (Jalur B) | live snapshot state (m5/h1/h6/h24, buy ratio, turnover, liquidity) | always (kept as `monte_carlo_merton` when Jalur A is active) | LOW |

The Merton simulation layer uses the standard structure:

`dS/S = (μ - λκ)dt + σdW + (Y - 1)dN`

with:

- `N` = Poisson jump-count process
- `Y = exp(N(μJ, σJ²))` = lognormal jump multiplier
- `κ = E[Y - 1]`
- diffusion volatility `σ`
- drift `μ`
- jump intensity `λ`

The equation and simulation are standard Merton. The parameter calibration is Pool-specific because DexScreener-only live snapshots do not provide a return time series.

Live calibration inputs include price movement across M5/H1/H6/H24, buy/sell pressure, 1h and 24h volume, turnover, transaction activity, liquidity, and volume acceleration/persistence.

The Review probability is therefore a **LOW-confidence scenario probability**, not a statistically estimated historical probability. The dashboard exposes Merton parameters, terminal percentiles, below/inside/above range probabilities, ever-out-of-range probability, and first-escape timing.

## Formula status

| Formula | Current state |
|---|---|
| Freshness | ACTIVE |
| Meme Identity | ACTIVE |
| Volume/Liquidity activity | ACTIVE |
| Buy/Sell pressure | ACTIVE |
| Runaway detection | ACTIVE |
| Discovery LP score | ACTIVE |
| Live range proxy | ACTIVE (fallback) |
| Adaptive historical range | ACTIVE when real OHLCV fetched |
| ATR | ACTIVE when real OHLCV fetched |
| Historical volatility | ACTIVE when real OHLCV fetched |
| Entropy | ACTIVE when real OHLCV fetched |
| Mean reversion | ACTIVE when real OHLCV fetched |
| Regime classification | ACTIVE when real OHLCV fetched |
| Bootstrap Monte Carlo | ACTIVE when real OHLCV fetched |
| Merton snapshot Monte Carlo | ACTIVE (fallback / pembanding) |
| Pool fee APR (actual) | ACTIVE for Meteora pools via Meteora data API |
| True IL | HISTORICAL / POSITION DATA REQUIRED |

## Dari rumus ke prediksi range / harga

Ada dua jalur yang berbeda. Keduanya menghasilkan **range** dan **probabilitas harga**, tetapi sumber datanya tidak boleh dicampur.

### Jalur A — Historical (dlmm_lp_engine.py, butuh OHLCV asli ≥ 30 candle)

Di Review, candle ini **diambil otomatis dari GeckoTerminal** (aggregate 15→5→1 menit, tanpa API key). Kalau dapat ≥30 candle, Jalur A menjadi primary dan `confidence` naik jadi `MEDIUM · REAL OHLCV`. Kalau gagal, Review tetap jalan di Jalur B — tidak ada candle sintetis.

| # | Rumus | Masuk ke | Menghasilkan |
|---|---|---|---|
| 1 | ATR / ATR% | `math_engine` → `range_engine` | basis lebar range (satuan harga → persen) |
| 2 | log-return volatility + volatility_ratio | `classify_regime`, `range_engine` | multiplier volatilitas; penanda regime EXTREME |
| 3 | z-score | `classify_regime`, `mean_reversion_force` | seberapa jauh harga dari mean → arah tarikan center |
| 4 | trend_strength | `classify_regime`, `range_engine` | multiplier trend (range melebar saat trending) |
| 5 | permutation_entropy | `range_engine` | multiplier kekacauan (chop → range lebih lebar) |
| 6 | mean_reversion_force | `range_engine` | **center** digeser ke fair value + multiplier penyempit |
| 7 | volume_pressure + liquidity_force | `range_engine` | **skew** — range dimiringkan ke arah tekanan order |
| 8 | liquidity_distribution (SPOT/CURVE/BID-ASK) | `liquidity_distribution` | bobot penempatan modal per bin |
| 9 | bootstrap Monte Carlo | `bootstrap_monte_carlo` | p_below, p_above, p_out_of_range, P5/P50/P95 |
| 10 | IL proxy + fee/IL ratio | `risk_engine` | risk score, catatan, keputusan fee vs IL |
| 11 | semua input di atas | `rebalance_engine` | rebalance YES/NO + urgency |

Rumus inti range:

```
center   = harga × (1 - mrw) + fair_value(72 bar) × mrw
width    = ATR% × Trend Multiplier × Volatility Multiplier × Entropy Multiplier × Mean-Reversion Multiplier
skew     = clamp(0.12 × (volume_pressure + liquidity_force), -0.20, 0.20)
lower    = center × (1 - width × (1 + skew))
upper    = center × (1 + width × (1 - skew))
```

`width` dibatasi 1%–90% dan `lower`/`upper` dijaga selalu > 0, karena ATR% memecoin bisa menembus 100% dan akan menghasilkan harga negatif yang membuat `bin_engine` gagal.

Bin dihitung dari range, bukan sebaliknya: `lower_bin = active_bin_id + floor(ln(lower/center) / ln(1 + bin_step))`.

**Prediksi harga** keluar dari jalur ini sebagai kuantil Monte Carlo (P5/P50/P95) dan `expected_terminal_price` — bukan sebagai satu angka pasti.

### Jalur B — Live snapshot (scanner.py, tanpa candle historis)

Ini jalur fallback (dan tetap jalan paralel sebagai pembanding `monte_carlo_merton`).

Karena DexScreener hanya member snapshot, jalur ini **tidak** memakai ATR/entropy/z-score (angkanya `null` di payload):

| Rumus | Masuk ke | Menghasilkan |
|---|---|---|
| momentum m5/h1/h6/h24 | `_live_range` | width = 1.8% + 22% × max\|move\|, center digeser oleh directional (maks ±8%) |
| buy_ratio, turnover, akselerasi volume, likuiditas | `snapshot_monte_carlo` | kalibrasi parameter Merton: σ, μ, λ, μ_J, σ_J |
| Merton jump-diffusion | `snapshot_monte_carlo` | P(below/inside/above), P(pernah keluar), P05/P50/P95, waktu jebol pertama |
| Meme Identity + Fresh Entry + LP score | `rank_pair` → `_live_analysis` | skor 0–100 untuk kandidat memecoin |
| survival + fee + range quality + directional safety | `_final_intelligence` | keputusan MASUK / TUNGGU / REBALANCE |

Rumus inti range live:

```
move        = max(|h1|, |h6|, |h24|) / 100
width       = clamp(0.018 + 0.22 × move, 0.025, 0.55)
directional = (0.45·h1 + 0.30·h6 + 0.25·h24) / 100       # dibatasi ±0.8
center      = price × (1 - 0.08 × directional)
```

### Kontrak probabilitas

- `p_below + p_inside + p_above = 1` (posisi harga di **akhir** horizon).
- `p_out_of_range` **sama dengan** `p_ever_out_of_range` = probabilitas **pernah** menyentuh batas selama horizon → dipakai oleh risk, rebalance, dan gate keputusan. Kedua nama kunci kini selalu sejalan di payload (Merton maupun bootstrap).
- Jangan jumlahkan `p_out_of_range` dengan below/inside/above.

Di payload Review:

| Field primary | Jalur A (OHLCV asli) | Jalur B (snapshot) |
|---|---|---|
| `range` | `range_engine` (ATR% × multiplier) | `_live_range` (proxy h1/h6/h24) |
| `monte_carlo` | bootstrap historical | Merton snapshot |
| `monte_carlo_merton` | Merton snapshot (pembanding) | `null` |
| `math.atr` dll. | terisi asli | `null` |
| `confidence` | `MEDIUM · REAL OHLCV` | `LOW · LIVE SNAPSHOT` |
| `history_status` | `REAL_OHLCV_GECKOTERMINAL` | `NO_REAL_HISTORY` |

## Engine version

Current scanner/review formula engine identifier:

`POOL-INTEL-4`

Version 4 activates the historical path in Review: real OHLCV from GeckoTerminal, real Meteora fee APR, and a unified probability contract (`p_out_of_range == p_ever_out_of_range`).

This version is intentionally a foundation. Individual formulas will be discussed and tuned one at a time rather than replacing them with generic defaults.
