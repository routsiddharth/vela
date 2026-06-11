# Ladder Structural / Cross-Strike Arbitrage — Findings

**Strategy tested:** riskless structural arbitrage across the ~100-strike hourly
ladders of Kalshi crypto markets (KXBTCD / KXETHD = "BTC/ETH > $K at top of hour?").
Four mispricing families:

- **(B) YES/NO internal cross** — buy YES + buy NO of the *same* strike for < $1.
- **(E) Cross-strike box** — for K_hi > K_lo, `yes_bid(K_hi) > yes_ask(K_lo)`:
  sell the higher-strike YES, buy the lower-strike YES, collect a credit on a
  payoff that is structurally ≥ 0 (a binary call spread you're paid to hold).
  This is the executable form of the monotonicity violation in the brief.
- **(C) Butterfly / negative density** — 3 adjacent strikes implying negative
  probability density, traded as buy–sell2–buy.
- **(monotonicity diagnostic)** — does mid(K) decrease in K.

All legs are **taker**; fee/contract = `ceil_cent(0.07·p·(1−p))`, min $0.01, netted
on every leg.

## Data & method

Two independent sources, both live:

1. **Live full ladders via Kalshi REST** — polled `/markets?series_ticker=…&status=open`
   every ~5 s for ~12 min, capturing every strike's top-of-book on every open
   BTCD/ETHD event (incl. the 04:00 UTC resolution). **720 ladder-snapshots**, 8
   distinct events, 80–188 strikes each. Each snapshot scanned for B / E / C.
2. **Historical `book_snaps` (paper.db)** — 96k crypto book rows over 3.4 h. Used
   for (B) over all rows, and for cross-strike monotonicity on the one BTC event
   that carried multiple strikes (`KXBTCD-26JUN1017`, 5 strikes, 12,468 matched
   timestamps → 38,612 adjacent-strike checks).

Code: `backtest/analysis/explore_ladderarb.py` (`collect` → `analyze`).

## Results

| Check | Sample | Raw hits | Survive spread + fees |
|---|---|---|---|
| (B) YES/NO internal cross | 96,991 REST + 95,991 DB rows | **0** | **0** |
| (E) cross-strike box | 720 ladders | 0 real | **0** |
| (C) butterfly | 720 ladders | 0 real | **0** |
| monotonicity (mid) inversions | 720 ladders | 206 | 0 tradeable |
| monotonicity (DB, executable) | 38,612 checks | 0 | **0** |

### Two bugs caught and corrected (both produced fake "free money")

1. **Butterfly mis-construction.** A negative "density" (`ya(K−h) − 2·yb(K) + ya(K+h) < 0`)
   looked like a credit butterfly, but the executable payoff has a **−1 region**
   (b < S ≤ c) because you sell the steep middle strike at *bid*. Worst-case payoff
   is −1, so it's riskless only if credit > $1 — impossible. After computing the
   true worst-case region payoff: **0 real butterflies.**
2. **Phantom book levels.** Far-from-money strikes carry no posted order, so the
   API returns `yes_ask = 0` / `yes_bid = 0` defaults (e.g. a deep-ITM strike
   showing `ya = 0.0`). My first box scan "found" 444 arbs buying YES@$0 — all
   phantom. Requiring a *real two-sided market* (`0 < yb < ya < 1`) removed
   **100%** of them.

After both fixes: **0 riskless violations across 720 live ladders + 192k DB rows.**

### Why there's nothing here

- `yes_ask = 1 − best_no_bid` by construction, so `yes_ask + no_ask = (1−no_bid) +
  (1−yes_bid) = 2 − (yes_bid+no_bid) ≥ 1` whenever the book isn't crossed. DB min
  was **1.001**, mean **1.018**. Internal cross is structurally impossible.
- The market maker keeps the **executable** ladder perfectly monotone. Every one of
  the 206 residual mid-inversions had a tradeable box credit of **≤ 0** (max = 0.00) —
  they live entirely inside a 1–2¢ bid-ask spread.
- Min YES spread = **1¢**, median 2¢. A cross-strike lock needs the spread to invert
  by more than fees (~2×1¢); it never does.

### Capacity (moot, but measured)

If an arb existed, size would not bind: median top-of-book 600 ct, p90 ~6,085 ct,
depth_yes median ~160k. The constraint is **frequency = 0**, not size.

## $/day on $50

**$0.00/day.** Zero surviving riskless edges. With 0 events, no bankroll produces
income. Capacity is large but irrelevant.

## Verdict

The Kalshi crypto ladder MM is internally arbitrage-free at the quote level.
Structural / cross-strike arbitrage does **not** clear the 1–2¢ spread + taker fees
and earns **$0/day**. Does not hit $5/day. The only residual angle is **model-based
relative value** (compare ladder mids to a lognormal fair value) — but that is
*directional, not riskless*, and is a different strategy from the one specified here.
