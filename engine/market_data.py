"""Market data normalization layer.

Converts Tool-Trade/DexScreener scanner rows into a stable object used by
future Pool intelligence engines.
"""

import time


class MarketData:
    def __init__(self, pair):
        self.raw = pair or {}
        self.token = self.raw.get("token")
        self.chain = self.raw.get("chain")
        self.dex = self.raw.get("dex")
        self.pair = self.raw.get("pair")
        self.price = self._num("price")
        self.liquidity = self._num("liquidity")
        self.volume_1h = self._num("volume_1h")
        self.volume_6h = self._num("volume_6h")
        self.volume_24h = self._num("volume_24h")
        self.buys_1h = int(self.raw.get("buys_1h") or 0)
        self.sells_1h = int(self.raw.get("sells_1h") or 0)
        self.age_h = self.raw.get("age_h")

    def _num(self, key):
        try:
            return float(self.raw.get(key) or 0)
        except Exception:
            return 0.0

    def buy_pressure(self):
        total = self.buys_1h + self.sells_1h
        return self.buys_1h / total if total else 0.5

    def volume_liquidity_ratio(self):
        return self.volume_24h / self.liquidity if self.liquidity else 0.0

    def liquidity_force(self):
        activity = max(self.buys_1h + self.sells_1h, 1)
        return self.liquidity * self.volume_liquidity_ratio() * activity

    def snapshot(self):
        return {
            "token": self.token,
            "chain": self.chain,
            "dex": self.dex,
            "pair": self.pair,
            "price": self.price,
            "liquidity": self.liquidity,
            "volume": {
                "1h": self.volume_1h,
                "6h": self.volume_6h,
                "24h": self.volume_24h,
            },
            "buy_pressure": round(self.buy_pressure(), 4),
            "volume_liquidity_ratio": round(self.volume_liquidity_ratio(), 4),
            "liquidity_force": self.liquidity_force(),
            "age_h": self.age_h,
            "timestamp": int(time.time()),
        }
