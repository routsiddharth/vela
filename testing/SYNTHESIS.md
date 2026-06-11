# Is the BTC 5-min HFT bot viable? — Synthesis

*Investigation of the claim: "An HFT bot is crossing $1M profit trading 5-min BTC
markets at $10k/day. It runs both sides of the same window, then a probability model
decides where the real money is. edge = fair_prob − market_price; it fades the crowd."*

Grounded in **live Kalshi market data** (probed 2026-06-08), **6 adversarial subagent
analyses**, **4 simulations**, and a **cited deep-research pass** (20 sources, 25
claims 3-vote-verified → 21 confirmed). All artifacts in this directory. The external
evidence independently supports every load-bearing conclusion below — see
[`findings/07_external_research.md`](findings/07_external_research.md).

---

## TL;DR verdict

**The strategy is partly real and partly marketing.** Stripped of the hype, there *is*
a coherent, profitable edge here — but it is **not** what the pitch says it is.

- ❌ **"Predicts BTC direction" / "fades the crowd"** — essentially false. Sub-15-min
  BTC direction is a random walk; you cannot forecast it. (Sim 3, findings 02/03.)
- ❌ **"Running both sides is a riskless floor"** — false. At the live 1¢ spread,
  two-sided quoting is fee-negative (−1.4¢/contract) and adverse selection makes it
  catastrophic (−11¢/contract). (Sim 2, finding 01.)
- ✅ **The real edge #1 — TWAP-settlement endgame.** The market settles on a *1-minute
  average of 60 samples*, which makes outcomes mechanically "locked" well before
  expiry. A feed-synchronized bot prices these near-locks far better than last-trade
  watchers. **This explains the "99¢ near-locks."** (Sim 1, finding 04.)
- ✅ **The real edge #2 — latency/staleness repricing.** "edge = fair_prob −
  market_price" is real, but `fair_prob` is just **live spot repriced into the binary
  faster than the Kalshi book updates** — pure latency arb vs Coinbase/Binance, not
  prediction. Edge ∝ how stale the book is; zero if competitors are equally fast.
  (Sim 3, findings 02/03.)
- ⚠️ **"$10k/day, $1M"** — implausible on *organic* edge (defensible ceiling ≈
  $150–$900/day per market for one participant). Becomes plausible **only** with a
  Kalshi designated-market-maker rebate, by trading many coins/venues at once, or if
  "profit" is gross/notional rather than net-of-fees. (Sim 4, finding 05.)
- ⚠️ **"$1M, traced his trades on 72M trades"** — low credence. Kalshi public trades
  are **anonymous** (no account id) so you cannot "trace his trades." "5-min" is a
  factual slip — the shortest BTC market is **15-min**. Round numbers + a TWAP
  look-ahead trap in any naive backtest ⇒ reads as an engagement/marketing pitch that
  *may* be directionally real. (Finding 06.)

**Bottom line:** A genuinely good bot here is a **low-latency market-maker / repricer**
that (a) harvests the TWAP endgame and (b) picks off stale quotes against a fast spot
feed — *not* a crowd-fading direction predictor. Whether that nets $10k/day depends
entirely on **maker-fee economics (rebates)** and **multi-market scale**, which is the
one thing still to verify.

---

## 1. What the market actually is (ground truth, observed live)

`KXBTC15M` — "Bitcoin price up down":
- **15-minute windows** (not 5-min). Contract = "BTC final ≥ the target set at window
  open" — a directional up/down binary, **struck at-the-money at open**.
- **Settlement = a TRIMMED average (drop top/bottom 20%) of 60 per-second CF Benchmarks
  RTI samples over the final 60s, aggregated across multiple exchanges** (a manipulation-
  resistant 1-minute index TWAP). *This is the single most important fact.* The trimming +
  multi-exchange aggregation makes outcomes lock *even harder* than a plain mean, and means
  a single-CEX (Coinbase/Binance) latency arb does **not** map cleanly onto settlement —
  you must track the aggregated BRTI index. *(confirmed: Kalshi + CF Benchmarks docs.)*
- **Fees: quadratic**, `fee/contract = round_up_cent(0.07·P·(1−P))` — peaks 1.75¢ at
  P=0.50, ~0.2¢ at P=0.97.
- **Book: bids-only both sides**, `yes_ask = 1 − best_no_bid`. Live: YES **0.75/0.76**
  (1¢ spread), ~30k contracts resting/side.

Full detail: [`findings/00_market_mechanics.md`](findings/00_market_mechanics.md).

---

## 2. The claims as testable theories — what works, what doesn't, and *how the bot does it*

| # | Theory (claim) | Verdict | Why | How a real bot actually does it |
|---|---|---|---|---|
| A | Two-sided quoting = riskless spread "floor" | ❌ **Doesn't work** as stated (high conf) | 1¢ spread < ~2.6¢ round-trip fee; adverse selection fills the *losing* leg (Sim 2: −1.4¢ to −26¢) | Quote around *model fair value*, not mid; cancel/replace sub-second; skew by inventory; live on MM rebates |
| B | A probability model finds "the real money" | ⚠️ **Real but mis-framed** (high conf) | For an ATM 15-min binary, fair ≈ Φ(d2) is dominated by *current spot* — it's a repricer, not a forecaster | Compute Φ(d2) from a low-latency spot feed faster than the book; trade the gap |
| C | Directional edge; "fade the crowd" | ❌ Forecasting ≈ 0; ✅ latency-repricing only (high conf) | Sub-15-min BTC is a random walk; profitable "fades" are fading *quote staleness*, not the future. Fading real momentum = run over | Fade only when the *quote* lags spot; never fade a genuine spot move |
| D | "99¢ near-locks" from skill | ✅ **Real edge** (TWAP endgame) (~80%) | 60-sample averaging locks the outcome early: **84% of windows already near-locked at T−60s, 94% at T−30s** (Sim 1) | Reconstruct the running TWAP from its own feed; buy 97¢ contracts truly worth ~99¢ |
| E | $10k/day, $1M | ⚠️ **Implausible organically** (~80%) | Needs 2–3× the market's *entire* daily volume; fees are 67–80% of gross; organic ceiling ≈ $150–900/day (Sim 4) | Only works via MM rebate + many coins/venues, or "profit" is gross not net |
| F | "Traced his trades; 72M-trade backtest" | ⚠️ **Low credence** (~80%) | Public trades are anonymous; "5-min" is wrong; TWAP look-ahead inflates any naive backtest | — (this is the marketing layer) |

Per-claim detail: [`findings/01..06_*.md`](findings/).

---

## 3. Where the edge really is (ranked)

1. **TWAP-settlement endgame (best risk-adjusted).** Low-variance, fee-efficient
   (tail prices ⇒ ~0.2¢ fee). Capacity-limited by thin size near 97–99¢. Main risk:
   **jump risk / calibration** on the rare flip (asymmetric: 33 wins of +3¢ wiped by
   one −97¢ loss). Requires accurate low-latency RTI reconstruction.
2. **Latency/staleness repricing.** Real and scalable across markets, but it's a
   **speed race** — edge collapses to zero if you're not faster than the marginal
   quoter (Sim 3: edge ∝ book staleness L). Requires genuine infra advantage.
3. **Incentive harvesting (bounded).** Kalshi *does* run maker incentives, but they're
   **small and capped**: a Volume rebate of **max $0.005/contract** (pro-rata, dilutes at
   scale) and a Liquidity Incentive Program paying for *resting orders even if unfilled*
   from a **$10–$1,000/market/day** pool. **Sim 2 showed even a 1.75¢ rebate can't beat
   adverse selection — so 0.5¢ can't fund $10k/day on spread capture.** The LIP's
   pay-to-rest is a separate, bounded income stream (harvest incentives, not alpha) — the
   one realistic *systematic* subsidy, but capped and competed for. *(confirmed: Kalshi
   docs.)* **Decisive unknown: the current post-April-2025 maker *fee* for short-dated
   crypto** — whether any positive spread margin survives fee + rebate + LIP.

---

## 4. Simulation evidence (all reproducible, pure-stdlib, in `sims/`)

- **Sim 1 — TWAP endgame** (`sims/sim1_twap_endgame.py`): 60k Monte-Carlo windows.
  - **84%** of windows already near-locked (cond. prob >0.9 or <0.1) at the start of the
    final minute; **94%** halfway through it.
  - TWAP-aware estimator is **perfectly calibrated** (mean_est ≈ realized in every bin).
  - vs a naive last-price trader: **RMS mispricing 5.6¢ (T−60s) → 9.8¢ (T−30s)**;
    |gap|>3¢ in 27–36% of windows = the exploitable edge.
- **Sim 2 — spread + adverse selection** (`sims/sim2_spread_adverse_selection.py`):
  1¢ spread nets **−1.4¢** on fees alone, **−11.4¢** under mild adverse selection; a
  1.75¢ maker rebate does **not** rescue it. The "floor" is fiction.
- **Sim 3 — latency repricing** (`sims/sim3_latency_repricing.py`): edge is **0 at zero
  lag** and grows with book staleness — ~**$500/day/market at 2s lag**, ~**$8,600/day at
  30s lag** (illustrative params). Confirms latency-arb, not forecasting.
- **Sim 4 — economics/capacity** (`sims/sim4_economics_capacity.py`): $10k/day needs
  500k–2M contracts/day = **1.4–5.8× the market's entire daily traded volume**; fee drag
  **67–80%** of gross; realistic single-participant ceiling **$86–$518/day** per market.

Raw outputs saved in [`data/sim*_output.txt`](data/).

---

## 5. Potential path forward (don't build yet — validate first)

**Cheapest decisive tests, in order:**

1. **Confirm the maker-fee reality.** Does `KXBTC15M` charge makers the quadratic fee,
   or are makers exempt/rebated / is there a designated-MM program? *This single fact
   decides whether any volume strategy is positive.* (Pull Kalshi fee schedule + MM
   program docs — the deep-research pass targets this.)
2. **Forward-test the TWAP endgame, paper-only.** Ingest CF Benchmarks RTI (or a
   Coinbase/Binance proxy), reconstruct the running 60-sample average live, and log the
   conditional fair vs the Kalshi last-trade in the final 2 minutes. Measure the *actual*
   mispricing and the *actual* resting size at 95–99¢ (real capacity). No capital.
3. **Measure book staleness.** Log Kalshi quote timestamps vs your spot feed; estimate
   the realized lag L (Sim 3 says edge ∝ L). If L ≈ 0 vs incumbents, the latency edge is
   not there for you.
4. **Honest backtest discipline.** Decision timestamps must precede *all* 60 settlement
   samples (no TWAP look-ahead); model fills as adverse (you trade when the market moves
   against you); charge real fees; test out-of-sample across vol regimes and coins.

**Only if (1)+(2)+(3) clear** do you build: a low-latency repricer + TWAP-endgame taker,
quoting around model fair value, sized to survive jump-risk blow-ups, ideally as a
rebated MM across BTC/ETH/+ 15-min markets simultaneously.

**What would change the verdict toward "very viable":** a real maker rebate ≥ ~1¢, a
genuine latency advantage over incumbents, and resting depth at the tails large enough
to deploy size. Absent those, expect **hundreds of $/day, not $10k.**

---

## 6. External corroboration (deep-research pass — complete)
Cited, 3-vote-verified evidence ([`findings/07_external_research.md`](findings/07_external_research.md))
independently supports the whole verdict:
- **Direction is unforecastable & cost-fragile** — ~52% accuracy; XGBoost +73.5% gross →
  −64% net at 10bps; no beat vs buy-and-hold after bootstrap. (Confirms Claim C.)
- **Spread-capture isn't a floor** — a Kalshi study found **makers lost −9.64% even when
  makers paid zero fees** (caveat: excludes short-dated crypto; maker *role* by analogy).
  (Confirms Claim A / Sim 2.)
- **Fees: $0.07·P·(1−P), ~1.77% at 50¢** — exactly the sims' model; makers charged since
  Apr-2025. **Rebates capped at $0.005/contract** + a per-market/day LIP pool. (Confirms
  Claim E / Sim 4: too small to fund the headline.)
- **Settlement is a trimmed multi-exchange index average** updating 1×/sec — the real edge
  is *fast index-tracking / fair-value repricing*, not prediction. (Confirms Claim B / D.)
- **No audited evidence** of a real $1M / $10k-day automated Kalshi crypto trader was found.

**Biggest remaining unknown:** does the 60-second trimmed-average settlement *neutralize*
the only horizon (sub-second microstructure) where BTC is actually predictable? If yes, it
caps even the sophisticated version of this strategy. That's the first thing the paper
forward-test (§5.2) would answer.
