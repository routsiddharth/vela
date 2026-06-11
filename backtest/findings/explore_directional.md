# Directional prediction from the underlying — KXBTC15M (15-min up/down)

**Reproduce:** `python backtest/analysis/explore_directional.py`
(activate `../ingest/venv`). Pulls full-range Binance 1m closes
(`data/binance_1m_full.parquet`, cached) and uses real Kalshi open-time prices
(`data/kalshi_open_candles.parquet`, fetched via REST candlesticks).

## Hypothesis
The 15-min up/down outcome is predictable from BTC's recent price action better
than the ~0.50 the market prices at open. Enter EARLY (at/near open) on the
predicted side, hold to settle.

## Data & method
- **6308 KXBTC15M windows, 68 days** (2026-04-02 → 2026-06-09). Strike[i] =
  settlement[i-1] (struck ATM at open); outcome YES iff settle ≥ strike;
  base YES rate 0.497.
- **Outcome label** comes from the REAL `markets.parquet` (`margin`/`result`).
  Binance is used ONLY to build the entry signal, never the label.
- **Full-window BTC** pulled from Binance 1m closes (the parquet only had the
  final 300s); 100% coverage at every window open. Binance↔RTI bias is removed
  **causally**: median(b_close − true_settle) over the prior 96 windows.
- **Real entry price**: Kalshi `candlesticks` (period_interval=1) for the most
  recent 999 windows give yes_bid/yes_ask at 60s/120s/180s into the window —
  i.e. the actual price you'd pay near open. (Trades.parquet only covers the
  final 180s, useless for an open entry.)
- Fees modeled exactly: taker = ceil_cent(0.07·p(1−p)), min $0.01.

## What the signal IS (and it's real)
Two equivalent framings of the only signal that works:
- **1-minute momentum at open** (`ret_1m`): hit **53.1%** (n=6090). A placebo
  using the *prior* window's ret_1m vs this outcome gives exactly 49.9% — so the
  53% is genuine, not a base-rate artifact.
- **De-biased "where you start" (open price − strike)**: when BTC is already
  clearly above/below the strike at open, it tends to stay there:

  | cell | n | directional hit |
  |---|---|---|
  | all | 6288 | 52.5% |
  | top 50% by \|open−K\| | 3145 | 55.3% |
  | top 19% (\|open−K\|≥$24) | 1258 | 59.4% |
  | top 9% (\|open−K\|≥$34) | 629 | 60.1% |

The directional prediction is genuinely informative — up to **60% hit** in the
big-displacement cells. Momentum works; mean-reversion does NOT (REV is the
losing side of the same coin everywhere).

## Why it does NOT make money — the decisive test
The edge evaporates because **the Kalshi market is efficient at the open.** Using
REAL entry prices (enter 60s into the window, pay the ask, hold to settle):

| \|sig\| pct | n | hit | avg price paid | pnl/ct after fee | t-stat | $/day @ $50 |
|---|---|---|---|---|---|---|
| all | 999 | 61.8% | 0.611 | **−$0.013** | −0.86 | −$94 |
| top 50% | 501 | 65.9% | 0.665 | −$0.026 | −1.25 | −$87 |
| top 30% | 300 | 69.7% | 0.695 | −$0.018 | −0.69 | −$35 |
| top 20% | 200 | 73.0% | 0.714 | −$0.004 | −0.12 | −$4 |
| top 15% | 150 | 78.0% | 0.727 | +$0.034 | +1.02 | +$31 |
| top 10% | 100 | 83.0% | 0.752 | +$0.059 | +1.59 | +$35 |
| top 5% | 50 | 88.0% | 0.784 | +$0.079 | +1.70 | +$22 |

The **price paid tracks the hit rate almost one-for-one** (0.611 price vs 0.618
hit; 0.752 vs 0.830). The market has already moved the line to fair within 60
seconds. Independent confirmation from the final-180s trades: in every
open-displacement bucket the late market price ≈ the realized yes-rate (e.g.
+$170 displacement → 57.6% outcome vs 57.1% market price).

**Statistically: |t-stat| < 2 in every cell.** The "positive" top-10% cell
(+$0.059/ct, ~$35/day naive) is **n=100 over 11 days at t≈1.6 — not
significant**, and it's a deep-ITM trade (avg price 0.75) where the +$0.059 is a
thin sliver above the 0.75+fee break-even that rests entirely on the ask
occasionally lagging true prob by 1–2c. First-half vs second-half of that cell:
+$0.077 vs +$0.042 — already decaying. Spread is only ~1.1c, so there is no room
for it to be robust.

## After-fee math, plainly
- To break even buying at price *p* you need hit ≥ p + ceil_cent(0.07·p(1−p)).
  At p=0.50 that's 52.0%; at p=0.75 it's ~76.3%.
- The signal delivers hit ≈ p (the market's fair price) ± noise. So expected
  edge ≈ −fee = **−1 to −2 cents per contract, guaranteed.** Over ~90 trades/day
  that's a steady bleed, not a profit.
- The only world where this prints money is **"market sits at 0.50 at open while
  BTC is visibly $30 off the strike."** The real candlesticks show that world
  does not exist — the market is already at 0.61–0.78 in those cells by 60s in.

## Capacity
Moot — there is no positive expectancy to scale. Even the illusory top-10% cell
is ~9 trades/day; KXBTC15M open-side depth is a few hundred dollars, so capacity
would be low regardless.

## Verdict
Directional prediction from the underlying is **real but un-tradeable**: the
Kalshi 15-min market prices the BTC displacement to fair within the first minute,
and after paying the ask + taker fees the strategy loses ~1–2¢/contract. It does
**not** reach $5/day at any bankroll on a risk-adjusted basis. The one
marginally-positive cell is small-sample noise (t≈1.6) on a deep-ITM trade with
no spread cushion — do not deploy.
