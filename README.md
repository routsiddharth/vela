# Vela

**A real-time probabilistic trading engine for Kalshi crypto prediction markets.**

*Vela — "the sail." It reckons the true settlement from the wind (free public price
feeds), then rides the gap between what's already decided and what the crowd is
still pricing.*

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
   premium vs the CF index).
2. At ~45s to close, **lock the bet** on the favored side — but only if the model's
   `p_side = P(our side wins | its own uncertainty)` clears a confidence gate.
3. Over the final seconds, **buy that side only at a genuine discount** (price
   floor/cap), holding to settlement.

It is arithmetic and patience, not prediction. Full thesis: [`STRATEGY.md`](STRATEGY.md).

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
├── livepaper/
│   ├── feeds.py        Binance multi-symbol 1s WS + Kalshi authed WS (book/trade/lifecycle)
│   ├── market.py       order book, per-market state, REST discovery, per-asset de-bias
│   ├── engine.py       per-sec estimate + p_side gate + fill logic + settlement
│   ├── live_exec.py    real Kalshi order lifecycle (place, poll fills, cancel, halt)
│   ├── broker.py       LiveBroker (Kalshi REST) + MockBroker (tests)
│   ├── store.py        SQLite (WAL)
│   ├── config.py       all strategy params + live trading flags
│   ├── report.py       PnL / win-rate summary  (python -m livepaper.report)
│   ├── data_btc/       BTC bot data: paper.db, run.log
│   └── data_eth/       ETH bot data: paper.db, run.log
├── backtest/           historical data + analysis
└── STRATEGY.md         strategy write-up
```

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
VELA_ASSET=ETH VELA_LIVE=1 VELA_POSITION_USD=5 VELA_MAX_DAILY_LOSS=15 VELA_MAX_OPEN_NOTIONAL=15 ./run_eth.sh

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
`trades`, `fills`, `windows`, `debias`, `events`. Safe to query while running.

---

## Key config (`livepaper/config.py`)

| Param | Value | Meaning |
|-------|-------|---------|
| `P_SIDE_MIN_BY_ASSET` | BTC: 0.84, ETH: 0.98 | per-asset confidence gate |
| `WIN_PX_FLOOR` / `CAP` | 0.45 / 0.99 | only fade genuine panic, not deep OTM |
| `POSITION_USD` | $5 | fixed notional per window (`VELA_POSITION_USD` to override) |
| `LIVE_MAX_DAILY_LOSS` | $25 BTC / $15 ETH | per-bot stop-loss (`VELA_MAX_DAILY_LOSS`) |
| `STRONG_TAKE_THRESH` | 0.95 | taker pathway fires when ask ≥ this |
| `RAW_DUMP` | True | writes raw Kalshi WS messages to disk (large; set False to disable) |
