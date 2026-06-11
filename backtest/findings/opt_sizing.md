# Sizing & Decision-Timing optimization — BTC panic-fade

Evaluator: `backtest.analysis.opt_harness.evaluate` (2500-window, 67-day BTC
KXBTC15M sample, maker fees, OOS = H2). Bank = $50. `worst` = worst single-window
$ pnl = the risk metric. Held edge params near the robust point; tested the
current tight gate AND a slightly looser one (P_SIDE_MIN=0.97).

## SIZING (NOTIONAL = PORTFOLIO_FRACTION * $50; current 0.10 -> $5)

usd_day scales ~LINEARLY with NOTIONAL. This is **leverage, not alpha** — the
per-$1 edge is constant (~$0.097/day per $1 of NOTIONAL on the tight gate). The
only thing sizing buys you is a bigger bet on the same edge, and `worst` /
ruin-risk scale right along with it.

### Tight gate (P_SIDE_MIN=0.99, FLOOR=0.55, CAP=0.99, SEC=[1,45], TAU=45)
| NOTIONAL | usd/day | OOS | win% | worst$ | bank-at-risk% |
|---:|---:|---:|---:|---:|---:|
| 2  | 0.13 | 0.07 | 100.00 | +0.01 | 0.0% |
| 5  | 0.43 | 0.22 | 100.00 | +0.04 | 0.1% |
| 10 | 0.92 | 0.47 | 100.00 | +0.09 | 0.2% |
| 15 | 1.41 | 0.71 | 100.00 | +0.14 | 0.3% |
| 20 | 1.91 | 0.96 | 100.00 | +0.19 | 0.4% |
| 30 | 2.91 | 1.47 | 100.00 | +0.27 | 0.5% |
| 50 | 4.89 | 2.47 | 100.00 | +0.27 | 0.5% |

At the tight gate there are **zero in-sample flips** at TAU=45, so `worst` is
just the fee on a winning high-price fill (a positive/near-zero pnl), and it
plateaus at +$0.27 once available print size caps contracts (NOTIONAL>=30 stops
buying more qty — the edge stops scaling cleanly past ~$30).

CAVEAT: 100% win is IN-SAMPLE. Assume the true flip rate is small but NONZERO. A
single flip costs ~NOTIONAL$ (qty ≈ NOTIONAL/px, loss ≈ qty*px ≈ NOTIONAL). So
the realistic worst-case single-window loss is ≈ -NOTIONAL$, regardless of the
benign in-sample `worst`. Size as if one window can lose its full NOTIONAL.

### Loose gate (P_SIDE_MIN=0.97) — flips are REAL in-sample
| NOTIONAL | usd/day | OOS | win% | worst$ | bank-at-risk% |
|---:|---:|---:|---:|---:|---:|
| 2  | 0.17 | 0.13 | 99.52 |  -1.80 |  3.6% |
| 5  | 0.50 | 0.38 | 99.43 |  -5.38 | 10.8% |
| 10 | 1.13 | 0.82 | 99.48 |  -9.84 | 19.7% |
| 15 | 1.72 | 1.24 | 99.46 | -15.20 | 30.4% |
| 20 | 2.34 | 1.66 | 99.48 | -19.68 | 39.4% |
| 30 | 3.53 | 2.53 | 99.47 | -30.41 | 60.8% |
| 50 | 5.95 | 4.23 | 99.48 | -50.08 | 100.2% |

The loose gate earns only ~15% more $/day but makes the flip risk explicit and
linear in NOTIONAL: at $50 a single flip wipes the entire $50 bank (100% at
risk). This is the honest picture the tight gate hides — `worst ≈ -NOTIONAL`.

## TIMING (TAU, with SEC_HI=TAU), NOTIONAL=5

| TAU | usd/day (tight) | win% | worst$ || usd/day (loose) | win% | worst$ |
|---:|---:|---:|---:|--|---:|---:|---:|
| 30 | -0.09 | 97.93 | -5.32 || 0.13 | 98.26 | -5.32 |
| 45 |  0.43 |100.00 | +0.04 || 0.50 | 99.43 | -5.38 |
| 60 |  0.18 | 99.15 | -4.94 || 0.32 | 98.78 | -5.13 |
| 75 |  0.09 | 99.08 | -4.59 || -0.16| 97.05 | -5.37 |
| 90 |  0.03 | 98.79 | -5.20 || 0.29 | 99.07 | -5.20 |

**TAU=45 is a genuine, unique sweet spot.** It is the ONLY TAU at which the tight
gate produces 100% in-sample win and a positive `worst` (no flips). The "earlier
TAU auto-tightens to safer" intuition does NOT hold here — TAU=30 actually goes
NEGATIVE with flips (97.9% win, -$5.32 worst), and every TAU≠45 introduces
flips (~-$5 worst = a full-NOTIONAL loss) and lower $/day. Later TAUs (60/75/90)
shrink the fill window (fewer windows: 142/108/115 vs 166) and bleed edge. Do not
move off TAU=45.

## RECOMMENDATIONS

**Prudent**: tight gate (P_SIDE_MIN=0.99), **TAU=45**, **NOTIONAL=$5–10**.
- Yields ~$0.43–0.92/day. Realistic worst-case single window ≈ -$5 to -$10
  (one assumed-rare flip) = 10–20% of a $50 bank. In-sample worst is +$0.04–0.09.
- This is the only configuration where the worst-case flip stays under ~20% of
  bankroll. Stay at the current $5 unless you accept that doubling $/day means
  one bad window can take 20% of the account.

**Aggressive**: tight gate, **TAU=45**, **NOTIONAL=$15** (cap here, not higher).
- Yields ~$1.41/day; at $15 the edge still scales near-linearly (print size
  starts capping past ~$30, so going bigger buys less alpha and more ruin).
  Realistic worst-case flip ≈ -$15 = ~30% of a $50 bank — about the prudent
  ceiling for a single window. Going to $30–50 only inflates $/day via leverage
  while a single flip risks 60–100% of the bank: not survivable, do not.

**Do NOT** loosen to P_SIDE_MIN=0.97 for the sizing: it adds only ~15% $/day but
turns the in-sample flip risk on (worst ≈ -NOTIONAL at every size). The extra
$/day is not worth converting `worst` from +$0.04 to -$5.38 at NOTIONAL=5.

## Bottom line on the $5/day target
$5/day on the tight gate needs NOTIONAL ≈ $50 (4.89 $/day), but at $50 a single
(assumed-rare) flip loses ≈ the full $50 bank. There is no sizing on a $50 bank
that earns $5/day without a single bad window being able to blow the account.
$/day is leverage; the alpha is fixed at ~$0.097/day per $1 risked. Grow the bank
first; don't chase $5/day by sizing into ruin.
