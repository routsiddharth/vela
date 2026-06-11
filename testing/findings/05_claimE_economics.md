# Claim E — Economics, fees & the $10k/day claim (adversarial review)

**Claimed:** ~$1M cumulative, ~$10,000/day, fills 21¢–99¢ across BTC & ETH.

## Per-trade net edge  (net = gross − ceil_cent(0.07·P·(1−P)))
- **(a) Latency scalp, P≈0.5:** fee = ceil(1.75¢) = **2¢**. Gross 1–3¢ → net **−1 to +1¢**,
  mean ~**0¢**. Fee eats the edge at mid-prices.
- **(b) TWAP near-lock, P=0.97:** fee = ceil(0.20¢) = **1¢** (round-up bites). Gross 2–3¢ →
  net **+1 to +2¢**. The only reliably positive archetype (tail prices = tiny raw fees).
- **(c) Spread capture, P≈0.5:** 1¢ gross, fee ~2¢/leg → net **−1¢ (single) to −3¢ (round
  trip)**. Structurally negative without a rebate.

## Volume required ($10k = 1,000,000¢/day)
| Net ¢/contract | Contracts/day | Per 15-min window (96/day) | Realistic? |
|---|---|---|---|
| 2¢ (optimistic) | 500,000 | ~5,200 | Aggressive |
| 1¢ (base) | 1,000,000 | ~10,400 | Very aggressive |
| 0.5¢ (pessimistic) | 2,000,000 | ~20,800 | Fantasy (single coin) |
| 0.25¢ (after slippage) | 4,000,000 | ~41,700 | Implausible |

**Depth check:** ~30k resting/side/window, but *traded* volume ≈ 5–20% of resting → ~3k–6k
traded/window → ~300k–580k traded/day on KXBTC15M. Netting $10k at 1¢ needs **1M fills/day =
2–3× the market's entire traded volume**, as counterparty to nearly every print. ETH + other
coins maybe 2–3× the pool — still demanding a dominant share of every venue.

## Fee drag
At 1M contracts/day mid-price: fee ≈ **$20k/day**; to net $10k, gross must be $30k → fee/gross
≈ **67%**. At 0.5¢ net (2M contracts): fees ~$40k, gross ~$50k → **80%**. Even the favorable
tail book (1¢ fee, 500k): **$5k/day** fees vs $15k gross = **33%**. Fees dominate every plausible
volume.

## Capacity ceiling
Scaling triggers (i) book-walking past the 1¢ top level through 20 levels (turns +1¢ negative
within a few thousand contracts), (ii) queue-priority loss behind 30k resting, (iii)
competition from other latency bots sharing the 1–3¢ gross. A single participant realistically
captures **5–15% of traded flow** → ~30k–90k contracts/day at true net 0.5–1¢ → **$150–$900/day**.
$10k/day is **10–60× above** that ceiling.

## Maker-rebate hinge  ⭐ the load-bearing unknown
If Kalshi runs a **designated-MM / maker-rebate** program, a 0.5–1¢ rebate flips archetype (c)
from −1¢ to break-even/positive and subsidizes (a)'s fee. Economics become *volume-driven*:
earn the rebate on every resting fill regardless of directional edge; 1M contracts/day × +1¢
rebate = the $10k. **Must verify: does KXBTC15M have an active MM rebate/incentive tier, at
what rate, with what obligations?**

## Verdict
**$10k/day net is implausible on organic edge alone** — requires ~2–3× the market's traded
volume at a net (1¢) fees largely consume; defensible organic ceiling ~$150–$900/day. Plausible
**only** under a maker-rebate/MM program, OR if "profit" is gross/notional, not realized-net.
$1M cumulative at a real $10k/day ≈ 100 trading days (3–4 mo) — internally consistent *if* the
daily number holds; at ~$500/day it's ~5+ years. **Confidence ~80%** $10k/day is unachievable
organically. **Verify the rebate program; demand fees-paid and net-after-fees on the equity
curve — gross-marked P&L is the likeliest explanation for the headline.**
