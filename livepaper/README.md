# livepaper — live forward-test of the TWAP panic-fade (multi-market)

A self-contained live forward-test runner that runs the validated strategy
(see [`../STRATEGY.md`](../STRATEGY.md)) against **live** data and records
everything second-by-second. With `VELA_LIVE=1`, it places real Kalshi orders;
without it, it only watches the Kalshi order book / trade feed and books
theoretical fills.

## What it does

- Trades **multiple Kalshi crypto series at once** (see `MARKETS` in
  [`config.py`](config.py)): `KXBTC15M` (BTC up/down), `KXETH15M` (ETH up/down),
  and `KXBTCD` (BTC hourly above/below). All settle identically — the mean of 60
  CF-Benchmarks RTI samples over the final 60s — so the same lock applies to each.
- Streams **1s prices for every needed Binance symbol** (BTCUSDT, ETHUSDT) over
  one combined WS, plus the **live book + trades** for every tracked market.
- Keeps a **per-asset causal de-bias** (Binance→RTI): ~+$25 for BTC, ~+$0.6 for
  ETH (both ≈0.04% of price — it's the USDT basis). Each is bootstrapped at
  startup from that asset's 15M settled history.
- Every second, per market, computes the **de-biased TWAP margin** `m̂` and logs
  it with the full book. At **45s to close** it **locks the bet side** and arms if
  `|m̂| ≥ THR` — a **relative gate** (`THR_BPS` basis points of price) so it scales
  across BTC (~$10) and ETH (~$0.27) from one constant.
- While `sec_to_close ∈ [5,45]`, every winning-side print **≤ CAP** is faded into a
  **paper fill** (bounded by the per-window notional cap and live cash).
- On settlement it realizes PnL, updates the shared live risk balance, and folds
  the realized Binance-vs-RTI error back into that asset's de-bias.

For **laddered** series (`KXBTCD` has ~100 strikes/hour), only strikes within
`band` of spot are tracked — the rest are deep ITM/OTM and never near a panic.

Current params ([`config.py`](config.py)): `CAP=0.99`, `τ=45`, window `[1,45]s`,
`$50` starting risk balance, live size = `10%` of shared risk balance per order.

## Run it

```bash
cd bitcoin                      # the package imports backtest.kalshi_client
source ../ingest/venv/bin/activate     # (or any venv with the deps below)
python -m livepaper              # starts logging; Ctrl-C to stop cleanly
```

Deps: `websockets httpx certifi cryptography python-dotenv` (already in the ingest
venv). Auth comes from [`../.env`](../.env) (`KALSHI_API_KEY` + `KALSHI_API_SECRET`);
Binance market data needs no key.

Leave it running 6–12h. On start it back-fills the de-bias from the last ~160
settled windows, so the lock gate is calibrated from second one.

## Watch it

```bash
python -m livepaper.report        # PnL + activity summary (safe while running)
tail -f livepaper/data/run.log    # live decisions / settlements
```

## Where the data lands (`livepaper/data/`)

| file | contents |
|------|----------|
| `paper.db` | SQLite (WAL) — query live with `sqlite3 paper.db` |
| `../data_shared/portfolio.db` | shared live risk balance for split BTC/ETH bots |
| `raw_kalshi.jsonl` | every Kalshi WS message (book/trade/lifecycle) |
| `raw_binance.jsonl` | every Binance 1s close |
| `run.log` | human-readable event log |

**`paper.db` tables:** `prices` (1/s/symbol), `book_snaps` (1/s/market, full book
JSON), `estimates` (1/s/market: asset, `m̂`, margin, `thr_abs`, gate), `trades`
(every print), `fills` (every paper fill), `windows` (one row/settled market:
asset, series, realized PnL + balance), `debias` (per-window Binance−RTI error per
asset), `events` (decisions, reconnects, lifecycle).

## Sweeps / analysis (offline, in `../backtest/analysis/`)

- `leniency_sweep.py` — hit-rate vs edge frontier over THR×CAP
- `window_sweep.py` — fill-rate vs the time window / decision τ
- `drift_test.py BTC|ETH` — Binance-vs-CF-RTI drift for an asset (de-bias viability)
- `livepaper/analyze_caps.py` — re-derive PnL from the live DB under any price cap

Everything needed to re-derive PnL under *any* alternate parameter set offline is
stored, so a 6–12h run is a reusable dataset, not just a scoreboard.

## Reading the result

The number that matters is the **live maker fill rate** — whether the panic
prints we fade are actually reachable in real time (the one thing the backtest
couldn't settle). Compare `fills` capture vs the `trades` that qualified, and the
realized `windows.net_pnl` vs the backtest's +3–10¢/contract. Losses are expected
to be ~0 if the lock gate holds; a single loss is informative, so it's logged in
full.
