# bitcoin/ — BTC 5-min HFT bot viability investigation

Investigation of a viral claim that an HFT bot nets $10k/day trading "5-min" Kalshi BTC
markets by "running both sides of the window" + a "probability model" that "fades the
crowd." **Read [`SYNTHESIS.md`](SYNTHESIS.md) first** — it's the full answer.

## Layout
- **[`SYNTHESIS.md`](SYNTHESIS.md)** — verdict, what works vs what doesn't, how a real
  bot does it, ranked edges, simulation evidence, path forward.
- **`findings/`** — one file per piece of the argument:
  - `00_market_mechanics.md` — ground truth from live Kalshi (the 15-min market, TWAP
    settlement, quadratic fees, the live order book).
  - `01_claimA_mm_floor.md` — two-sided "riskless floor" (❌).
  - `02_claimB_fair_value_model.md` — the "probability model" is a spot repricer (⚠️).
  - `03_claimC_directional_fade.md` — "fade the crowd" / direction prediction (❌).
  - `04_claimD_twap_endgame.md` — the TWAP-settlement endgame edge (✅, the real one).
  - `05_claimE_economics.md` — $10k/day, fees, capacity (⚠️ implausible organically).
  - `06_claimF_credibility.md` — "$1M / 72M backtest / traced his trades" (⚠️ low cred).
  - `07_external_research.md` — cited external evidence (deep-research pass).
- **`sims/`** — pure-stdlib simulations (no deps):
  - `sim1_twap_endgame.py` — Monte-Carlo of the 60-sample TWAP lock + calibration.
  - `sim2_spread_adverse_selection.py` — two-sided MM PnL vs fees vs adverse selection.
  - `sim3_latency_repricing.py` — latency-arb edge vs book staleness.
  - `sim4_economics_capacity.py` — required volume, fee drag, capacity ceiling.
- **`data/`** — live Kalshi snapshot + saved `sim*_output.txt` runs.

## Run the sims
```bash
cd bitcoin/sims
for f in sim*.py; do echo "== $f =="; python3 "$f"; done
```

## One-line verdict
Real edge exists, but it's **low-latency market-making + TWAP-endgame repricing**, *not*
crowd-fading direction prediction. $10k/day is implausible without a maker rebate and
multi-market scale; expect hundreds of $/day organically. **Don't build before validating
the maker-fee/rebate economics and forward-testing the TWAP endgame on paper.**
