# BTC TWAP-endgame backtest — findings so far (shared context for subagents)

Date: 2026-06-09. Data probed live. **Read this before starting.**

## Data available (backtest/data/, load via backtest/btc_lib.py)
- `markets.parquet` — 6,308 settled KXBTC15M windows, 2026-04-03 → 2026-06-09.
  cols: ticker, open_time, close_time, close_dt, strike, true_settle (=expiration_value,
  the realized 60-sample settlement price), result(yes/no), volume_fp, margin(=true_settle-strike),
  yes(=1 if margin>=0).
- `binance_1s.parquet` — Binance BTCUSDT 1s closes, final 300s of each window (6,303 windows).
  cols: ticker, sec_to_close(1..300), price.
- `trades.parquet` — Kalshi executed trades, final 180s, 2,500-window even sample (~2.4M rows).
  cols: ticker, created_time, sec_to_close, yes_price, no_price, size, taker_side.

`btc_lib.py` helpers: load_markets/load_binance/load_trades, binance_matrix() (ticker×sec pivot),
raw_avg60(), causal_bias() (trailing-24h median de-bias, CAUSAL), estimate(piv,tau,delta).
Run with `PYTHONPATH=$(pwd)` from bitcoin/, venv at bitcoin/venv.

## Confirmed mechanics
- Settlement S = average of 60 CF-Benchmarks RTI samples over the FINAL 60s (1 Hz). Plain mean
  per Kalshi product metadata. YES iff S >= strike. Strike = prior window's settlement (ATM).
- Fees: quadratic, fee/contract = round_up_to_cent(0.07·P·(1−P)). At tail prices (0.97-0.99)
  the raw fee is ~0.1-0.2¢ but **rounds UP to 1¢** — this is decisive.
- Outcomes ~50/50 (3175 no / 3133 yes) → sub-15-min direction is a random walk (as expected).

## What WORKS (established, high confidence)
- **Binance is a usable proxy for RTI** but carries a HIGH bias that DRIFTS a lot (weekly median
  swung −$31 → +$97 over 2 months). A static de-bias fails; a **causal trailing-24h median**
  de-bias reduces residual to mean≈0, std $10.4, |resid| q99 $32.
- **Lock detection works.** At τ=30s, betting the de-biased-estimate side, filtering |mhat|>$50:
  61% of windows tradeable, **99.97% win rate (1 flip/3848)**; |mhat|>$75 → 0 flips/2903.
  Calibration degrades gracefully earlier (τ=90 |mhat|>$100 → 99.86%).

## What FAILS (the killer — established, high confidence)
- **The edge is NOT capturable as a taker.** When the model is confident, BTC is visibly far
  from strike, so the CROWD already prices the winning side at **0.985–0.999**. Scan of every
  (τ ∈ {30,45,60,90,120}, threshold ∈ {50,75,100,150}): net EV after fee is **−0.5 to −0.95¢/
  contract in ALL cells**. The "buy 97¢ worth 99¢" gap does not exist at the confident tail; the
  1¢ fee round-up + occasional flip > the 0.1–1.5¢ gross gap. Taker near-lock = dead.

## Measurement caveats to verify (don't inherit my mistakes)
- entry price was approximated as mean traded price near τ; a taker BUYING the winning side pays
  the ask — refine using taker_side / the winning side's actual fill price.
- binance_matrix() interpolates gaps with limit_direction="both" → could leak ≤5s of future into
  col[τ]. Minor; recompute estimator without forward-fill if it matters.
- delta de-bias is causal (shift(1).rolling). Keep any new signal causal — no window uses its own
  outcome or future windows.

## Open questions for the subagents (find a strategy that actually nets positive after fees)
The taker path is dead. A viable strategy must beat the quadratic fee. Genuinely test, don't assume.
