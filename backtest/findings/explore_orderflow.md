# Order-flow & book-imbalance directional signals — KXBTC15M / KXETH15M

**Verdict up front: NO TRADEABLE EDGE.** Every micro-structure signal tested
(aggressor flow, book imbalance, large prints, contract-price momentum) is
**LAGGING** — it carries essentially zero information beyond the contract price
that already exists at decision time. The eye-popping 90–100% "hit rates" are the
classic trap: they only appear once the price is already at 0.95+/0.05−, i.e. the
outcome is known and priced. Where the price still offers value (0.35–0.65),
following the signal **loses 5–25c/contract after fees**.

## Data & method

- **Primary:** `backtest/data/trades.parquet` — 2,437,557 trades across **2,497
  KXBTC15M windows**, final 180s, trade-level (sec_to_close, yes_price, no_price,
  size, taker_side), joined to `markets.parquet` outcome (`result`, ~50/50).
- **Book (signal b):** `livepaper/data/paper.db` `book_snaps` — 34k snaps over
  **33 live windows** (only place with depth/bid-size). Small n, but the result
  is structural, not statistical.
- **Method:** at each decision time T ∈ {150,120,90,60,45,30,20,10,5}s-to-close,
  compute the signal from a 30s rolling flow window, the prevailing yes_price
  px_T, and the outcome y. **The test** is not "does the signal match the
  outcome" — it's *"conditional on the signal at T, does the outcome beat the
  PRICE at T by more than fees."* Trade the side the signal points to, enter at
  px_T, charge the real Kalshi taker fee `ceil_cent(0.07·p(1−p))`.

## Baseline: the market is efficient (this is what kills it)

The contract price is a near-perfectly-calibrated probability at every horizon:

| px_T bucket | n | avg px | realized yes |
|---|---|---|---|
| 0.4–0.5 | 207 | 0.456 | 0.430 |
| 0.5–0.6 | 195 | 0.554 | 0.559 |
| 0.6–0.7 | 239 | 0.654 | 0.649 |
| 0.9–1.0 | 10148 | 0.991 | 0.992 |

Realized yes-rate ≈ price in every bin. Any edge must come from a signal the
price has **not** absorbed. None does.

## (a) Aggressor flow & (c) large prints — LAGGING

`hit_vs_outcome` tracks `avg_entry_px` almost exactly at every T (the flow already
moved the price to where it's "right"). Strongest buckets (|signal|>p80), after fees:

| signal | T | n | hit | entry_px | net $/ct |
|---|---|---|---|---|---|
| aggressor_flow | 60 | 495 | 0.410 | 0.423 | **−0.024** |
| aggressor_flow | 30 | 489 | 0.262 | 0.256 | −0.005 |
| large_prints | 60 | 478 | 0.431 | 0.437 | −0.017 |
| large_prints | 30 | 446 | 0.291 | 0.293 | −0.012 |

Net is negative across the board (you pay the fee for information already in the
price). The few +0.02–0.03 cells at |sig|>p95 have SE 0.022–0.029 — not
significant and not consistent across adjacent horizons.

**Leading test** — correlation of the signal at T with the *residual* (outcome −
price): `corr(net_flow, outcome−price)` ∈ [−0.033, +0.025]; `corr(big_prints,…)`
∈ [−0.028, +0.039]. Indistinguishable from zero. The flow does **not** predict
the part of the outcome the price hasn't already captured.

## (d) Price momentum — already priced; fade loses

Contract mid-price drift: `hit_vs_outcome ≈ entry_px` again (continuation is
priced). `corr(momentum, outcome−price)` ∈ [+0.011, +0.049] — the largest of any
signal but still trivially small. Net $/ct hovers around 0 with SE that swamps it.
The **momentum-FADE** variant (bet against drift) loses −0.01 to −0.06/ct at every
horizon — there is no mean-reversion edge either.

## (b) Book imbalance — the purest lagging illustration

| T | depth_imb hit | PRICE hit | imb≠price cases | imb wins when it disagrees |
|---|---|---|---|---|
| 120 | 0.93 | 0.93 | **0** | — |
| 60 | 0.96 | 0.96 | **0** | — |
| 30 | 1.00 | 1.00 | **0** | — |

- **Depth imbalance is mechanically identical to the price.** It *never* disagrees
  with the price's sign (`imb≠price n=0`). When YES is winning, YES is cheap, so
  YES depth piles up — the imbalance IS the price, restated. The "1.00 hit rate"
  is purely the price being at ~1.0 by 30s out. Zero independent edge.
- **Best-bid-size asymmetry (`bid_imb`) is worse than useless:** when it disagrees
  with the price (n=5–14 per horizon), it is wrong — `imb_wins` = 0.00–0.08.
  Following bid-size imbalance actively loses.

## The honest check: signals where the price still has value (0.35 ≤ px ≤ 0.65)

This is the only regime where edge could exist. Restricting to it (667 snapshots),
`edge = hit − entry_px` for the strongest (|sig|>p80) bucket:

| signal | best/worst edge across T | net $/ct |
|---|---|---|
| aggressor_flow | −0.005 to −0.229 (all ≤0) | −0.03 to −0.25 |
| large_prints | mostly negative; one +0.19 at n=6 | noise |
| momentum | −0.14 to +0.09, SE ~0.13 | noise |

Every robust cell is negative. The positive ones are n=6–13 with SE 0.13–0.18 —
noise that flips sign between adjacent horizons. **When the price is a genuine
coin-flip, following the order flow is a money-loser**: you buy the side the flow
just pushed expensive, and it reverts to the ~50/50 outcome.

## $/day on $50

There is no positive-expectancy configuration to size. The best honest estimate of
after-fee edge per trade is **≤ 0 ± ~2c/contract**. At any realistic trade count
this is **$0/day, trending negative once you add fees and slippage**. It does **not**
hit $5/day, and there is no bankroll at which a zero/negative-edge signal becomes
profitable.

## Why this market is efficient at the micro level

KXBTC15M settles on the mean of 60 RTI samples over the final 60s. The contract
price is a tight, fast, calibrated tracker of the underlying's distance-to-strike.
By the time order flow or book depth reveals direction, the price has already moved
there — the information and the price are the same event. The only edges that have
ever shown up in this repo's prior work come from the **underlying** (TWAP-anchored
panic-fade, per MEMORY), not from the contract's own microstructure. This run
confirms: **the contract's own order flow is not a source of alpha.**

## Confidence & caveats

- **High confidence** on (a)/(c)/(d): 2,497 windows, the lagging structure
  (hit≈entry, corr≈0) is consistent across all horizons and survives the
  uncertain-price restriction. This is a structural, not small-sample, negative.
- **Medium-high** on (b): only 33 live windows, but `imb≠price n=0` for depth is a
  structural identity, not a sample artifact — more data won't reverse it.
- **Top risk we guarded against:** the leading-vs-lagging trap (late-window "hit
  rates" that are just the price). We explicitly measured signal-vs-price-residual
  and restricted to uncertain prices; the edge vanishes in both.
- **Untested:** earliest part of the window (>180s out) isn't in the parquet, and
  ETH/KXBTCD/KXETHD flow at scale. But the >180s regime has even less information
  and wider spreads — no reason to expect edge there. Not worth the data pull.
