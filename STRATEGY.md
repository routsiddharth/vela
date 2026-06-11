# Strategy — the endgame "already-decided" trade

*High-level direction. The core idea was validated against live data and **corrected** — the
trade is the mirror image of the original write-up. The math, sizing, and thresholds backing
this are in [`backtest/`](backtest/) (verdict: [`backtest/findings/SYNTHESIS.md`](backtest/findings/SYNTHESIS.md)).*

## The one idea
Kalshi's short-dated BTC market asks "will Bitcoin be up or down over this window?" but the
**winner is decided by an *average* of Bitcoin's price over the final 60 seconds of the window,
not the price at the last instant.** An average is sluggish: once ~45 of the 60 one-second
samples are already banked, the remaining 15 can barely move the result. So well before the
clock runs out, the outcome is often **mathematically settled** — even though the market keeps
trading as if it's still live.

That much was confirmed. **What changed is how you capture it.**

## What the data said (and why the trade flipped)
We backtested the original plan — *calmly buy the near-certain side at a small discount* — on
6,308 settled windows. **It loses ~0.5–1¢/contract everywhere.** Reason: by the time the
outcome is obvious, BTC is *visibly* far from the line, so the crowd already prices the winner
at 0.985–0.999. There's no discount left, and Kalshi's quadratic fee rounds up to a full 1¢ at
those extremes. The market is efficient with respect to spot. Taking the near-locked winner is
dead.

But the same data showed a **refinement that does work, with >95% confidence**: don't buy
calmly — **buy from someone panicking.**

The 60-sample average is anchored by the samples already collected, so a late spot wiggle can't
move the settlement much. But naive traders watch *last price*, and when spot lurches the wrong
way in the final minute they **panic and dump the soon-to-win side cheap** — selling for 90¢
something that's locked to pay $1. The average already drowns out that wiggle. **The edge is
fading that panic:** the discount only exists in those brief moments, so you rest a cheap bid
and let the frightened seller hit it. (~78% of the cheap volume is exactly these panic sells.)

> **Analogy.** A game is 49–0 with two minutes left — mathematically over. A nervous fan,
> spooked by one long completion, sells you his winning ticket for 90¢ on the dollar. You know
> the pass doesn't matter. You buy it and collect the full payout.

You are not predicting Bitcoin. You're just better at arithmetic than a scared person staring
at a flickering price.

## The trade, in three rules
1. **Reconstruct the settlement price yourself, for free.** The official settlement reference is
   a blend of big exchanges' prices; the recipe is public and the feeds are free. Build a
   running estimate of the late-window average from them. Binance works as a proxy **but its
   bias drifts** (−$31→+$97 over two months), so apply a **causal trailing-24h de-bias** —
   without it the estimate is wrong; with it the residual is ~$10 std.
2. **Only act when it's truly locked — gate on model *confidence*, not a raw margin.** At ~**45
   seconds before close**, turn the de-biased margin into a probability our side wins,
   `p_side = Φ(|margin| / sd_S)`, where `sd_S` folds in BOTH the diffusion of the still-unlocked
   samples AND the de-bias tracking error. Only trade when **`p_side ≥ 0.99`** (≈ the robust
   ~$40–50 margin gate, but unit-free so it scales across assets). **Skip every close call.**
3. **Only fade *real* panic, as a maker, and size for the flip.** Rest cheap bids on the
   TWAP-favored side, but only fill a print whose price is in **[0.55, 0.97]** — above the floor
   it's a scared seller dumping a winner (panic); *below* it the market is confidently telling
   you you're wrong (adverse selection — that is the trap that loses the whole stake). Fee is the
   **maker** rate (0.0175, rounded per order), not taker. Size each fill by **¼-Kelly hard-capped
   so one window's worst-case loss ≤ 2% of bankroll**, with the flip prob blended 55/45 model/market.

## The correction that flipped it from left- to right-skewed (multi-agent search, 2026-06-09)
The first live cut bled out (one −$5 flip erased 14 pennies-each wins) because the gate was
*decoupled*: it checked `|margin| ≥ $10` and a price cap **separately**, so a cheap print (0.28)
at a thin margin (+$30) cleared it and we bought the side the market was confidently pricing to
lose. Six subagents (fees, tail, sizing, frequency, market-making, a strategy-book mine)
converged on the fix now in `livepaper/`: **(a)** gate on `p_side ≥ 0.99` (removed *every* losing
window in a 2-month backtest — distribution goes right-skewed); **(b)** a `[0.55, 0.97]` price
band (reject adverse-selection prints, keep genuine panic); **(c)** corrected **maker** fees
(~16× lower than the old per-contract model); **(d)** CVaR-capped ¼-Kelly sizing (a model failure
now costs ~2% of bankroll, not 100%); **(e)** more independent series (added KXETHD) for
frequency. Detail + backtests in [`backtest/strategy_search/`](backtest/strategy_search/).

## What the backtest delivered
- Net **+3 to +10¢/contract** (fatter the stricter the price cap).
- **0 losing windows in 2 months.** Positive out-of-sample (train H1 → test H2), positive every
  month (Apr +4.7¢, May +3.1¢, Jun +3.2¢), bootstrap CI excludes zero.
- Cheap offers persist a median **62s** (82% last >15s) — not a latency race; you have time to
  rest and lift.
- The win rate isn't cheap-subset luck: it inherits the τ=45s/$50 lock reliability. **The lock
  sets the win rate; the panic sets the entry price.**

## Where to be careful (directionally)
- **Close calls** are the trap — exactly where an imperfect homemade estimate gets the outcome
  wrong. The whole edge depends on staying away from them. The $50 margin gate is the wall.
- **Sudden jumps** are the tail risk: a near-locked outcome pays pennies, but a rare violent
  move against it costs the whole stake. Survival is about sizing for that flip, not win-rate.
- **Capacity is modest** — a few hundred dollars/day at most, and **bursty** (it clusters on
  panic days, since panic is the raw material). A steady grind, not a money printer.
- **The maker fill rate is unproven.** A backtest can't tell you whether you'll win the queue
  for those cheap orders against other smart buyers, how fast the edge decays as it's exploited,
  or how it holds through a USDT/RTI basis shock. That's the one thing only live data answers.

## How to prove it before risking anything
The lock detection and the edge's *existence* are settled offline. The remaining unknowns are
all about live execution as a maker. So:

**Build order:** ✅ prove the estimate + edge offline (done) → **paper forward-test the live
maker fill rate** (rest cheap bids against the live WS feed, measure actual fills vs the
backtest) → size for the modeled flip → only then risk real money.

The single highest-value next step is that paper forward-tester — it stands between this and a
go.
