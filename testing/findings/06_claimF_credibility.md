# Claim F — Credibility of the headline (adversarial review)

**Claims:** "$1M profit, $10k/day, 5-min BTC markets; I traced his trades using Claude →
backtested on 72M trades; runs both sides, a probability model decides where the real money is."

## Trade attribution feasibility
The `/markets/trades` endpoint returns **anonymous prints** (trade_id, ticker, price, count,
taker_side, timestamp) — **no account id**. You cannot prove a set of fills belongs to one
person/bot from public data. "Traced his trades using Claude" is **not literally possible** —
at best an inference. You *can* infer statistically (clusters of same-size/cadence prints, a
maker consistently on one side, repeated round lots) that *an* automated participant exists.
**Unknowable:** identity, net position, true PnL, fees paid, one bot vs many lookalikes,
cancelled orders, whether one actor is on both sides. "Traced his trades" deserves little weight
— a narrative wrapper on pattern-matching.

## Backtest pitfalls (instrument-specific)
- **(a) Look-ahead via TWAP:** settlement (or any of the 60 final samples) is known only after
  the window; using it at decision time manufactures most apparent edge in a 15-min binary.
- **(b) Fill / no-adverse-selection:** assuming fills at the observed print with no queue
  removes the maker's dominant cost — you're filled precisely when the market moves against you.
- **(c) Fee omission:** churning both sides pays quadratic fees on huge volume; a fee-free
  backtest turns negative-EV churn into a phantom edge.
- **(d) Survivorship/cherry-pick:** only BTC, only calm vol, only winning windows. 72M trades
  sounds large but can be one favorable coin/period.
- **(e) In-sample overfit:** tuning vol band & thresholds on the same data → good curve, zero
  OOS value.
- **(f) Slippage/capacity:** "$10k/day" assumes depth absorbs your size; scaling moves the very
  prints you backtested against.

## Marketing-pitch priors
"5-min" is **wrong** — the shortest standard market is `KXBTC15M` (15-min). A real operator
wouldn't misname their own instrument; the slip signals second-hand/embellished knowledge.
Combined with suspiciously round figures ($1M, exactly $10k/day), it reads like an
engagement/marketing hook (course, Discord, newsletter, clout). It *could* still be
directionally real, but priors shift toward pitch.

## Base rates
A solo retail bot netting a *steady* $10k/day on Kalshi crypto is rare. More likely:
a designated/incentivized MM earning rebates (not pure alpha), a short lucky streak
extrapolated, or a backtest-only/exaggerated figure. Sustained scalable retail HFT edge on a
thin venue is the tail outcome.

## What would change my mind
Audited brokerage/settlement statements; raw API trade logs **with an account id** tying fills
to one entity; a sealed-param **live forward-test** over weeks including fees; rerunnable
OOS/multi-regime backtest code.

## Verdict
Treat the headline as **likely embellished marketing, not a verified result.** The "trace" is
unprovable from public data; the backtest is highly susceptible to TWAP look-ahead and
fill/fee fictions; the factual slip + round numbers fit a pitch. *Some* bot making money in
these windows is plausible; the specific "$1M / $10k-a-day, I traced it" claim is low-credence.
**Confidence ~80%.**
