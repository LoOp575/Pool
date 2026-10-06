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

- Monte Carlo = unavailable
- historical volatility = unavailable
- ATR = unavailable
- entropy = unavailable
- historical probability = unavailable

The UI should report that state instead of manufacturing confidence.

## Core LP philosophy

The project optimizes for:

**Fee opportunity + range survival + manageable volatility - directional risk - tail risk**

The target is not the coin with the biggest pump.

A highly aggressive one-way move can generate volume while simultaneously making an LP range difficult to survive.

## Monte Carlo status

The Review dashboard now runs a **Merton 1976 jump-diffusion Monte Carlo** directly from the live DexScreener state.

The simulation layer uses the standard structure:

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
| Live range proxy | ACTIVE |
| ATR | HISTORICAL ONLY |
| Historical volatility | HISTORICAL ONLY |
| Entropy | HISTORICAL ONLY |
| Mean reversion | HISTORICAL ONLY |
| Monte Carlo | ACTIVE · LIVE SNAPSHOT SCENARIO; HISTORICAL CALIBRATION NOT AVAILABLE |
| True fee yield | NOT CLAIMED from snapshot |
| True IL | HISTORICAL / POSITION DATA REQUIRED |

## Engine version

Current scanner/review formula engine identifier:

`POOL-INTEL-2`

This version is intentionally a foundation. Individual formulas will be discussed and tuned one at a time rather than replacing them with generic defaults.
