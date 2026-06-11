# Maker-angle economics for the BTC 15-min near-lock trade

Date: 2026-06-09. Reproduce: `PYTHONPATH=$(pwd) python backtest/analysis/maker_economics.py`
(writes `backtest/analysis/maker_results.csv`). All signals causal (btc_lib
`estimate()` + `causal_bias()`; no window uses its own outcome or future windows).

## VERDICT: NO. Maker is not viable. Best net EV with real volume = ~0 (exactly
break-even) and every other cell is negative. The maker fee does NOT save the trade.

---

## 1. Real fee schedule (with sources)

Kalshi's fee = a quadratic in price, **rounded UP to the nearest cent per contract**.

- **Taker fee/contract = `ceil(0.07 · P · (1−P) · 100) / 100`.**
- **Maker fee/contract ≈ `ceil(0.0175 · P · (1−P) · 100) / 100`** — the maker rate
  is ~**one-quarter** of the taker rate (≈0.0175 vs 0.07), confirmed across
  multiple independent fee writeups.
- **Crypto 15-minute direction markets (KXBTC15M) ARE in the fee-bearing subset.**
  They are explicitly named as carrying maker/taker fees (alongside 5-min crypto,
  post-Feb-2026 NCAAB and Serie A) — they are NOT zero-maker-fee markets. Crypto
  uses the standard 0.07 taker / ~0.0175 maker base (the only halved-multiplier
  carve-outs are S&P 500 / Nasdaq-100 index markets at 0.035 taker).
- **The contract terms PDF (CRYPTO15M.pdf) contains NO fee terms** — it only
  specifies settlement (simple average of the CF Benchmarks index over the 60s
  prior to expiration, Source Agency CF Benchmarks, $0.001 min tick). Confirms the
  settlement mechanics already in FINDINGS_SOFAR.md; fees live only in the fee schedule.

### Incentive programs (checked — they do NOT rescue this trade)
- **Volume Incentive Program** (the cashback): rewards are capped at **$0.005/contract**
  AND only count "Eligible Volume" = trades executed **at prices between $0.03 and
  $0.97**. Our confident-tail fills happen at **0.97–0.999**, which are OUTSIDE the
  eligible band → **$0 volume rebate** on exactly the trades we'd make. Even if some
  fills landed ≤0.97, the cap (0.5¢) is below the fee (1¢).
- **Liquidity Incentive Program** (resting-order rewards): a **discretionary,
  per-market, pro-rata pool** (snapshot-based liquidity-provider score × a Time
  Period Reward), only on markets Kalshi flags as eligible, paid only if your share
  ≥ $1.00. It is **not** a guaranteed per-contract maker rebate, is not committed to
  crypto 15-min, and rewards posting depth across the book (incl. losing-side bids),
  not directional near-lock sniping. Not bankable for this strategy.
- No designated-market-maker / standing maker-rebate program for retail on these
  markets post-April-2025. (Kalshi's Oct-2024 volume program and Jan-2025 fee-rebate
  program were **terminated** per the Aug-2025 CFTC filing.)

Sources:
- https://kalshi.com/fee-schedule and https://kalshi.com/docs/kalshi-fee-schedule.pdf (canonical; JS/rate-limited)
- https://help.kalshi.com/en/articles/13823805-fees ("Maker fees are charged for
  orders placed that are not immediately matched … left as resting orders")
- https://marketmath.io/platforms/kalshi and /blog/kalshi-fees-guide-2026
  (taker `ceil(0.07·P·(1−P)·100)/100`; maker ≈ 1/4 of taker, ≈0.0175)
- https://www.predictionhunt.com/blog/kalshi-fees-complete-guide-2026 (crypto base
  0.07; maker rounds to ~0 for small trades — but see round-UP caveat below)
- https://help.kalshi.com/incentive-programs/{volume,liquidity}-incentive-program
- https://kalshi-public-docs.s3.amazonaws.com/regulatory/notices/Volume%20and%20Liquidity%20Incentive%20Program%20-%20August%202025.pdf
  (Appendix A: Volume Reward cap $0.005/contract, eligible prices $0.03–$0.97;
  LIP is a discretionary pro-rata pool; prior 2024/2025 programs terminated)
- https://kalshi-public-docs.s3.amazonaws.com/contract_terms/CRYPTO15M.pdf (settlement only, no fees)

### The decisive fee fact: round-UP kills the maker discount at the tail
The maker rate is 1/4 of taker, but the fee is rounded **up** to the next whole
cent, and the raw maker fee is tiny-but-nonzero at every tradeable price — so it
still rounds up to **1¢**:

| P     | taker | maker (round-up) | maker raw |
|-------|-------|------------------|-----------|
| 0.90  | 1.00¢ | **1.00¢**        | 0.158¢    |
| 0.95  | 1.00¢ | **1.00¢**        | 0.083¢    |
| 0.98  | 1.00¢ | **1.00¢**        | 0.034¢    |
| 0.99  | 1.00¢ | **1.00¢**        | 0.017¢    |

**Maker fee == taker fee (1¢) at every confident-tail price.** Because settlement is
binary 0/100¢, any positive raw fee rounds to 1¢. The maker discount only helps at
sub-0.90 prices where the raw fee would otherwise round to 2¢ — i.e. where the
strategy has NO edge. (Sources differ on whether tiny maker fees "round to ~$0";
the canonical formula and Kalshi's per-contract round-UP convention give 1¢. We
also report a `maker0` column assuming the optimistic floor-to-0 — it still doesn't
produce bankable positive EV; see §3.)

---

## 2. Maker fill realism + adverse selection

Setup: at τ s-to-close, model fair = `estimate()` (de-biased Binance). Rest a BUY
on the model-favored side at price `e`. It fills iff the favored side actually
**trades at ≤ e** during the remaining window (from `trades.parquet`, the 2,495-window
sample). Measured per (τ, |m̂| threshold, entry e): fill rate, win-rate-conditional-
on-fill, unconditional win rate, and net EV after the real maker fee.

**Adverse selection is real and monotone.** A resting bid fills only when the market
trades *down to* your level — which is exactly when the outcome is drifting against
you. Example, τ=120, |m̂|>$50:

| entry | fill % | n_fill | win (uncond) | win \| fill | net EV (maker) |
|-------|--------|--------|--------------|-------------|----------------|
| 0.99  | 48.8%  | 736    | 98.94%       | 97.83%      | **−2.17¢**     |
| 0.98  | 30.7%  | 463    | 98.94%       | 96.54%      | −2.46¢         |
| 0.97  | 21.8%  | 329    | 98.94%       | 95.14%      | −2.86¢         |
| 0.95  | 13.7%  | 206    | 98.94%       | 92.23%      | −3.77¢         |
| 0.90  |  8.0%  | 120    | 98.94%       | 86.67%      | −4.33¢         |

The **deeper / cheaper the bid, the more it fills but the lower the conditional win
rate** — the signature of adverse selection. Magnitude across all cells with
n_fill≥100: **win_uncond − win_fill ≈ 3.3pp mean, 2.2pp median, up to 12.3pp.** So
the model's ~99.5% unconditional accuracy degrades to ~92–98% on the subset that
actually fills, and the cheap deep fills (where the gross gap would be largest) are
precisely the ones that most often lose.

---

## 3. Verdict — is there ANY positive-EV maker parameterization with volume?

**No.** Scanning τ∈{30,45,60,90,120}, |m̂|>{50,75,100,150}, entry∈{0.90…0.99}:

- **Cells with meaningful fill volume (n_fill ≥ 100): max net maker EV = 0.0¢** —
  and that single cell (τ=45, |m̂|>50, e=0.99, n=101) is **exactly break-even**:
  win|fill = 100% → +1¢ gross − 1¢ fee = 0. Every other volume cell is **negative**
  (−1.1¢ to −4.3¢/contract). The bulk of fillable volume sits at −1.2 to −2.5¢.
- The only strictly-positive cells are **n_fill = 1–24** (entry ≤0.97, |m̂|>150):
  fill rates 0.1–5%, i.e. ~1–24 contracts across ~500 windows. Statistically empty,
  not a strategy.
- **maker == taker EV in every confident cell** (both pay 1¢) — the "maker discount"
  buys nothing at the tail, exactly because of round-up.
- **Even under the optimistic `maker0` assumption** (maker fee floored to 0¢ at the
  tail): best volume cell rises to only **+1.0¢** (τ=45/|m̂|>50/e=0.99, n=101, win
  100%) and **+0.2¢** (τ=90/|m̂|>100/e=0.99, n=126). These hang entirely on a
  near-100% conditional win rate over ~100 fills at e=0.99, where one extra flip
  flips the sign — fragile, low-volume, and contingent on a fee treatment Kalshi's
  own round-up convention contradicts. Per the mandate, near-zero/fragile = NO.

### Why maker fails (one line)
You only get filled when the price comes to you (adverse selection drops win|fill
from ~99.5% to ~92–98%), and the round-UP-to-cent fee makes the maker fee identical
to the taker fee (1¢) at every confident price — so the maker path inherits the same
killer as the taker path with worse selection on the fills. The +1¢ gross at e=0.99
is exactly eaten by the 1¢ fee; deeper/cheaper bids that would widen the gross gap
fill rarely and lose more often.

**Bottom line: best-case net ≈ 0¢/contract at the only cell with real volume; the
realistic regime is −1 to −2.5¢/contract. Maker near-lock is dead, same as taker.**
