"""
Time Range Engine

Estimates how long a price range can survive after Monte Carlo range detection.
This is a probability feature, not a guaranteed prediction.
"""

from dataclasses import dataclass


@dataclass
class RangeDuration:
    min_minutes: int
    max_minutes: int
    confidence: float
    status: str


def estimate_range_duration(
    volatility: float,
    volume_pressure: float,
    buy_ratio: float,
    liquidity_force: float,
    entropy: float = 0.5,
):
    """
    Estimate range survival window.

    Inputs are normalized values where possible:
    - volatility: 0-1
    - volume_pressure: -1 to 1
    - buy_ratio: 0-1
    - liquidity_force: 0-1
    - entropy: 0-1
    """

    stability = (
        (1 - min(volatility, 1)) * 0.35
        + min(max(buy_ratio, 0), 1) * 0.20
        + min(max(liquidity_force, 0), 1) * 0.25
        + (1 - min(max(entropy, 0), 1)) * 0.20
    )

    pressure_penalty = abs(volume_pressure) * 8

    base_minutes = int(5 + stability * 20 - pressure_penalty)
    base_minutes = max(1, base_minutes)

    spread = max(2, int(base_minutes * 0.45))

    return {
        "estimated_duration": {
            "min_minutes": max(1, base_minutes - spread),
            "max_minutes": base_minutes + spread,
        },
        "confidence": round(min(max(stability, 0), 1), 2),
        "status": "stable" if stability > 0.65 else "fragile",
    }
