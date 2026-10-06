# Meteora DLMM LP Engine

Engine posisi LP: OHLCV -> math -> regime -> adaptive range -> bin -> liquidity -> Monte Carlo -> risk -> rebalance.

Formula inti:
Range Width = ATR% × Trend Multiplier × Volatility Multiplier × Entropy Multiplier × Mean-Reversion Multiplier.

Bin mapping memakai active_bin_id + bin_step_bps. Jangan menebak active bin dari harga.

IL adalah benchmark proxy constant-product, bukan perhitungan IL DLMM on-chain. Token A/B weights adalah bobot inventory-side ter-normalisasi, bukan liquidity amount on-chain.

Sebelum modal nyata: backtest bobot/threshold, validasi fee model, dan cocokkan bin math dengan pool Meteora yang ditarget.
