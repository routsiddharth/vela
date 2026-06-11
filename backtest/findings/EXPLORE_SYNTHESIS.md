# Six distinct strategies vs the $5/day target — compiled findings

*Tested 2026-06-10. Six subagents, each a genuinely distinct approach (nothing to
do with the TWAP/panic-fade). Target: $5/day on $50 (= 10%/day). Each backtested
on real data (markets.parquet 6,308 windows, trades.parquet 2.4M prints, live
paper.db book/tape, fresh Binance/Kalshi REST pulls) with fees modeled exactly.*

## Result: none of the six hits $5/day. All net ≈ $0 or negative after fees.

| # | Strategy | Signal real? | $/day on $50 (after fees) | $5/day? | Why it fails |
|---|----------|-------------|---------------------------|---------|--------------|
| 1 | **Directional** (BTC momentum/reversion, enter early) | **Yes** — 53% base, up to 60% on big moves | **negative** (~−1¢/ct bleed) | No | Kalshi prices the BTC move to fair within **60s**; ask + fee turns real signal into a steady loss |
| 2 | **Market-making** (two-sided spread capture) | marginal | **≈ $0** (best cell +$168/day *point est, 95% CI −155…+449*) | Maybe (low conf) | Adverse selection eats the spread; neutral book = break-even. The "+$1,487/day" headline was a **2-of-14-market directional artifact** |
| 3 | **Ladder arbitrage** (cross-strike, KXBTCD/ETHD) | **No** | **$0.00** | No | Ladder is arb-free at the quote: `yes_ask+no_ask ≥ 1` structurally; monotonicity holds inside the 1–2¢ spread. (Caught 2 bugs that faked 4–5-figure "profits".) |
| 4 | **Favorite-longshot / calibration** | **No** | **≈ $0** OOS | No | Markets well-calibrated (settle YES 50.4%). The one "edge" (0.20–0.30 bucket) was **one trending month** — flips sign train/test |
| 5 | **Order-flow / book-imbalance** | **No** | **≈ $0**, negative | No | Flow & depth are **lagging echoes** of an already-efficient price; `corr(signal, outcome−price) ≈ 0`; following them loses to fees |
| 6 | **Wildcard** (time-of-day, round-number, BTC↔ETH lead-lag, autocorr, open-overreaction) | mostly no | **≈ $0** / negative | No | Mean-reversion is real (52%) but sits on the fee wall and the *tradeable* version inverts to 42%. 6 ideas killed |

## The single root cause (all six independently hit it)

1. **The market is efficient w.r.t. every public signal.** Any signal strong enough
   to move the 15-min outcome (BTC momentum, order flow, the open quote) is already
   in the Kalshi price within ~60 seconds. Directional prediction *works* (up to 60%
   hit) but you can't buy it below fair.
2. **Kalshi's fee is a wall at the coin-flip.** `taker = ceil_cent(0.07·p(1−p))`
   peaks at **~1.75¢/contract near 0.50** — exactly where the ATM up/down markets
   trade. A 52–53% edge is worth <2¢ gross and the fee eats all of it.
3. **The bankroll is not the binding constraint — the edge is.** The two strategies
   with a *flicker* of positive expectancy (MM on BTC15M spread≥2¢; panic-fade) are
   **capacity-limited to a few $/day with variance that includes zero**. More than
   ~$10–20 of working capital doesn't help, so a bigger bankroll changes nothing.

## What this means for the $5/day (10%/day) target

- **10%/day ≈ 3,700%/year compounded.** No strategy in an efficient, fee-bearing
  market sustains that. The target implies an edge that does not exist in these
  markets via any public signal.
- The honest ceiling here is **a few dollars/day at best, with high variance and a
  real chance of zero/negative** — a grind, not income.
- The only genuine levers to do better are ones explicitly out of scope or
  unavailable: lower fees (fixed by Kalshi), a **latency/data edge** faster than the
  market (the speed race — excluded, and competes with pros), or a **non-public
  signal** (news/flow you can't get from the tape). None is a $50-bankroll play.

## The least-bad real options (in order)

1. **Accept the real number.** Run the best of {panic-fade, MM-on-BTC15M-spread≥2¢}
   knowing it's $0–3/day with variance — and judge it on a week of live paper, not a
   day. It will not be $5/day.
2. **If $5/day income is the actual goal, these markets at $50 are the wrong
   vehicle** — the math doesn't support it. Bigger bankroll doesn't fix a
   capacity-limited near-zero edge.
3. **Stop spending fees on coin-flips.** Every strategy that trades near 0.50 is
   fighting the 1.75¢ fee. If anything is ever positive, it lives in the tails
   (cheap, low-fee) — which is where the original panic-fade already pointed.

Per-strategy detail: `explore_{directional,marketmaking,ladderarb,longshot,orderflow,wildcard}.md`.
