# Claim B — The probability / fair-value model (adversarial review)

**Claim:** "A probability model estimates the true probability BTC moves a direction in the next few minutes, and decides where the real money is."

## Fair-value derivation
Contract pays $1 if BTC settle ≥ open-strike K. For driftless GBM the digital fair value is
`fair = Φ(d2)`, `d2 = ln(S_t/K) / (σ√τ)`. Over minutes the drift term is microscopic, so d2
is dominated by **log-distance of current spot from strike**, scaled by remaining-horizon vol.

With σ ≈ 60% annualized, `σ√τ`: full 15 min ≈ **0.32%**; 5 min left ≈ **0.185%**; 1 min ≈ **0.083%**.

| Current move | Time left | σ√τ | fair prob |
|---|---|---|---|
| +0.10% | 5 min | 0.185% | **0.705** |
| +0.05% | 5 min | 0.185% | **0.606** |
| +0.10% | 1 min | 0.083% | **0.886** |
| +0.20% | 2 min | 0.117% | **0.956** |

At open τ is large, d2→0, fair ≈ 0.50. As τ→0 the denominator collapses → fair snaps toward
0/1 (a 0.1% move that's a coin-flip at open is an 89% favorite with 1 min left). TWAP
settlement only blunts the very-last-minute snap (second-order).

## Repricer-not-forecaster argument
The only fast, high-information input is **current spot S_t**; K is fixed at open, σ is a slow
nuisance, τ is a clock. So the "probability model" is a **deterministic repricer of live
spot**, not a forecaster. Feed it the true current price → it returns the correct fair value
mechanically; it holds *zero* view on the future. Kalshi's book reprices off the *same* spot
but with latency/discretization. If your spot feed + Φ recompute beat the marginal Kalshi
quoter, you see fair 0.705 while the book still shows 0.66 — that gap is **latency/staleness
arb vs the CEX spot**, not prediction. Edge ∝ speed & feed quality − fees.

## Possible real alpha
Genuine prediction of S_{t+τ} would need: order-flow imbalance (predictive at 100ms–sec,
decays before a 15-min settle; already in next-tick spot), perp basis/funding (flat over 15
min), microstructure momentum/mean-reversion (tiny, sub-bp, swamped by spread + TWAP). A
garnish of a few tenths of a cent, fast-decaying — not the entrée.

## Verdict
The model is **real and correct, but a spot-tracking repricer, not a direction forecaster.**
Its edge is mapping live CEX spot → Φ(d2) faster/cleaner than the book — latency/staleness arb
— plus a thin microstructure sliver. The claim is **misleadingly framed**: the money is in
execution speed and feed quality, not foresight. **Confidence ~85%.** Caveat: intra-window
*vol* (not direction) may be slightly forecastable, adding a sliver — still not "predicting BTC
direction."
