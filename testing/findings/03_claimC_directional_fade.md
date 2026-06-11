# Claim C — Directional edge & "fade the crowd" (adversarial review)

**Claim:** "edge = fair_prob − market_price; the bot estimates true probability BTC moves a direction and FADES THE CROWD when price disagrees; fills run 21¢ → 99¢."

## Random-walk attack
Sub-15-min BTC log-returns are ≈ a martingale: HF return autocorrelation is statistically
near zero, and the little that exists (bid-ask bounce, seconds-scale momentum) is too small to
clear costs and is arbitraged first. So *direction* is ≈ unforecastable beyond 50/50 given
spot. Decompose `fair − price`: it comes from (1) a better forecast of future drift = true
directional alpha, or (2) a faster/better estimate of the *current* fair given live spot. Since
(1) ≈ 0, **almost the entire exploitable gap is (2): latency repricing** — computing a function
of an already-known spot faster than the quote updates. Not prediction.

## When fading pays vs blows up
- **Pays:** Kalshi quote lags spot / thin-book overreaction pushes the binary off
  spot-implied fair; you buy the cheap side, quote snaps back. (This is just latency repricing
  — you're fading the *quote's staleness*, not the crowd's view of the future.)
- **Blows up:** the move is real (liquidation cascade / news); the crowd is right, momentum
  continues. **Example:** ATM 15M, 5 min in, a liquidation drives spot +0.4%; "up" jumps
  0.50→0.80; you fade (buy down@0.20 on a "fair=0.55"); spot grinds +0.3% more into the TWAP;
  up settles 1.00; you lose ~0.20/contract — one tail trade erases dozens of 2¢ scalps.

## The 21¢–99¢ tautology
Offered as proof of skill; actually a property of the instrument. An ATM binary's fair value
is N(d)-shaped in (spot-distance ÷ remaining vol-budget). Early & near strike → ~0.50; late &
displaced → ~0.99 on the winning side, ~0.01–0.21 on the losing side of the *same* window. A
bot trading across all (τ, spot-distance) states **mechanically** shows 21¢→99¢ fills whether
or not it has edge. The range proves it traded different points on the value curve — nothing
about being *right*.

## Overfitting / look-ahead trap
`fair_prob` depends on params (realized vol, threshold, TWAP model). Tuned on the same history
it's scored on → curve-fit. Two leaks: (1) using settlement-window data (the 60 future RTI
samples) at decision time = direct look-ahead; (2) selecting vol/threshold grid by backtest
PnL → guaranteed in-sample edge that mean-reverts to fee drag live. The quadratic fee is the
floor a real edge must clear; overfit edges don't.

## Verdict
A **directional/forecasting** edge essentially **does not exist** (random walk ⇒ term 1 ≈ 0).
What can be real is a **repricing-latency edge** — fee-gated, and a speed race vs other latency
players. "Fade the crowd" is misleading: profitable fades fade *quote staleness*, not the
future; indiscriminate fading walks into momentum/liquidation tails. The 21¢→99¢ range is a
tautology. **Confidence ~85%** the edge (if any) is latency-repricing, not forecasting.
