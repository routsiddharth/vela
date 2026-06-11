# BTC panic-fade — frequency optimization

Objective: **maximize traded windows (fill frequency)** subject to
`oos_usd_day >= 0.22` (don't drop below current profit) and `min_month >= 0`
(positive every month). Same fixed strategy, 2500-window / 67-day BTC backtest,
maker fees, $50 bankroll, NOTIONAL fixed at 5.0.
(traded windows/day ≈ windows / 0.396 / 67.)

## Recommended max-frequency config

```
P_SIDE_MIN = 0.817
FLOOR      = 0.45
CAP        = 0.99
SEC_LO     = 1
SEC_HI     = 45
TAU        = 45
NOTIONAL   = 5.0
```

| metric        | recommended (P=0.817) | current (P=0.99) |
|---------------|-----------------------|------------------|
| windows       | 280                   | 166              |
| windows/day   | ~10.6                 | ~6.3             |
| usd_day       | 1.233                 | 0.428            |
| oos_usd_day   | 0.504                 | 0.217            |
| is_usd_day    | 0.729                 | 0.210            |
| winpct        | 94.8%                 | 100.0%           |
| min_month     | +0.134                | +0.059           |
| worst trade   | -5.272                | +0.040           |

This trades **~69% more often** (280 vs 166 windows, ~10.6 vs ~6.3/day) while
the **OOS profit more than doubles** (0.504 vs 0.217) and every month stays
positive (min_month +0.134 vs +0.059). The headline cost is winpct: 94.8% vs a
clean 100% — the loose gate admits some flips, so individual losing windows now
exist (worst trade -5.27 vs the current strategy never losing a window).

## What moves frequency (and what doesn't)

- **P_SIDE_MIN is the only real frequency lever.** Dropping it from 0.99 → ~0.82
  is what adds fills (166 → ~280 windows) and, surprisingly, *raises* OOS over
  most of that range (the high p_side gate was leaving good fades on the table).
- **FLOOR / CAP barely affect frequency** at the loose gate — the band is wide
  open and almost nothing trades near the floor. (FLOOR 0.45→0.75 leaves windows
  ~unchanged at 275–279 and actually *improves* OOS, because cheap winners are
  the flip-prone ones. CAP is where the fills live: CAP 0.90→0.99 takes windows
  72 → 279.) FLOOR=0.45, CAP=0.99 maximizes raw fill count.
- **Raising TAU / widening SEC_HI HURTS** — both frequency and OOS. TAU 45→60
  drops to 72 windows and OOS goes negative; SEC_HI within TAU=45 is best left at
  45. SEC_LO=1 is best. So the time window is already at its sweet spot at the
  current TAU=45 — do not widen it.

## Tradeoff curve (FLOOR=0.45, CAP=0.99, SEC_LO=1, SEC_HI=45, TAU=45)

Frequency rises as P_SIDE_MIN drops; OOS climbs, peaks, then falls off a cliff.

| P_SIDE_MIN | windows | /day | usd_day | oos_usd_day | winpct | min_month |
|-----------:|--------:|-----:|--------:|------------:|-------:|----------:|
| 0.99 (cur) | 166     | 6.3  | 0.428   | 0.217       | 100.0  | +0.059    |
| 0.90       | 249     | 9.4  | 0.456   | 0.116       | 96.9   | -0.033 ✗  |
| 0.84       | 274     | 10.3 | 1.635   | 0.628       | 96.6   | +0.134    |
| 0.83       | 277     | 10.4 | 1.549   | 0.694       | 96.0   | +0.134    |
| 0.82       | 279     | 10.5 | 1.410   | 0.504       | 95.3   | +0.134    |
| **0.817**  | **280** | 10.6 | 1.233   | **0.504**   | 94.8   | +0.134    |
| 0.816      | 281     | 10.6 | 1.052   | 0.323       | 94.4   | +0.134    |
| 0.815      | 282     | 10.6 | 1.116   | 0.323       | 94.4   | +0.134    |
| 0.814      | 284     | 10.7 | 0.739   | -0.054 ✗    | 93.3   | -0.206 ✗  |
| 0.81       | 287     | 10.8 | 0.979   | 0.062 ✗     | 93.4   | -0.012 ✗  |

Note the non-monotonic dip at P≈0.90 (OOS 0.116, min_month -0.033) — that pocket
fails the constraints even though looser settings pass. The frontier is **not**
a clean slope; the safe, profitable plateau is roughly P_SIDE_MIN ∈ [0.82, 0.84].

## The cliff & why I didn't go looser

- **Absolute loosest point that still clears both constraints: P=0.815**
  (282 windows, OOS 0.323, min_month +0.134) — but its OOS margin over the 0.22
  floor is thin and it sits one tick from the cliff.
- **P=0.814 falls off the cliff:** OOS -0.054, min_month -0.206. One tick of
  gate loosening flips it from healthy to losing — winpct 94.4 → 93.3.
- I recommend **P=0.817** over 0.815/0.816 because it costs only **2 windows**
  (280 vs 282) yet buys a much safer OOS (0.504 vs 0.323) and keeps the same
  min_month. It's the most action that still pays *with margin*, rather than the
  most action that barely pays.

## Honest cost summary

- Trades **~69% more** (166 → 280 windows; ~6.3 → ~10.6 fills/day).
- OOS profit **more than doubles** (0.217 → 0.504); in-sample 3.5x.
- The price: it's no longer a perfect-record strategy — winpct drops 100% →
  94.8%, and you take real losing windows (worst -$5.27 vs the current strategy
  never losing). With NOTIONAL=$5 on a $50 bankroll, a -$5.27 window is ~10% of
  bankroll in one window — position sizing / a per-window stop is worth a look
  before going live.
- The edge is fragile near the boundary: do not push P_SIDE_MIN below ~0.82, do
  not widen the time window (TAU/SEC_HI), and re-validate if the data window
  changes — the cliff at 0.814 means small drift could turn the loosest settings
  unprofitable.
