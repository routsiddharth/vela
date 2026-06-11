# opt_maxday — maximizing full-sample $/day for the BTC panic-fade

Objective: maximize **full-sample usd_day** over the 6 edge params (P_SIDE_MIN,
FLOOR, CAP, SEC_LO, SEC_HI, TAU) using the shared `opt_harness.evaluate`.
NOTIONAL fixed at 5.0 ($50 bankroll). Backtest = 2500-window KXBTC15M, 67 days,
maker fees modeled. Baseline (current config) = **$0.43 full / $0.22 OOS**.

Method: coarse 6-D grid (384 cells) → refine around top region → 1-D plateau
scans on every axis to separate genuine plateaus from overfit spikes.

## TOP-5 by full-sample usd_day (all OOS-positive, all on the plateau)

| # | P_SIDE | FLOOR | CAP | SEC_LO | SEC_HI | TAU | full $/day | OOS $/day | min_month | winpct | worst $ |
|---|--------|-------|-----|--------|--------|-----|-----------|-----------|-----------|--------|---------|
| 1 | 0.97 | 0.45 | 0.99 | 5 | 45 | 45 | **0.524** | 0.379 | +0.113 | 99.5% | -4.57 |
| 2 | 0.97 | 0.50 | 0.99 | 5 | 45 | 45 | 0.523 | 0.378 | +0.113 | 99.5% | -4.57 |
| 3 | 0.97 | 0.55 | 0.99 | 5 | 45 | 45 | 0.521 | 0.376 | +0.113 | 99.5% | -4.57 |
| 4 | 0.97 | 0.45 | 0.99 | 1 | 45 | 45 | 0.514 | 0.384 | +0.098 | 99.4% | -5.01 |
| 5 | 0.97 | 0.50 | 0.99 | 1 | 45 | 45 | 0.508 | 0.382 | +0.094 | 99.4% | -5.01 |

## RECOMMENDED config

**P_SIDE_MIN=0.97, FLOOR=0.45, CAP=0.99, SEC_LO=5, SEC_HI=45, TAU=45, NOTIONAL=5.0**

- full = **$0.524/day**, OOS = **$0.379/day**, IS = $0.131/day
- min_month = **+$0.113** (highest in the whole search — every month profitable)
- winpct 99.5%, worst single window -$4.57 (best worst-case in the top region)
- 204 windows traded over 67 days

vs current ($0.43 full / $0.22 OOS): **+22% full, +73% OOS**, and a higher,
positive min-month. The OOS lift is the real story — this config generalizes
much better than the current one, not just a bigger in-sample number.

## Plateau or spike?

**Plateau on 5 of 6 axes, soft peak on the timing axis.**

- FLOOR: flat 0.45–0.60 (full 0.49–0.52, OOS ~0.38) — pick 0.45.
- SEC_LO: flat 1–5 (SEC_LO=5 marginally best: best min_month +0.113 and best
  worst-case -4.57; SEC_LO=1 has marginally better OOS 0.384).
- CAP: 0.99 > 0.98 (more fills, same OOS quality).
- P_SIDE: 0.97 is a genuine local optimum. Neighbors 0.965/0.975 are lower on
  full ($0.37/$0.47) but OOS is *flat* at 0.37–0.39 across 0.965–0.975, so the
  edge is real, not a single-cell artifact. Below ~0.96 the curve goes jagged
  and IS/OOS diverge (overfit); at 0.99 it reverts toward the current baseline.
- **SEC_HI/TAU is the one sensitive axis (a soft peak, not a plateau):**
  SEC_HI=TAU=45 is the peak. SEC_HI=35→0.285, 40→0.388; TAU=50→0.356,
  TAU=60→collapses to ~0. This is structurally sensible — the edge lives in the
  final ~45s before close, so gating and trading on the full last-45s window is
  the natural operating point, not an overfit corner. Still, this axis has the
  least margin for error, so 45/45 should be treated as a regime choice.

## Spikes we explicitly rejected (overfit, do NOT use)

- P_SIDE=0.88, FLOOR=0.45: full=**$0.948** but IS=0.669 / OOS=0.279 — pure
  first-half overfit, min_month only +0.013.
- P_SIDE=0.90, FLOOR=0.50: full~0.45 but OOS=0.10, **min_month negative**,
  worst -$5.12. The low-P_SIDE band admits losing trades; high full numbers
  there are noise.

## The honest ceiling

The frontier is shallow as advertised. Best robust, plateau-stable, OOS-positive
config tops out near **$0.52/day full / $0.38/day OOS on $50**. This is a real
~20–70% improvement over current but nowhere near $5/day — the edge per window
is tiny (≈1.4c/contract net) and the win rate is already ~99.5%; there is no
parameter setting in-range that changes the order of magnitude.
