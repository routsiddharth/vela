# BTC TWAP-endgame — empirical verdict (backtest synthesis)

*Tested on live data, 2026-06-09. 6,308 settled KXBTC15M windows (2026-04-03→06-09),
1.9M Binance 1s prices, 2.4M Kalshi trades. 4 subagents + red-team audit. Code in
backtest/, data in backtest/data/.*

## Verdict in one line
The strategy **as written in STRATEGY.md (buy the near-locked winner as a taker) does NOT
work** — it is net **−0.5 to −0.95¢/contract** everywhere. But a **refinement does work**
with high confidence: **fade the late-window panic on the TWAP-favored side.**

## What the data proved

### 1. Lock detection works (audited, no leakage)
- Settlement = plain mean of 60 RTI samples over the final 60s (confirmed: plain mean beats
  trimmed/median fits to the true settlement value `expiration_value`).
- Binance is a usable RTI proxy but its bias **drifts hard** (weekly median −$31→+$97). A
  **causal trailing-24h median de-bias** fixes it: residual mean≈0, std $10.4, q99 $32.
- Betting the de-biased-estimate side, filtered by |m̂|>threshold, the realized win rate:
  - τ=30s, |m̂|>$50 → **99.97%** (1 flip/3848); |m̂|>$75 → 100% (0/2903).
  - τ=45s, |m̂|>$50 → **100%** (0 flips/3833).
  - Red team: no look-ahead, no forward-fill leakage, causal de-bias, holds out-of-sample.

### 2. The taker near-lock trade is dead (the market is efficient w.r.t. spot)
When the model is confident, BTC is visibly far from strike, so the **crowd already prices the
winning side at 0.985–0.999**. The "buy 97¢ worth 99¢" gap does not exist at the confident
tail; the quadratic fee's **round-up to 1¢** at the tails buries the sub-cent gross gap. Every
(τ, threshold) cell is net-negative. Maker doesn't rescue it: maker fees still round to 1¢ at
the tails, the volume-incentive rebate excludes prices >0.97, and generic resting bids lose to
adverse selection (3.3pp win-rate degradation on fills).

### 3. The edge that DOES work: TWAP-anchored panic-fade
The 60-sample average is anchored by samples already collected, so a late spot move can't move
the settlement much — but naive traders watch *last price* and **dump the soon-to-win side in
panic** when spot scares them. Because the average is locked, that side still pays $1.

- Cheap winning-side offers (≤0.97) appear in ~49–879 windows, **persist a median 62s** (not a
  latency flash — 82% last >15s), and recur across **33 distinct days, both halves, every
  month**. ~258k cheap contracts in the sampled windows; median 3,276 takeable in the best
  single second.
- **78% of the cheap volume is panic SELLS** (takers dumping the winner into bids) → the
  natural capture is **resting cheap bids on the TWAP-favored side** (maker role, but the TWAP
  anchor inverts the usual adverse selection — you *want* to catch these).

### Backtest of the rule (decision at τ=45, |m̂|≥THR, take winner only at ≤CAP, sec[5,45])
| THR | CAP | net ¢/ct | win% | windows/losers | OOS net ¢ | est $/day (25–100% capture) |
|----|----|---------|------|----------------|-----------|------------------------------|
| 40 | 0.97 | **+10.1** | 100 | 41 / 0 | +12.0 | ~$380–1,500 |
| 40 | 0.99 | +3.6 | 100 | 153 / 0 | +4.5 | ~$415–1,660 |
| 50 | 0.97 | **+3.4** | 100 | 19 / 0 | +3.2 | ~$34–140 |
| 75 | 0.97 | +3.9 | 100 | 7 / 0 | +3.2 | ~$14–56 |

- **0 losing windows in 2 months**, positive **out-of-sample**, positive **every month**
  (Apr +4.7¢, May +3.1¢, Jun +3.2¢), bootstrap CI excludes zero.
- The win rate is NOT cheap-subset luck — it inherits the τ=45/$50 lock reliability proven on
  3,833 windows. The cheap entry sets the *profit*; the lock sets the *win rate*.
- **Robust choice: CAP≤0.97, THR≥50** — buying at avg ~0.94 to win, a flip costs −0.94 but the
  flip rate is <0.026%, so the cushion is huge.

## Confidence
- **>95%**: the edge exists in-sample, is causal, audited, out-of-sample- and month-stable; the
  canonical taker version fails.
- **NOT yet 95% (needs paper-trading)**: live **fill rate as a maker** (queue priority /
  competition), forward **persistence** as it's exploited, and resilience to a **basis
  dislocation** (USDT/RTI) in production.
- **Capacity is modest** ($hundreds/day), bursty (clusters on panic days). A grind, not a
  printer — consistent with STRATEGY.md's own expectations.

## What to forward-test (paper, no capital)
1. Live: rest cheap bids on the |m̂|≥50 side at τ≤45; measure ACTUAL fill rate vs the backtest.
2. Tighten the de-bias lookback (2–6h halves residual) + add a "skip if residual unstable" gate.
3. Track realized win rate vs the <0.026% modeled flip rate; size for the eventual loss.
