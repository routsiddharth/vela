# Wildcard inefficiency hunt — KXBTC15M / KXETH15M

*Empirical, brutally honest. 2026-06-10. Code: `backtest/analysis/explore_wildcard.py`,
`explore_leadlag.py`, `pull_eth_1m.py`. Data: 6,308 settled KXBTC15M windows
(2026-04-03 → 06-09, 67 days), Binance 1m BTC+ETH full history, 28 live 15M
open-quotes from `livepaper/data/paper.db`.*

## TL;DR

I tested the untested ideas: **outcome streaks/autocorrelation (e)**,
**time-of-day/session (a)**, **BTC→ETH lead-lag (c)**, **opening-price
overreaction (d)**, and **round-number magnetism (b)**. The two that looked
promising in raw stats — **outcome mean-reversion** and **BTC→ETH lead-lag** —
**both die under honest scrutiny**:

- **Mean-reversion is statistically real (52%, p=0.0017, OOS-stable) but
  economically unexploitable.** When you can actually buy the reverted side
  cheap (≤0.49), its win rate *collapses to 42%* (a net loss). The market prices
  intra-window momentum into the open. **No edge.**
- **BTC→ETH lead-lag at 1-min horizon is zero** (corr of BTC move-so-far vs
  ETH's remaining move = 0.016). BTC/ETH are contemporaneously co-moving
  (corr 0.88), no exploitable lag.

**$/day on $50: ~$0 (mean-reversion nets ≈0 to negative after the entry trap).
Does NOT hit $5/day.** Honest negative.

---

## What I tested and what I found

### (e) Outcome autocorrelation / streaks — REAL signal, UNEXPLOITABLE
- **Effect exists.** Runs test z=+3.16 (more alternation than chance). Betting
  *against* the prior window's outcome wins **51.99%** [51.0,53.2], n=6,288,
  p=0.0017. P(up | prev up)=47.6% vs P(up | prev down)=51.6% (−4pp spread,
  lag-1). OOS (last 40% of the timeline) holds at **51.5%**; positive every
  month (Apr 53.4 / May 51.0 / Jun 51.0). This is a genuine ~2pp edge.
- **But it's right at the fee wall.** Buying the reverted side as a taker:
  break-even is exactly at the open price 0.50 (E = −0.01c after the 2c taker
  fee). It's only +1c/ct at 0.49, +2c at 0.48. So it pays *only if you can
  routinely enter ≤0.49.*
- **The kill test (adverse selection).** Using Binance spot 1 min into each
  window vs the strike, I split windows by whether the reverted side opens
  *cheap* (the only case you'd buy it). Result:
  - reverted side **CHEAP** → reversion wins **42.2%** (n=3,087) → you LOSE.
  - reverted side **EXPENSIVE** → reversion wins **61.4%** (n=3,201) → you can't enter.
  - Early intra-window lean predicts the outcome hard: P(up | early up-lean) =
    **57.7%** vs **37.3%**. Within-window momentum is real and *priced into the
    open quote.* The 52% sequence-reversion is a property of the settlement
    series, not a tradeable asymmetry — whenever the discount appears, it appears
    *because* spot is moving against you.
- **VERDICT: real statistic, dead trade.**

> NOTE on a trap I avoided: the *continuous* return autocorrelation looks like
> **+0.50** in the raw data — but that is entirely **2 garbage windows**
> (|margin| 72,013 and another, where the strike ≠ prior settlement, the 0.05%
> data exception). Dropped, the clean margin autocorr is **−0.0085 ≈ 0**. No
> continuous-return edge. (Anyone who built on that +0.50 would be overfitting
> two bad rows.)

### (c) BTC→ETH lead-lag — ZERO
- BTC and ETH 1m returns correlate **0.88** *contemporaneously*. The lead-lag
  question is whether BTC's move *so far* in a window predicts ETH's *remaining*
  move (dec→settle). It does not: **corr = 0.016**. The residual signal
  (BTC-move − ETH-move) predicting ETH's remaining direction: 51.8% — within
  noise, no OOS edge.
- The initially-striking "31% accuracy" of the naive residual signal was a pure
  artifact of ETH's *own* move (which mechanically determines most of the
  outcome at 1-min granularity). Controlling for it, BTC adds nothing.
- Caveat: tested at **1-minute** granularity (best available ETH history). A
  sub-second lead-lag *might* exist but would be a latency race, not a
  structural edge, and there's no hint of it at 1m. **Not pursuing.**

### (a) Time-of-day / session — NOISE
- 24 UTC-hour up-rates: **1 of 24** significant at .05 (hour 13, p=0.024) —
  exactly the ~1.2 expected by chance. **0 survive Bonferroni** (.05/24). No
  hour is reliably biased away from 0.50.
- Day-of-week: all 7 within noise of 0.50 (weekday up 0.498, weekend 0.494).
- Volatility *does* vary by session (med |move| ranges $53 at 05:00 UTC to $106
  at 14:00 UTC, US-open) — useful for a *maker/vol* strategy's sizing, but the
  *direction* is not predictable. **No directional edge.**

### (b) Round-number magnetism — NULL
- P(settle drifts toward the nearest round level | within 15%) = 0.514 / 0.506 /
  0.515 for $1000/$500/$100. All CIs straddle 0.50. **No magnetism.**

### (d) Opening-price overreaction — market opens ~fair (small n)
- Windows open with a genuine directional lean (open mid std 0.090; |lean|>3c in
  82% of windows) — but the lean is the *correct* spot read, not a faddable
  error: open-mid>0.5 predicts the outcome at 53.8% on the 26 matched live
  windows (CI spans 0.50, and the sample is one clustered ~6.5h block, so this
  is only a feasibility probe). The adverse-selection result above is the real
  evidence: the open lean is *informative*, which is precisely why fading it
  (and why the mean-reversion entry) fails. **No fade edge.**

---

## Economics if you ignore the kill test (for reference only)

Were the 52% reversion entry-able at 0.49–0.50 (it is NOT), it would yield
~$9–28/day on $50 at 10–30 ct/window. **That number is fictional** — the
adverse-selection test shows the realistic fill is on the 42% side. The honest
expectation is **≈$0/day, drifting negative** after fees.

## Confidence & risks
- **High confidence (>90%)** that mean-reversion and lead-lag are *not*
  tradeable: large n, OOS-stable statistics, and a *mechanistic* explanation for
  why the tradeable version inverts (momentum is priced into the open).
- **Top risk:** multiple comparisons — I tested ~6 ideas; the one that "passed"
  (mean-reversion) was then killed by an independent OOS economic test, which is
  the correct discipline. The time-of-day "hit" was correctly dismissed as MC noise.
- **Data caveat:** the +0.50 spurious autocorr proves the dataset has 2 bad rows;
  I filtered them. ETH lead-lag tested only at 1m.

## Verdict
**Every wildcard idea in my mandate is a dead end for directional trading. The
KXBTC15M/ETH15M markets are efficient w.r.t. the open quote: any signal strong
enough to move the outcome is already in the price by the time you'd act. No
$5/day here. The only live edge in this repo remains the panic-fade (excluded
from my mandate); these alternatives do not add to it.**

## Ideas KILLED (don't re-test)
1. Outcome mean-reversion / streak-fade — real 52% but unexploitable (entry trap → 42%).
2. Continuous-return autocorrelation — ~0 once 2 garbage rows removed.
3. BTC→ETH lead-lag (1m) — corr 0.016, no edge.
4. Time-of-day / day-of-week directional bias — MC noise, 0 survive Bonferroni.
5. Round-number magnetism — null.
6. Opening-price fade — open is ~fair / informative, not faddable.
