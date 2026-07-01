# Vela

**A quant research project on a structural mispricing in Kalshi's short-dated crypto
markets — run and validated live, on real capital, because live fills are the only
honest out-of-sample test.**

Vela trades a structural mispricing in TWAP-settled binary options: the settlement
value is a 60-second average that is mostly *already realized* before expiry, yet
the order book keeps quoting it as live. The engine reconstructs the settling
average from free reference feeds, prices a calibrated win probability each second,
and executes only when expected value clears modeled fees and a confidence gate.
The live bot is the data-generating prerequisite; the research question — is this
edge real, or noise — is the point (see [Live results](#live-results-honest-not-oversold)).

*Vela — "the sail." It reckons settlement from the wind (public price feeds), then
trades the gap between what is already decided and what the book is still pricing.*

---

## Live results 

Realized PnL reconciled to the cent.
Indexed to hide account size — reproduce it with [`scripts/pnl_report.py`](scripts/pnl_report.py).

| Metric | Value |
|---|---|
| Indexed equity | **≈1.40×** since the 2026-06-18 bankroll reset |
| Realized PnL | **+$20.23** over **210** traded windows |
| Hit rate | **97.6%** (205 W / 5 L) |
| Per-window t-stat | **≈1.0 — not yet statistically significant** |

The hit rate is the least trustworthy number here: the payoff is left-skewed (many
small wins, a rare large loss), and N=210 doesn't yet clear its own noise. Whether
the edge survives an honest significance test — HAC t-stat, block bootstrap,
calibration, fill-conditional adverse selection — is the actual research; the live
bot exists to keep generating the out-of-sample data that question needs. That
analysis lives in [`notebooks/analysis.ipynb`](notebooks/analysis.ipynb) — the main
research artifact — with supporting scripts in [`analysis/`](analysis/).

---

## Motivation and beginnings

Kalshi is the first CFTC-regulated exchange for event contracts — a young market where,
as with any young market, the structural inefficiencies haven't yet been competed away.
I started poking at it out of curiosity about that microstructure, and the project grew
into a hands-on way to learn real-time quant execution end to end: reconstructing a
reference index from raw feeds, calibrating a probability online, and managing live order
flow against modeled fees. The real seed, though, was a single observation. Scrolling the
short-dated crypto markets one day, I actually read the resolution criteria — these
contracts don't settle on the last price, they settle on the **mean of 60 samples over the
final 60 seconds** — and noticed the order book was still quoting them as if the terminal
tick decided everything.

That gap is the whole thesis: once most of those 60 samples are banked, the outcome is
**near-determined** while implied probability is still mean-reverting around 0.50, and
flow-driven participants keep dumping the winning side on late ticks the average has
already absorbed. Vela is the attempt to harvest that — to find a repeatable pattern in
something that *looks* like noise (a coin-flip binary) but is actually mostly resolved
arithmetic. It began as a paper simulator to test whether the edge survived fees and
slippage; once it did, it went live on real capital. Convergence arithmetic and
disciplined execution, not directional forecasting.

---

## The edge

Kalshi's short-dated crypto contracts ("BTC up or down over the next 15 min?", "BTC
above $X at the top of the hour?") **do not settle on the terminal price** — they
settle on the **arithmetic mean of 60 CF-Benchmarks RTI samples over the final 60
seconds.**

A mean is a low-pass filter. Once ~45 of the 60 samples are banked, the residual 15
have bounded leverage over the result, so the outcome is frequently **near-determined
while implied probability is still mean-reverting around 0.50.** Flow-driven
participants mark to *last price* and dump the winning side on a late tick that the
average has already absorbed — the side still settles at $1. That is the
mispricing Vela harvests.

**Execution loop:**
1. Reconstruct the settling 60s TWAP in real time from **Binance 1s** prints, with a
   **causal Binance→RTI de-bias** (Binance carries a stable ~3.5 bps premium to the
   CF index) re-estimated online from realized settlements.
2. At ~45s to expiry, **fix the directional bet** on the favored side — gated on
   `p_side = P(side wins | model uncertainty)` clearing a per-asset confidence
   threshold.
3. Cross or rest into the final seconds **only at a positive-EV entry** (price
   floor/cap, fees modeled to the cent), holding to settlement.

Convergence arithmetic and disciplined execution, not directional forecasting.

---

## The markets

All settle to the **mean of 60 RTI samples over the final 60s**. They differ only in the strike:

| Series | Question | Strike | Window | Index / proxy |
|--------|----------|--------|--------|---------------|
| `KXBTC15M` | up/down vs 15 min ago | prior 60s-avg (ATM) | 15 min | BRTI / BTCUSDT |
| `KXETH15M` | up/down vs 15 min ago | prior 60s-avg (ATM) | 15 min | ERTI / ETHUSDT |
| `KXBTCD`   | above $X at the hour | fixed ladder (`greater`) | 1 hour | BRTI / BTCUSDT |
| `KXETHD`   | above $X at the hour | fixed ladder (`greater`) | 1 hour | ERTI / ETHUSDT |

Prices are in dollars (0.01–0.99); a YES contract pays $1. The two-sided "range"
series (`KXBTC`/`KXETH`) are excluded — their two-boundary margin doesn't fit the
single-margin model.

**Fees matter and are modeled exactly.** Taker = `ceil_cent(0.07·p·(1−p))`; maker =
`ceil_cent(0.0175·qty·p·(1−p))` per order. Both peak near p=0.50 — the edge lives
in the tails where fees are lowest.

---

## Three live trading pathways

| # | Name | Mechanism | Markets | Gate |
|---|------|-----------|---------|------|
| 1 | BTC panic-fade | maker limit bid at decision (~45s to close) | KXBTC15M + KXBTCD | p_side ≥ 0.84 |
| 2 | BTC strong-take | taker buy when ask ≥ 0.95 within [2s, 45s) | KXBTC15M only | none (price gate only) |
| 3 | ETH panic-fade | same as BTC panic-fade, stricter gate | KXETH15M + KXETHD | p_side ≥ 0.98 |

All three run concurrently on the same Kalshi account. BTC and ETH bots are separate
processes with independent kill-switches and daily-loss halts.

---

## Architecture

```
vela/
├── notebooks/
│   └── analysis.ipynb   ★ MAIN ARTIFACT — the research writeup (de-bias fit,
│                          settlement decomposition, probability calibration,
│                          fill-conditional adverse selection, PnL attribution)
├── livepaper/
│   ├── priceblend/      Binance 1s feed + causal de-bias — independent of Kalshi
│   │   ├── feed.py
│   │   ├── debias.py
│   │   └── service.py
│   ├── trading/         Kalshi book/orders/engine, driven by PriceBlend's output
│   │   ├── kalshi_ws.py   Kalshi authed WS (book/trade/lifecycle)
│   │   ├── book.py        order book + per-market state
│   │   ├── discovery.py   REST market discovery + settlement lookups
│   │   ├── engine.py      per-sec estimate + p_side gate + fill logic + settlement
│   │   ├── projection.py  window projection (mhat/margin/sd_S/p_side)
│   │   ├── live_exec.py   real Kalshi order lifecycle (place, poll fills, cancel, halt)
│   │   ├── broker.py      LiveBroker (Kalshi REST) + MockBroker (tests)
│   │   └── portfolio.py   shared live risk ledger (BTC/ETH split)
│   ├── store.py         SQLite (WAL)
│   ├── config.py        all strategy params + live trading flags
│   ├── report.py        PnL / win-rate summary  (python -m livepaper.report)
│   ├── data_btc/        BTC bot data: paper.db, run.log
│   └── data_eth/        ETH bot data: paper.db, run.log
├── analysis/            research scripts feeding the notebook — signal
│                          validation, fill calibration, book coverage
├── backtest/            historical data + strategy backtests
└── scripts/             ops utilities: pnl_report.py, check_btc.py, show_strong.py
```

**Start here:** [`notebooks/analysis.ipynb`](notebooks/analysis.ipynb) is the main
artifact — it takes the live data and works the research question end to end: the
Binance→RTI de-bias fit, what actually determines settlement, probability
calibration, fill-conditional adverse selection, and PnL attribution by bucket.
Everything under `livepaper/` is the live engine that generates the data the
notebook analyzes; `analysis/` holds the scripts that stage it.

Each second per tracked market: pull Binance feed → compute de-biased TWAP margin
and `p_side` → at 45s lock the bet if gate passes → place real maker limit bid →
poll fills → cancel unfilled at 2s → on settlement, realize PnL and update de-bias.

---

## Run

Requires `websockets httpx certifi cryptography python-dotenv`. Auth in `.env`
(`KALSHI_API_KEY` + `KALSHI_API_SECRET`).

```bash
# BTC bot — panic-fade + strong-take, live
VELA_ASSET=BTC VELA_LIVE=1 VELA_STRONG_TAKE=1 ./run_btc.sh

# ETH bot — panic-fade only, live, tighter stop
VELA_ASSET=ETH VELA_LIVE=1 VELA_MAX_DAILY_LOSS=15 VELA_MAX_OPEN_NOTIONAL=15 ./run_eth.sh

# inspect
python -m livepaper.report
tail -f livepaper/data_btc/run.log   # fills + PnL only
```

**Kill switches** (cancel all open orders + halt immediately):
```bash
touch livepaper/data_btc/KILL
touch livepaper/data_eth/KILL
```

**Data:** `paper.db` (SQLite, WAL) holds `prices`, `book_snaps`, `estimates`,
`trades`, `fills`, `windows`, `debias`, `events`. The shared live risk balance is
in `livepaper/data_shared/portfolio.db`. Safe to query while running.

---

## Key config (`livepaper/config.py`)

| Param | Value | Meaning |
|-------|-------|---------|
| `P_SIDE_MIN_BY_ASSET` | BTC: 0.84, ETH: 0.98 | per-asset confidence gate |
| `WIN_PX_FLOOR` / `CAP` | 0.45 / 0.99 | only fade genuine panic, not deep OTM |
| `PORTFOLIO_FRACTION` | 0.10 | live order size = 10% of shared risk balance |
| `LIVE_MAX_OPEN_NOTIONAL` / `LIVE_MAX_OPEN_FRACTION` | $25 / 0.50 | total resting+open exposure cap is max of floor and fraction |
| `LIVE_MAX_DAILY_LOSS` | $25 BTC / $15 ETH | per-bot stop-loss (`VELA_MAX_DAILY_LOSS`) |
| `STRONG_TAKE_THRESH` | 0.95 | taker pathway fires when ask ≥ this |
| `RAW_DUMP` | False | writes raw Kalshi WS messages to disk (large; opt in to enable) |
