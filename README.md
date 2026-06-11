# Vela

**A real-time probabilistic trading engine for Kalshi crypto prediction markets.**

*Vela — "the sail." It reckons the true settlement from the wind (free public price
feeds), then rides the gap between what's already decided and what the crowd is
still pricing.*

This is a **paper-trading research project**: the engine watches live Kalshi order
books and trades and books *theoretical* fills against a $50 paper bankroll. It
**never places a real order.**

---

## The idea

Kalshi's short-dated crypto markets ("will BTC be up or down over the next 15
min?", "will BTC be above $X at the top of the hour?") **do not settle on the price
at the final instant** — they settle on the **simple mean of 60 CF-Benchmarks RTI
samples over the final 60 seconds.**

An average is sluggish. Once ~45 of those 60 samples are banked, the remaining 15
can barely move the result — so the outcome is often **mathematically near-settled
while the market is still trading it as live.** Naive traders watch *last price* and
**panic-dump the already-won side** when a late tick scares them. The average has
already drowned that tick out, so that side still pays $1.

**Vela's edge is fading that panic:**
1. Reconstruct the settling 60s average in real time from free **Binance 1s**
   prices, with a **causal Binance→RTI de-bias** (Binance runs a stable ~0.035%
   premium vs the CF index — see `backtest/analysis/drift_test.py`).
2. At ~45s to close, **lock the bet** on the favored side — but only if the model's
   `p_side = P(our side wins | its own uncertainty)` clears a confidence gate.
3. Over the final seconds, **buy that side only at a genuine discount** (a price
   floor/cap), holding to settlement.

It is arithmetic and patience, not prediction. Full thesis: [`STRATEGY.md`](STRATEGY.md).

---

## The markets

All settle to the **mean of 60 RTI samples over the final 60s** (confirmed from
every series' `rules_primary`). They differ only in the strike:

| Series | Question | Strike | Window | Index / proxy |
|--------|----------|--------|--------|---------------|
| `KXBTC15M` | up/down vs 15 min ago | prior 60s-avg (ATM) | 15 min | BRTI / BTCUSDT |
| `KXETH15M` | up/down vs 15 min ago | prior 60s-avg (ATM) | 15 min | ERTI / ETHUSDT |
| `KXBTCD`   | above $X at the hour | fixed ladder (`greater`) | 1 hour | BRTI / BTCUSDT |
| `KXETHD`   | above $X at the hour | fixed ladder (`greater`) | 1 hour | ERTI / ETHUSDT |

Prices are in dollars (0.01–0.99); a YES contract pays $1. The two-sided "range"
series (`KXBTC`/`KXETH`) are intentionally excluded — their two-boundary margin
doesn't fit the single-margin model.

**Fees matter and are modeled exactly.** Taker = `ceil_cent(0.07·p·(1−p))` (min 1¢);
maker = `ceil_cent(0.0175·qty·p·(1−p))` per order. Both peak near p=0.50 (~1.75¢/ct
taker) — trading the coin-flip is expensive, so the edge lives in the tails.

---

## Architecture

```
vela/
├── livepaper/          the live paper-trading engine (read-only)
│   ├── feeds.py        Binance multi-symbol 1s WS + Kalshi authed WS (book/trade/lifecycle)
│   ├── market.py       order book, per-market state, REST discovery, per-asset de-bias
│   ├── engine.py       per-sec estimate + p_side gate + trade-driven paper fills + settlement
│   ├── store.py        SQLite (WAL) + raw JSONL firehose
│   ├── config.py       ALL strategy params live here (gate, floor/cap, sizing, fees)
│   ├── report.py       PnL / win-rate / balance summary  (python -m livepaper.report)
│   └── data/           paper.db, raw_*.jsonl, run.log   ← the live dataset
├── backtest/           historical data + analysis (data/, analysis/, findings/)
├── testing/            earlier research notebooks/sims
└── STRATEGY.md         the high-level strategy write-up
```

How the engine works each second, per tracked market: pull the asset's Binance feed
→ compute the de-biased TWAP margin and `p_side` → at 45s lock the bet if
`p_side ≥ P_SIDE_MIN` → while in the fill window, book a paper fill on any
winning-side print in `[WIN_PX_FLOOR, CAP]` → on settlement, realize PnL, update the
$50 balance, and fold the realized Binance−RTI error back into that asset's de-bias.
Everything is logged second-by-second.

Strategy knobs (in `config.py`): `P_SIDE_MIN` (confidence gate), `WIN_PX_FLOOR` /
`CAP` (discount band), `MIN_WINDOW_NOTIONAL` / `PORTFOLIO_FRACTION` (sizing),
`TAU_DECISION`, `SEC_LO/HI`, `MARKETS`.

---

## What we've learned (the honest part)

This is research, and the research says the easy money isn't here. Key findings
(full write-ups in `backtest/findings/`):

- **Six genuinely distinct strategies were tested and all make ≈ $0/day after
  fees** — directional, market-making, cross-strike ladder arbitrage,
  favorite-longshot, order-flow imbalance, and a grab-bag of time-of-day /
  round-number / lead-lag ideas. The market is efficient to every public signal and
  the fee is a wall at the coin-flip. (`EXPLORE_SYNTHESIS.md`)
- **The panic-fade is the one approach with a real edge — but it's tiny and
  capacity-bound:** ~$0.2–1.6/day on a $50 bankroll in backtest, *not* the $5/day
  one might hope for. More size is leverage, not alpha.
- **The EV truth:** for a binary, `EV/contract = win_rate − price`. Live, fills
  cluster at high prices (0.97–0.99) where a win is worth ~1¢ against a ~$5
  downside, so even a ~97% win rate barely covers the losses — **roughly
  break-even.** The genuine edge is in the *rarer, cheaper* fills; `CAP` is the knob
  that trades fill-frequency against per-fill margin.
- **Best swarm-optimized config** (`opt_*.md`): `P_SIDE_MIN≈0.84, WIN_PX_FLOOR=0.45`
  — ~4× the edge of the conservative gate, but it takes real ~$5 losing windows
  (96.6% win, not 100%). The 0.95–0.97 region is a trap (out-of-sample reverses
  sign under a flipped train/test split).

**Status:** a long-lived live paper experiment is running in `livepaper/data/` to
measure the realized win rate vs. entry price over time. Honest expectation is
~break-even; the deliverable is the dataset and the execution learnings, not income.

---

## Run / inspect

The live engine needs `websockets httpx certifi cryptography python-dotenv`;
backtests also need `pandas numpy scipy pyarrow`. Auth lives in `.env`
(`KALSHI_API_KEY` + `KALSHI_API_SECRET`; Binance market data needs no key).

```bash
cd vela
python3 -m venv venv && source venv/bin/activate
pip install websockets httpx certifi cryptography python-dotenv pandas numpy scipy pyarrow

python -m livepaper            # start the engine (read-only; Ctrl-C to stop)
python -m livepaper.report     # PnL / win-rate / balance  (safe while running)
tail -f livepaper/data/run.log # live decisions & settlements

# backtests / analysis
python -m backtest.analysis.opt_harness        # parameter evaluator (OOS-aware)
python -m backtest.analysis.leniency_sweep     # gate × cap frontier
python -m backtest.analysis.drift_test ETH     # Binance-vs-RTI drift for an asset
```

**The data:** `livepaper/data/paper.db` (SQLite, WAL — query live with `sqlite3`)
holds `prices`, `book_snaps`, `estimates`, `trades`, `fills`, `windows`, `debias`,
`events`. `raw_kalshi.jsonl` / `raw_binance.jsonl` are the full message firehose.

---

## Invariants

- **Read-only.** The engine subscribes to market data and books paper fills; it
  never sends an order. Keep it that way until explicitly going live.
- **Behavior is configured in `config.py`, not scattered.** Secrets are the only
  thing in `.env`, never committed.
- **The watch metric is the realized win rate vs. average entry price** — not the
  realized $. You're positive only while `win% ≥ avg entry price`.
