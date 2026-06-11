# Strategy-search shared notes (read first)

## The problem we are solving
The live paper run is **left-skewed**: 14 small wins (~0–3¢ on ~90¢+ stakes) + 1
big loss (−$5.357) ⇒ **net −$4.23**. One flip erases all wins. Goal: **maximize
edge** and **shift the PnL distribution right** (cut the left tail). The −$5 loss
came from a fill `@0.28` at margin only `+30` — a *cheap + thin-margin* print,
which is adverse selection (the market knew), NOT a panic dump. Treating all
cheap prints as panic is the core bug.

## The bet (settlement)
Kalshi crypto windows settle on the **plain mean of 60 RTI 1s samples over the
final 60s**. `YES wins iff S >= strike`. Binance {BTC,ETH}USDT 1s is a proxy for
RTI; its bias drifts → use a **causal trailing-24h (96-window) median de-bias**.

## Corrected fees — USE `backtest/strategy_search/fees.py`
We REST bids → we are the **MAKER** (rate 0.0175), or **fee-free** if the product
isn't in Kalshi's maker-fee list. The old code used 0.07 + a per-contract 1¢
floor — ~16× too high. Headline results under MAKER; show ZERO (optimistic) and
TAKER/old (pessimistic) alongside.

## Data
Backtest parquet (BIG, statistical power, BTC-only KXBTC15M, ~2 months/6,308 windows):
- `backtest/data/markets.parquet`: ticker, open_time, close_time, close_dt(tz),
  strike, true_settle, result(yes/no), volume_fp, margin(=true_settle−strike; YES iff ≥0)
- `backtest/data/trades.parquet` (2.44M rows, final 180s of a 2500-window sample):
  ticker, created_time, sec_to_close(float), yes_price, no_price, size, taker_side
- `backtest/data/binance_1s.parquet` (1.9M): ticker, sec_to_close(int 1..300), price

Live paper SQLite (SMALL, 54 windows, but BTC+ETH+KXBTCD AND real book depth):
- `livepaper/data/paper.db` tables: `prices, book_snaps, estimates, trades,
  fills, windows, debias, events`. `book_snaps` has top-of-book + `book_json`
  (full depth) per sec/market — the only source of **resting liquidity / queue**
  info. `windows` has realized per-window PnL. Schema in `livepaper/store.py`.
  Open read-only: `sqlite3.connect("file:...paper.db?mode=ro", uri=True)`.

## Existing engine to REUSE (don't reinvent)
- `backtest/btc_lib.py`: `load_markets()`, `binance_matrix()` (piv: ticker×sec→price),
  `raw_avg60(piv)`, `causal_bias(m,raw60)` (delta), `estimate(piv,tau,delta)` (causal Ŝ).
- `backtest/analysis/fade_lib.py`: `model_pwin(piv,m,tau,sigma_sec)` → per-ticker
  `mhat, margin_hat, sd_S, p_yes` (PROPER prob via diffusion + proxy variance — use
  this instead of the crude bps gate), `market_price_at_tau(trades,tau)`,
  `estimate_sigma_sec(piv)`.
- `backtest/analysis/final_strategy.py`: the canonical fade backtest `run(THR,CAP,...)`.
  Run modules from `bitcoin/` with the venv: `python -m backtest.analysis.final_strategy`.

## Current operating point (config.py)
τ_decision=45, actionable sec∈[5,45], gate |margin|≥THR_BPS/1e4·price (THR_BPS=1.6
≈$10 on BTC), CAP=0.99, per-window notional cap $5, bankroll $50.

## Ground rules
- **Causal only** — window i may use only windows < i for de-bias/variance. No look-ahead.
- Write your script to `backtest/strategy_search/agentN_<name>.py`; import `fees.py`.
- Report headline numbers under MAKER fees: net ¢/contract, win%, #windows, #losers,
  contracts, est $/day, AND a **left-tail metric** (worst-window PnL and 5% CVaR),
  plus in-sample vs out-of-sample (split at median close_dt).
