"""Shared backtest library for the BTC TWAP-endgame strategy.

Data (in backtest/data/):
  markets.parquet : one row per settled window
    ticker, open_time, close_time, close_dt, strike, true_settle, result(yes/no),
    volume_fp, margin (= true_settle - strike; YES iff margin>=0)
  binance_1s.parquet : Binance BTCUSDT 1s closes, final 300s of each window
    ticker, sec_to_close (1..300), price
  trades.parquet : Kalshi executed trades, final 180s of a 2500-window sample
    ticker, created_time, sec_to_close, yes_price, no_price, size, taker_side

KEY FACTS (confirmed live, 2026-06-09):
  * Settlement S = average of 60 CF-Benchmarks RTI samples over the FINAL 60s.
  * Strike K = the prior window's settlement (struck ATM at open).
  * Outcome YES iff S >= K.  Binance is a PROXY for CF-Benchmarks RTI.
  * Binance BTCUSDT runs a fairly stable HIGH bias vs RTI (~$14, USDT/USD basis
    + exchange mix). The de-bias MUST be causal: estimate it from prior settled
    windows only (never the window's own outcome).

Decision convention: at `tau` seconds-to-close we know all Binance 1s prices with
sec_to_close >= tau. The 60 settlement samples sit at sec_to_close in [1,60].
"""
from __future__ import annotations
import numpy as np, pandas as pd
from pathlib import Path

D = Path(__file__).resolve().parent / "data"

def load_markets():
    m = pd.read_parquet(D/"markets.parquet").sort_values("close_dt").reset_index(drop=True)
    m["yes"] = (m["margin"] >= 0).astype(int)
    return m

def load_binance():
    return pd.read_parquet(D/"binance_1s.parquet")

def load_trades():
    return pd.read_parquet(D/"trades.parquet")

def binance_matrix():
    """Return (tickers, P) where P[i, s] = price for ticker i at sec_to_close s (1..300).
    NaN where missing; forward/back filled lightly along seconds."""
    b = load_binance()
    piv = b.pivot_table(index="ticker", columns="sec_to_close", values="price", aggfunc="last")
    piv = piv.reindex(columns=range(1, 301))
    # fill small gaps along the second axis
    piv = piv.interpolate(axis=1, limit=5, limit_direction="both")
    return piv

def raw_avg60(piv):
    """Binance simple mean over settlement seconds 1..60 (the proxy for true settle)."""
    return piv[list(range(1, 61))].mean(axis=1)

def causal_bias(m, raw60, lookback=96):
    """Causal de-bias delta_i = trailing median of (raw_avg60 - true_settle) over the
    previous `lookback` settled windows (default 96 = 24h). Returns a Series aligned to m.index."""
    df = m[["ticker","true_settle"]].copy()
    df["raw60"] = df["ticker"].map(raw60)
    df["err"] = df["raw60"] - df["true_settle"]          # known only AFTER the window settles
    # trailing median of PRIOR windows (shift 1 so window i uses only <i)
    df["delta"] = df["err"].shift(1).rolling(lookback, min_periods=20).median()
    return df["delta"]

def estimate(piv, tau, delta):
    """Causal point estimate of settlement S at `tau` seconds-to-close.
       Locked settlement samples: sec in [tau,60] (if tau<=60). Remaining: sec in [1,tau).
       Forecast remaining at current de-biased spot (martingale). Returns Series of S_hat (RTI units).
       `delta` is a Series aligned to piv.index (Binance-minus-RTI bias)."""
    secs = piv.columns
    if tau <= 60:
        locked_cols = [s for s in range(tau, 61)]
        n_lock = len(locked_cols)
        n_rem = 60 - n_lock
        locked_sum = piv[locked_cols].sum(axis=1)
        spot_now = piv[tau]                       # current price at decision
        s_hat_binance = (locked_sum + n_rem*spot_now)/60.0
    else:
        spot_now = piv[tau]
        s_hat_binance = spot_now                  # no samples yet; martingale forecast
    return s_hat_binance - delta                  # subtract Binance bias -> RTI estimate
