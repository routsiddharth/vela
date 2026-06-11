# TWAP-vs-spot divergence FADE — findings

Date: 2026-06-09. Author: divergence/fade investigation.
Scripts: `backtest/analysis/fade_lib.py`, `fade_backtest.py`, `fade_pooled.py`.
All signals CAUSAL (de-bias is trailing-24h median; proxy variance is a trailing rolling std;
no window uses its own outcome or any future window).

## VERDICT: **NO.**

There is no divergence/fade rule with net-positive EV after fees that survives out-of-sample.
The premise — that the market over-reacts to late spot moves relative to where the 60-sample
TWAP will settle — is **not supported by the data**. When the model most disagrees with the
market, the **market is right and the model is wrong.**

## The theory and how it was tested

Theory: settlement S = mean of 60 RTI samples over the final 60s. After a late spot spike/drop,
naive traders chase spot and push the Kalshi price toward the new spot, but the AVERAGE is
anchored by the samples already locked, so the market over-reacts → fade it (bet the TWAP side).

Test, at decision times τ ∈ {10,15,20,30,45} (all ≤ 60s, so some settlement samples are locked):
1. **Model fair value (causal).** `btc_lib.estimate()` gives the de-biased TWAP point estimate
   `mhat` (locked samples + martingale remainder). Converted `mhat` → P(YES) with
   `sd_S = sqrt(diffusion_var + proxy_var)`:
   - diffusion of the remaining (τ−1) samples, σ_sec = **$3.69/s** from the Binance 1s data;
   - **proxy/de-bias tracking error** = causal trailing std of (mhat − true_settle).
   The proxy term DOMINATES (≈$8–17) — diffusion alone ($1–10) badly under-states the true
   residual (≈$10–16). Calibration is good once both are included (Brier 0.020–0.029; at the
   confident tail predicted 0.998 → empirical 0.998).
2. **Market-implied prob + real tradeable price (causal).** From `trades.parquet`, at τ use only
   trades with sec_to_close ∈ [τ, τ+8] (at-or-before the decision). `mkt_yes` = nearest yes_price;
   the **ask** for a side = nearest trade where `taker_side` equals that side (the price a taker
   would actually pay). Fade = buy the TWAP-favored side at its ask.
3. **Net P&L** = win → (100 − ask¢) − fee; lose → (−ask¢) − fee, with the quadratic fee
   `ceil(0.07·p·(1−p)·100)¢`, min 1¢.

## Hard numbers

Pooled across all τ, **5,059** tradeable windows (a real ask on the favored side; ~70% of windows
have one — no meaningful selection bias, both sides ~equally present).

**Sweeping the divergence threshold (edge = model_p_side − market_p_side), pooled, net ¢/contract:**

| edge ≥ | n   | net ¢  | 95% CI (bootstrap) | win rate |
|--------|-----|--------|--------------------|----------|
| 0.00   | 3099| −0.70  | (−1.21, −0.19)     | 0.896    |
| 0.02   | 551 | −0.01  | (−2.72, +2.73)     | 0.428    |
| 0.03   | 506 | +0.51  | (−2.40, +3.49)     | 0.385    |
| 0.05   | 453 | +0.74  | (−2.32, +3.81)     | 0.327    |
| 0.08   | 417 | +0.31  | (−3.14, +3.67)     | 0.278    |
| 0.12   | 391 | −0.36  | (−3.65, +2.99)     | 0.238    |
| 0.20   | 347 | −0.59  | (−4.03, +2.94)     | 0.184    |

**Best cell found** (edge ≥ 0.05 & ask ≤ 0.85): n=419, net **+1.16¢**, CI **(−2.05, +4.44)**.
Every positive cell's 95% CI straddles zero. The single biggest realized loss is **−100.9¢**
(one flip at a 0.99 ask). The payoff is brutally skewed: at the edge thresholds that pick genuine
fades, win rate is 15–40%, **median pnl is −2 to −4¢**, mean is pulled toward zero only by rare
big wins — a lottery whose mean is unmeasurable from ~400 trades.

**Out-of-sample (date split 2026-05-07; IS n≈2437, OOS n≈2622):** the sign FLIPS.

| rule                  | IS net ¢ | OOS net ¢ | OOS 95% CI       |
|-----------------------|----------|-----------|------------------|
| edge ≥ .02            | −0.99    | +0.98     | (−2.97, +4.76)   |
| edge ≥ .05            | −0.76    | +2.15     | (−2.04, +6.32)   |
| edge ≥ .05 & ask ≤ .85| +0.01    | +2.20     | (−2.05, +6.61)   |
| edge ≥ .02 & ask ≤ .9 | −1.23    | +1.87     | (−2.38, +6.13)   |

The in-sample half is **negative for every rule**; the positive numbers are all in the
out-of-sample half. Picking on IS would have rejected the strategy. Sign instability across
halves = noise, not edge.

## Why it fails — the market is not over-reacting; the model is over-confident

In the divergence cases (edge ≥ 0.05, n=453) where the model says the favored side is worth
**0.741** but the market prices it at **0.288**, the **realized win rate is 0.327.**
- Brier — **market 0.116 vs model 0.360.** The market is ~3× more accurate exactly where they
  disagree. The market correctly prices information the TWAP point-estimate misses (proxy/de-bias
  error, genuine late drift the martingale model wrongly mean-reverts).
- The realized rate (0.327) is only ~4pts above the market price (0.288) — a ~**3.8¢ gross edge
  at the mid**, itself inside the noise band and OOS-unstable.

That tiny gross edge is fully consumed by frictions:

```
gross edge @ market mid :  +3.84 ¢
ask premium (taker pays ask, not mid) : −1.67 ¢
quadratic fee                          : −1.43 ¢
-----------------------------------------------
net @ ask                              : +0.74 ¢   (CI −2.0..+4.4, OOS sign-flips)
```

## Conclusion

Same wall as the near-lock taker trade: a sub-cent gross signal (here, even its existence is
within noise) buried by the taker ask premium (~1.7¢) plus the quadratic fee (~1.4¢). The
60-sample averaging IS already incorporated by the market — divergences are the market being
right about proxy/drift risk, not an exploitable over-reaction. **Do not deploy a TWAP-fade
taker strategy.** Any future attempt would need (a) maker fills (post inside the spread to avoid
the ask premium and the fee — Kalshi maker fee is also quadratic but you capture the spread),
and/or (b) a materially better fair-value model than the de-biased Binance proxy, whose ≈$10–16
tracking error is the binding source of error at these τ.
```
