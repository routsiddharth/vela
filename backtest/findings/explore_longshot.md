# Favorite-Longshot / Systematic-Mispricing Calibration — KXBTC15M

**Verdict up front: NO persistent, tradeable edge. The apparent mispricings are
directional-regime noise (which way BTC trended that month), not a stable
calibration bias. Net after fees on $50: ≈ $0/day, with no bucket surviving an
out-of-sample / month-over-month sign test.**

---

## Data & method

- `backtest/data/trades.parquet` — 2,437,557 executed trades over 2,497 distinct
  KXBTC15M markets, Apr 3 – Jun 8 2026 (66 days). **Caveat baked in: this file is
  only the final 180s of each market** (`sec_to_close ∈ (0,180]`), so prices are
  late-window and heavily resolved (U-shaped: 21% of trades ≤0.02, 24% ≥0.98).
- `backtest/data/markets.parquet` — 6,308 markets with `result` (yes/no),
  `strike`, `true_settle`. Outcome is the join key.
- **To defeat the 180s survivorship slice**, I REST-pulled FULL-window trade
  history (`/markets/trades`, up to 900s before close) for a **time-diverse,
  stratified-by-date sample of 414 markets** → `trades_strat.parquet`
  (2.05M trades >180s before close). This is the clean test the prompt asked for.
- **Calibration**: per price bucket {0.02,0.05,0.10,0.20,…,0.90,0.95,0.98}, compare
  the realized YES win-rate to the price. Mispricing = win-rate − price.
  Significance via Wilson 95% CIs. Sample sizes per bucket: 20k–290k trades
  (trade-weighted) and 60–540 markets (market-level) — plenty for tight CIs.
- **Fee model** (from prompt): taker/ct = ceil_cent(0.07·p·(1−p)), min $0.01.
  Held to settlement → only the entry taker fee applies (winner redeems at $1, no
  exit fee). Round-trip if you ever exit early is ~2× that.

## Result 1 — no directional/side skew

- Markets resolve YES 50.4% (CI [48.5%, 52.4%], **0.5 inside**). No up/down bias.
- Taker aggressor wins 48.8–49.0% regardless of side — takers slightly lose to fees,
  no exploitable aggressor signal.

## Result 2 — the 180s-window calibration looks like it has edges… but they don't replicate

Trade-weighted over the full 180s file, two buckets show after-fee EV > 0 at the
pessimistic CI bound:

| bucket | price | actual win | mispr | best side | EV/ct (pt) | EV/ct (pess CI) |
|--------|-------|-----------|-------|-----------|-----------|-----------------|
| 0.20–0.30 | 0.253 | 0.292 | +3.8c | BUY YES | **+1.81c** | **+1.56c** |
| 0.80–0.90 | 0.858 | 0.846 | −1.2c | BUY NO  | +0.19c | +0.02c |

The 0.20–0.30 bucket is the only one that looks juicy: +1.8c after fee, ~119k
contracts/day available. The win-rate is **identical (0.2911 vs 0.2919) whether the
taker bought or sold** → it is NOT a momentum/aggressor artifact, it is a true
price-level property in this sample. So far this passes every na ïve test.

## Result 3 — the killer: it is one anomalous month, and it flips sign out-of-sample

Per-month mispricing of the 0.20–0.30 bucket (180s file):

| | Apr | May | Jun |
|---|---|---|---|
| mispricing | +0.9c | **−3.0c** | **+31.7c** |

The "edge" is **entirely** June 2026. Daily June detail: the 0.20–0.30 markets
resolved YES 79%, 75%, 94% on trending-up days and 1.8% on 6/8 — i.e. a handful of
trending-BTC days where 25%-priced longshots all hit. Only 72 distinct June markets
drive it; per-market YES rate is 0.417 (huge CI). High-volume trending days dominate
the trade-weighted mean.

**Formal OOS (train Apr+May, test June): every mid-range bucket flips sign.** The
0.20–0.30 bucket is −1.3c in train, +31.7c in test. Not a single mid-range bucket
agrees in sign across the split. Only the extreme tails agree, and there the
mispricing is <1c — below the fee.

## Result 4 — clean early-window test (>180s, 414 time-diverse markets): same conclusion

This is the test the late-window caveat demanded. Pooled, the early-window
calibration looks almost perfectly calibrated (|mispr| ≤ ~2c everywhere) — but that
is because the months **cancel**. Per month it is wildly unstable:

| bucket | price | Apr | May | Jun | pooled | same sign? |
|--------|-------|-----|-----|-----|--------|-----------|
| 0.02–0.05 | 0.037 | −3.5 | +1.4 | +15.2 | +2.0 | no |
| 0.05–0.10 | 0.077 | −6.7 | −3.1 | +21.6 | +0.4 | no |
| 0.10–0.20 | 0.156 | −7.6 | +4.6 | +13.0 | +1.9 | no |
| 0.20–0.30 | 0.258 | −5.6 | −3.6 | +11.0 | −1.4 | no |
| 0.30–0.40 | 0.358 | −6.2 | −0.7 | +11.2 | −0.6 | no |
| 0.50–0.60 | 0.554 | +1.4 | −1.5 | −5.9 | −1.2 | no |
| 0.80–0.90 | 0.853 | −1.4 | +0.9 | +6.4 | +0.9 | no |
| 0.90–0.95 | 0.927 | −6.0 | −4.8 | −1.5 | −4.8 | **YES** |
| 0.98–1.00 | 0.988 | +1.2 | +0.2 | +1.2 | +0.5 | **YES** |

(values are mispricing in cents = win% − price)

Only three buckets keep a consistent sign across all three months:
- **0.00–0.02**: −1.0c — below the $0.01 fee floor, not exploitable.
- **0.98–1.00**: +0.5c — below fee, not exploitable.
- **0.90–0.95**: −4.8c consistently — buying YES at ~0.92 loses (they win only ~88%).
  This is the ONLY thing resembling a persistent signal: **deep favorites at
  0.90–0.95 are OVER-priced** (equivalently the cheap NO is under-priced → BUY the
  0.05–0.10 NO / SELL the 0.92 YES). But the magnitude swings 6.0c→4.8c→1.5c and
  decays toward zero in the most recent month, and the symmetric longshot bucket
  does NOT confirm (it sign-flips). With one consistent-sign bucket out of 14 and a
  decaying magnitude, this is most likely survivorship of a noisy series, not a law.

The classic favorite-longshot bias (longshots over-priced, favorites under-priced)
is **not stably present** here. BTC 15-min ATM markets are dominated by directional
variance: whichever way BTC drifts in a window decides everything, and that drift is
mean-zero across months, so no price level carries a durable calibration premium.

## Per-day economics (the number you asked for)

- If you had naively traded the pooled "edge" (buy YES 0.20–0.30): ~$3.57/day point
  estimate on $50… but its true OOS expectation is ≈ $0 and it had a **−$X month
  (May) and depends on one +month (June)**. Sharpe ≈ noise. Not real.
- The only persistent-sign bucket (sell 0.90–0.95 YES) clears the fee on paper
  (−4.8c pooled vs ~1c fee), giving a notional +3–4c/ct; ~$50/0.92 ≈ 54 ct/trade →
  ~$1.6–2/trade. **But** its sign-stability is 3/3 months on a decaying magnitude
  with no symmetric confirmation — I do **not** trust it as a $5/day engine; live it
  would most likely realize ≈ $0 ± a lot.

## HITS $5/DAY (=10%/day on $50)?  **No.**

A persistent 2–3c calibration edge would compound to that. It does not exist here.
The pooled tables that look exploitable are averages over months that individually
disagree in sign. Every candidate fails the month-over-month / train-test sign test.

## Confidence & top risk

- Confidence in the **negative**: high. 2.0M+ early-window trades, 414 time-diverse
  markets, tight CIs, and a clean OOS split — the instability is not a small-sample
  illusion, it is the dominant feature.
- Top risk to the negative read: only ~2 months of data and a single REST stratified
  pull; a longer history *might* surface a small (<1c) stable tail bias. But <1c is
  below the fee, so it would not be tradeable regardless.
- Top risk had I called it positive: textbook **overfitting to a directional
  regime** — the 0.20–0.30 "edge" is literally one trending month.

## Reproduce

```
source ../ingest/venv/bin/activate
python backtest/analysis/explore_longshot.py                 # full calibration + persistence
python backtest/analysis/explore_longshot.py --fetch-strat 400  # re-pull stratified early-window
```
