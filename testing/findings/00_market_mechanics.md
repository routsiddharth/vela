# Kalshi short-dated BTC markets — observed mechanics (ground truth)

Probed live against `https://api.elections.kalshi.com/trade-api/v2` on 2026-06-08.

## The actual market: `KXBTC15M` — "Bitcoin price up down"
- **15-minute windows** (`frequency: fifteen_min`). Each *event* opens and closes
  exactly 15 min apart (`open_time` → `close_time`). Example:
  `KXBTC15M-26JUN081515-15`, open 19:00:00Z, close 19:15:00Z.
- **Contract**: `strike_type = greater_or_equal`, `floor_strike` = a target set ≈ at
  the spot price when the window opens (e.g. target `$63,440.24`). So each contract
  is effectively **"will BTC be ≥ its opening price after 15 minutes"** — a directional
  up/down binary, struck **at-the-money at open**.
- **There is no 5-minute BTC market.** Shortest standard BTC cadence is 15-min. The
  tweet's "5-min" is imprecise (or conflates with another asset). Also live: hourly
  `KXBTCD` (BTC ≥ strike at a fixed hour), daily ranges `KXBTC`, plus `KXETHD`,
  `KXBNB15M`, `KXNEAR15M`, etc. (15-min cadence exists across several coins).

## Settlement — a 1-minute TWAP, not the close print  ⭐ critical
From series `product_metadata`:
> The price used to determine this market is based on **CF Benchmarks' Real-Time
> Index (RTI)**. At the last minute before expiration, **60 RTI prices are collected.
> The official and final value is the average of these prices.**

⇒ Settlement = **average of 60 samples over the final 60 seconds** (≈1 Hz TWAP).
Not the instantaneous expiry print. This is the single most important microstructure
fact (see `findings/04_twap_endgame.md`).

## Fees — quadratic
- Series meta: `fee_type: quadratic`, `fee_multiplier: 1`.
- Kalshi standard formula: **fee per contract = round_up_to_cent(0.07 × P × (1−P))**,
  charged per fill. Peaks at P=0.50 → 0.07×0.25 = **1.75¢/contract**; at P=0.75 →
  0.07×0.1875 = **1.31¢/contract**.
- Whether *makers* pay this (vs takers only / rebates / MM exemptions) is the hinge of
  the whole economic case — flagged for the deep-research pass.

## Order book — bids-only, both sides
- Kalshi posts `yes_dollars` (YES bids) and `no_dollars` (NO bids). There are no ask
  queues; **`yes_ask = 1 − best_no_bid`**.
- **Live snapshot of the active window** (`KXBTC15M-26JUN081515-15`):
  - best YES bid **0.75**, best NO bid **0.24** ⇒ implied **YES 0.75 / 0.76, spread = 1¢**.
  - resting depth ≈ **29,400 YES** / **34,450 NO** contracts across 20 levels.
- **Two-sided "lock"**: buy YES@0.75 + buy NO@0.24 = **0.99 cost → 1¢ gross/pair**.
  Quadratic fee ≈ 1.31¢ + 1.28¢ = **~2.6¢ round-trip ⇒ the naive spread lock is
  fee-NEGATIVE** unless makers are fee-exempt/rebated. This single number breaks the
  "market-making is the riskless floor" framing as stated.

## What this implies for the strategy claims
1. "Run both sides of the same window" = post YES bid + NO bid (= YES ask). Real, but
   the 1¢ spread cannot pay the quadratic fee ⇒ not a riskless floor. Needs maker
   economics or directional skew to be positive.
2. "Probability model" for an ATM 15-min binary ≈ a **live-spot repricer** (fair ≈
   Φ(d2) with ATM strike), not a BTC-direction *forecaster*. Edge = being faster /
   more accurate than the Kalshi book = **latency arb vs spot**, not prediction.
3. "21¢ longshots → 99¢ near-locks" — the 99¢ end is explained by **TWAP endgame
   near-certainty**, not foresight. The 21¢ end is the same trade from the other side.
