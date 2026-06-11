# External research — cited evidence (deep-research pass)

20 sources fetched → 90 claims → 25 adversarially verified (3-vote, need 2/3 to kill)
→ 21 confirmed, 4 killed. Full machine output: `data/` task log. This corroborates and
*sharpens* the primary analysis; three findings materially refine it (⭐).

## Confirmed findings

### ⭐ 1. Settlement = a TRIMMED, MULTI-EXCHANGE index average (not a single CEX print)
Kalshi crypto contracts settle on **CF Benchmarks' Real-Time Index (BRTI/RTI)** — a
**60-second window of per-second observations, trimmed-averaged (excluding the top and
bottom 20%)**, aggregated across **multiple major exchanges every second** "to reduce the
risk of price manipulation from any single exchange." *(high confidence, 3-0; Kalshi Help
Center + CF Benchmarks, primary issuer sources)*
- **Refines finding 04 (TWAP endgame):** the averaging is even more variance-reducing
  than a plain mean — trimming drops outliers, so the outcome locks *more* firmly →
  *strengthens* the near-lock thesis. (Sim 1 used a plain 60-sample mean; the real
  trimmed-multi-exchange index is at least as locked.)
- **Refines findings 02/03 (latency):** a clean **single-venue** latency arb vs
  Coinbase/Binance does **not map onto settlement** — you must track the *aggregated
  BRTI*, not one exchange. The edge is tracking the index faster than the Kalshi book,
  and cross-venue (Coinbase-vs-BRTI) arb is diluted.

### 2. BRTI recalculates once per second (~200ms premium feed on request)
Sets the cadence a fair-value model must mirror. *(high, 3-0; CF Benchmarks.)* "The
realistic edge is fast/accurate tracking of the index to price binary fair value, not
predicting BTC direction" — independent confirmation of finding 02.

### 3. Sub-5-min BTC direction is near-chance and dies on costs
Directional accuracy ~**52%** (McNally LSTM, "barely above the 50% chance level"; 2025
work low-mid-50s). Naive sign-based ML on hourly BTC: XGBoost long-only **+73.5% gross →
−64% net** at 10bps costs; best cost-aware config does **not** beat buy-and-hold after
bootstrap adjustment. *(high, 3-0; arXiv 2606.00060 / 2606.00071.)* Confirms finding 03.
*Caveat: hourly BTC ML, analogous not a direct 15-min-binary test.*

### 4. Real short-horizon predictability is sub-second microstructure, not multi-minute
Hawkes+COE next-mid-change model ~**67.1%** at the **1–5 second** horizon; 3-second
Binance returns predictable from order-flow imbalance / spread / VWAP-mid. But "profits
are upper bounds... transaction fees would reduce returns," and pure sign prediction
"fluctuates around zero." *(high/mixed, 3-0 & 2-1; Springer + arXiv 2602.00776.)* ⇒ the
only horizon with real edge is *below* the 60-second settlement window — **the averaging
plausibly neutralizes it** (an open question).

### 5. ⭐ Market-making "floor" is empirically false — makers LOSE even fee-free
Kalshi study (Bürgi, Deng & Whelan, GWU 2026-001): across ~314k contracts, **Makers
earned −9.64%** average return (Takers −31.46%) **even pre-2025 when makers paid ZERO
fees**; only makers buying contracts ≥50¢ earned +2.6%. *(medium, 2-1; primary.)*
**Important scope caveat:** the paper *explicitly excludes* the short-dated hourly crypto
markets — it covers politics/sports/financials, so this is the maker *role* by analogy,
not a direct BTC-15M measurement. Still: it independently refutes "spread capture is a
guaranteed floor" (finding 01, Sim 2).

### 6. ⭐ Maker subsidies are real but small & capped
- **Volume Incentive Program:** cashback **max $0.005/contract** (0.5¢), paid pro-rata by
  share of total volume (dilutes as you scale).
- **Liquidity Incentive Program** (Sep 15 2025–Sep 1 2026): pays for **resting orders
  even if unfilled**, scored from per-second random snapshots weighted by size/proximity
  to best price, pro-rata from a **$10–$1,000/market/day** pool.
*(high, 3-0; primary Kalshi docs.)*
- **Refines finding 05 (the "maker-rebate hinge"):** the rebate exists but is **capped at
  0.5¢/contract** — and **Sim 2 showed even a 1.75¢ rebate cannot overcome adverse
  selection**, so 0.5¢ certainly can't fund $10k/day on spread capture. The LIP's
  pay-to-rest mechanic is a *separate*, bounded income stream (harvest incentives, not
  alpha) — capped per market/day and diluted by competition.

### 7. Taker fee confirmed: $0.07·P·(1−P)/contract, rounded up
≈**1.77% of price at 50¢**; makers were free pre-April-2025, **charged after**. *(high,
3-0; primary.)* Exactly the model used in all four sims. "High-frequency turnover faces
costs that swamp a near-chance directional edge."

## Killed / unconfirmed (transparency)
- "No model beats a random walk at hourly BTC" — **refuted 0-3** (too strong; weak
  intermittent predictability exists in-sample, e.g. CIDR projection scores, but not
  cost-robust).
- "BTC directional taker (crowd-fading) specifically unprofitable, t=−0.67, p=0.75" —
  split **1-2** (not confirmed, but further undercuts BTC directional edge).
- "BTC maker spread-capture had strong backtest (IR 41.78) but flash-crash tail" — split
  **1-2** (so BTC maker tradability is genuinely *ambiguous*, with documented tail risk).

## Net effect on the verdict
Every load-bearing conclusion of the primary analysis is **independently supported**:
direction is unforecastable and cost-fragile (3); spread-capture is not a floor (5);
fees are a 1.77% headwind (7); rebates are real but too small to fund the headline (6);
and the genuine edge is **fast index-tracking / fair-value repricing** (2,4) plus the
TWAP/trimming-driven endgame (1). No audited evidence of a real $1M / $10k-day automated
Kalshi crypto trader was found.

## Key open questions (from the research)
1. **Current (post-April-2025) maker fee for short-dated crypto** — does any positive
   spread margin survive after fee + $0.005 rebate + LIP? *(the decisive unknown.)*
2. **Does the 60s trimmed-average settlement neutralize the only horizon (sub-second)
   where BTC predictability is real?**
3. Any **audited** primary evidence for the $1M/$10k-day claim? (none found.)
4. How **diluted** are the incentive pools at scale across competing MMs?
