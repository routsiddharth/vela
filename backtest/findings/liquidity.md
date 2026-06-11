# Cheap-fill (liquidity) strategy — findings

Date: 2026-06-09. Question: the AVERAGE confident-tail entry is 0.985–0.999 (no
edge after fees), but does the *tail of the distribution* hide a
systematically-catchable subset where the winning side trades CHEAP? **Answer:
YES, narrowly — at τ=45 only, with small but real capacity.**

All signals causal: model = `btc_lib.estimate(piv, tau, causal_bias)`; "winning
side" = sign of `mhat_tau`; a "takeable cheap fill" = a trade where the TAKER
lifted the model-predicted winning side at price ≤ X within (0, τ] sec-to-close
(a resting offer existed and cleared, so we could have joined it). Realized
outcomes used only for accounting, never for selection.

Scripts: `backtest/analysis/liq_common.py` (infra),
`liq_distribution.py`, `liq_cheap_winrate.py`, `liq_tau45.py`, `liq_why.py`,
`liq_oos.py`, `liq_robust.py`.

## 1. Full distribution of the winning side's traded prices (confident windows)

Confident = `|mhat_tau| ≥ thr`. Trade sample covers 2,497/6,283 windows (39.7%),
final 180s. Size-weighted mean fill price of the predicted winner:

| τ | locked settle secs | szw-mean price | min | % of size ≤0.97 | % of size ≤0.95 |
|---|---|---|---|---|---|
| 15 | 46 | 0.998 | 0.963 | 1.9% | 0% |
| 30 | 31 | 0.993 | 0.010 | 3.5% | 1.1% |
| 45 | 16 | 0.997 | 0.912 | 1.5% | 0.5% |
| 60 | 1 | 0.948 | 0.001 | 12.5% | 10.2% |

Cheap fills DO exist (most volume at τ=60). The mean hides a real left tail.

## 2. The skeptic's test — did the cheap winning side actually WIN?

Conditioning the confident-window cheap fills on the realized outcome is decisive:

| τ | win-rate of fills ≤0.97 | win-rate ≤0.95 | verdict |
|---|---|---|---|
| 15 | 100% (n=36) | — | clean but ~no cheap supply |
| 30 | **39%** | **0.5%** | cheap = informed reversal → DEAD |
| 45 | **100%** (n=357) | **100%** (n=96) | clean → tradeable |
| 60 | 62% | 52% | adverse-selected → DEAD |

The cheap availability at τ=60 (the bulk of it) is a MIRAGE: with only 1 locked
settlement second, a confident `mhat` is a pure martingale forecast, so a cheap
offer on the "winning" side is an *informed reversal* (spot crossing back through
strike). Those fills lose. Same at τ=30. **Only τ=45 has cheap fills that win**,
because 16 of 60 settlement seconds are already locked (outcome largely pinned)
yet spot still wobbles, so panic/forced sellers dump a side that has actually
already won. The locked-margin filter does NOT separate winners (τ=30 losers had
+40 locked margin); the discriminator that works is the structural **count of
locked settlement seconds (τ ≤ 45)**.

## 3. Backtest — net EV after the quadratic fee

Fee = `round_up_cent(0.07·p·(1−p))`, min 1¢ (`liq_common.fee_cents`). Payoff per
contract (cents): win → `(100−100p) − fee`, lose → `−100p − fee`.

**τ=45, thr=40 (full sample), buy winning side at offered p ≤ X:**

| X | size (sampled) | net EV (size-w) | win-rate | cheap windows |
|---|---|---|---|---|
| ≤0.99 | 354,952 | **+5.99¢** | 1.000 | 77 |
| ≤0.98 | 218,703 | **+9.59¢** | 1.000 | 33 |
| ≤0.97 | 167,362 | **+12.13¢** | 1.000 | 22 |
| ≤0.95 | 105,263 | **+17.91¢** | 1.000 | 11 |

Monthly (Apr/May/Jun) stability at τ=45 thr=40: 0 losers in every month, EV
+1.3→+16¢. Fills are genuinely takeable (taker lifted our side): median fill 25,
mean 70, p90 175, max 6,000 contracts.

## 4. Out-of-sample split (first half dates → params, second half → validate)

Split at the median close date (2026-05-06); 33 test days.

**Naive optimizer (all τ, maximize EV·size) picks the τ=60 TRAP:**
in-sample τ=60/thr=60/X≤0.99 shows +11¢ @ 100% win → **OOS collapses to −6.8¢,
66% win, −$21.8k.** This is the headline cautionary result: unconstrained tuning
over-fits the τ=60 martingale mirage.

**Constrained selection (require ≥15 locked settlement secs → τ ∈ {15,30,45}):**
- TRAIN picks **τ=45, thr=40, X≤0.99** (+1.3¢, 100% win in-sample).
- **OOS: +7.25¢/contract, win-rate 1.0000 (0 losers / 44 windows), $20,256 on
  sampled fills.**

Capacity (full universe, scaling sampled size by 1/0.397 coverage):
- τ=45 X≤0.99: ≈13,500 contracts/day, ≈**$810/day** (full sample) / **$1,550/day**
  (OOS half — higher because cheap supply clustered in the later period).
- τ=45 X≤0.97: ≈6,400 contracts/day, +12.1¢, ≈$774/day.

## 4. Verdict — YES, narrowly

**There is a cheap-fill rule with net positive EV after fees and non-trivial
(but modest) capacity, and it survives OOS.**

- **Best rule:** at τ=45s, when `|mhat_45| ≥ 40` (model confident, ≥16 settlement
  seconds locked), take any offer on the model-predicted winning side at price
  **≤ 0.99** (tighten to ≤0.97 for higher per-contract EV at lower volume).
- **Net per contract:** +6¢ (X≤0.99) to +12¢ (X≤0.97), OOS-confirmed +7.25¢.
- **Win rate:** 100% on cheap fills, 0 losers across 121 cheap windows
  (train+test) — because the τ filter excludes the informed-reversal regimes.
- **Capacity:** ~6k–13k contracts/day full-universe, ~$0.8k–$1.5k/day. Cheap
  supply is bursty: 22–77 cheap windows over 66 days; you can't size up at will.

**Caveats / why this is "narrow," not a free lunch:**
- Cheap supply is RARE and bursty; per-window median fill is ~25 contracts.
  Real-world fills compete with other takers for the same resting offers.
- The win-rate is 100% on a *small* cheap-fill sample (low-double-digit windows);
  the structural reason (locked settlement seconds) makes it credible, but a
  single bad fill at p=0.90 costs ~90¢, so position sizing must respect that the
  tail is thin.
- **τ=60 and τ=30 are traps — do NOT trade cheap fills there.** Their cheap
  availability is large but adversely selected (negative OOS EV). The edge lives
  *only* where enough settlement seconds are already locked.
- This refines the prior finding: the taker path is dead *on average*, but a
  causally-filtered cheap-fill subset at τ=45 is net positive after the fee.
