# Pool Intelligence Formula Engine

## Time-In-Range Prediction (NEW)

The intelligence layer now includes an estimated range survival duration after range probability calculation.

Purpose:

- Estimate how many minutes a pool can statistically remain inside the predicted range.
- Avoid treating range as static.
- Add a time dimension to Monte Carlo/range analysis.

Inputs:

- volatility
- volume pressure
- buy ratio
- liquidity force
- entropy

Flow:

```
Range Detection
      ↓
Monte Carlo Probability
      ↓
Time-In-Range Engine
      ↓
Estimated Survival Window
```

Example output:

```json
{
  "range": "$0.000012 - $0.000015",
  "estimated_duration": {
    "min_minutes": 5,
    "max_minutes": 12
  },
  "confidence": 0.72,
  "status": "stable"
}
```

The duration is a probability estimate, not a guaranteed holding time.

Existing formula architecture remains unchanged.
