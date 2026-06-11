# Robust optimization of the BTC panic-fade strategy

Objective: maximize `oos_usd_day` (held-out 2nd half) s.t. `is_usd_day>0`,
`min_month>0`, `worst>=-2.0` (no catastrophic single window). Shared evaluator
`backtest.analysis.opt_harness.evaluate`. 2500-window / 67-day BTC backtest,
maker fees, $50 bankroll, NOTIONAL=5.0 fixed.

## RECOMMENDED ROBUST CONFIG

    P_SIDE_MIN=0.99  FLOOR=0.45  CAP=0.99  SEC_LO=1  SEC_HI=45  TAU=45  NOTIONAL=5.0

| metric        | value |
|---------------|-------|
| usd_day       | 0.431 |
| oos_usd_day   | 0.221 (PRIMARY) |
| is_usd_day    | 0.210 |
| min_month     | 0.0585 |
| 2026-04       | 0.185 |
| 2026-05       | 0.188 |
| 2026-06       | 0.0585 |
| winpct        | 100.0 |
| worst         | +0.04 |
| windows       | 166 |

All three months positive, IS positive, no losing window. Passes every constraint.

## Why this and not the higher-OOS configs

The feasible region is bounded by **cliffs**, not a smooth frontier. Two axes are
dangerous:

- **P_SIDE_MIN cliff between 0.986 and 0.987.** At >=0.987 winrate is 100% and
  `worst=+0.04`; at <=0.986 a single window flips to a loss and `worst` crashes
  to ~ -5.0 (constraint violated, IS collapses).
- **TAU cliff between 43 and 44.** At >=44, `worst=+0.04`; at <=43 a window flips
  and `worst` ~ -5.4.

Above each cliff, OOS *decreases monotonically* with both P_SIDE_MIN and TAU. So
the raw-OOS maximum sits right on the cliff edge (e.g. P_SIDE_MIN=0.987,
oos=0.246; or P_SIDE_MIN=0.988, oos=0.241). Those are **spikes, not plateaus**:
their `-1`-step neighbors fall off the cliff. They are disqualified for shipping.

P_SIDE_MIN=0.99 / TAU=45 sits with a deliberate **safety buffer** (2 grid-steps
above each cliff) while giving up only ~0.02 OOS vs the cliff-edge configs.

FLOOR was lowered 0.55 -> 0.45: it admits a few more cheap windows (the cliffs are
on P_SIDE_MIN/TAU, not FLOOR), nudging OOS 0.217 -> 0.221 with zero robustness cost.
FLOOR, CAP, SEC_LO, SEC_HI are all flat/benign axes.

## Plateau proof — ±1-step in-range neighbor check

Step grid: P_SIDE_MIN ±0.002, FLOOR ±0.05, CAP ±0.01, SEC_LO ±1, SEC_HI ±1, TAU ±1.
(CAP+1, FLOOR-1, SEC_LO-1 are out of the allowed param range, so skipped.)

| perturb        | value | oos   | is    | min_month | worst | pass |
|----------------|-------|-------|-------|-----------|-------|------|
| P_SIDE_MIN -1  | 0.988 | 0.241 | 0.225 | 0.059     | +0.04 | OK |
| P_SIDE_MIN +1  | 0.992 | 0.200 | 0.190 | 0.059     | +0.04 | OK |
| FLOOR +1       | 0.50  | 0.219 | 0.210 | 0.059     | +0.04 | OK |
| CAP -1         | 0.98  | 0.206 | 0.200 | 0.054     | +0.09 | OK |
| SEC_LO +1      | 2     | 0.215 | 0.211 | 0.054     | +0.04 | OK |
| SEC_HI -1      | 44    | 0.210 | 0.205 | 0.056     | +0.04 | OK |
| TAU -1         | 44    | 0.233 | 0.226 | 0.059     | +0.04 | OK |
| TAU +1         | 46    | 0.206 | 0.195 | 0.059     | +0.04 | OK |

**0 of 8 in-range neighbors fail.** Genuine plateau. Note TAU-1=44 and
P_SIDE_MIN-1=0.988 are still comfortably OK (the cliffs are 2 steps away at 43 and
0.986), which is exactly the buffer we wanted.

For contrast, the higher-OOS P_SIDE_MIN=0.988 candidate FAILS the plateau test:
its P_SIDE_MIN-1 (0.986) and TAU-1 (44) neighbors both crash to worst ~ -5
(2 of its in-range neighbors fail). Rejected.

## Honest comparison vs current

Current = `P_SIDE_MIN=0.99, FLOOR=0.55, CAP=0.99, SEC=[1,45], TAU=45`:
oos 0.217, full 0.428, IS 0.210, min_month 0.0585, worst +0.04, win 100%.

Recommended = same but **FLOOR 0.55 -> 0.45**: oos 0.221, full 0.431, IS 0.210,
min_month 0.0585, worst +0.04, win 100%.

The honest conclusion: **the current config is already at the robust optimum.**
The only safe, non-overfit improvement available is the FLOOR nudge, worth ~+$0.004/day
OOS (+1.6%) — within noise. There is no free lunch deeper in: every materially
higher-OOS config buys it by sitting on a loss-window cliff (worst ~ -5, IS often
<=0, or only one good month), which is precisely the trap to avoid. Ship the
recommended config; treat it as confirmation that the live config is well-tuned, not
as a meaningful edge gain. Frontier remains shallow (~$0.2 OOS / day) as expected.
