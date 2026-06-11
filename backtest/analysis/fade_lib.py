"""Shared helpers for the TWAP-vs-spot divergence / fade backtest.

Model (all CAUSAL):
  At decision time tau (seconds-to-close), settlement S = mean of 60 RTI samples at
  sec_to_close in [1,60]. Samples at sec_to_close in [tau,60] are LOCKED (observed).
  Samples at sec_to_close in [1,tau) are unrealized.

  mhat = btc_lib.estimate(piv, tau, delta)  -> de-biased point estimate of S (RTI units).

  To turn mhat into P(YES)=P(S>=K) we add the diffusion variance of the remaining samples.
  Let spot_tau be the de-biased spot at tau. The remaining sample at future sec-to-close k
  (k = tau-1, tau-2, ..., 1) is observed (60-tau)+? steps in the future... we index by how many
  seconds AHEAD of tau it is collected: the sample collected at sec_to_close c (c in [1,tau-1])
  is collected at time (tau - c) seconds AFTER the decision. Its price ~ spot_tau + sqrt(tau-c)*sigma*Z.
  S = (locked_sum + sum_{c=1}^{tau-1} X_c)/60, locked_sum known.
  Mean(S) = mhat (already the martingale point estimate).
  Var(S) = (1/60^2) * Var(sum_c X_c). The X_c share the SAME Brownian path, so
    Cov(X_a, X_b) = sigma^2 * min(tau-a, tau-b).
  Var(sum) = sigma^2 * sum_{a,b} min(tau-a, tau-b). With lags l = tau-c in {1,...,tau-1}.
  Let l_c = tau - c, c=1..tau-1 -> lags {1,2,...,tau-1}.
  Var(sum_l X) where each X_l = spot + sigma*W(l), W Brownian motion sampled at integer lags.
  Cov(X_li, X_lj) = sigma^2 * min(li, lj). So Var(sum) = sigma^2 * sum_i sum_j min(li,lj).
"""
from __future__ import annotations
import numpy as np, pandas as pd
from scipy.stats import norm
import backtest.btc_lib as L


def remaining_var_factor(tau: int) -> float:
    """sum_{i,j in lags} min(li,lj) for lags = {1,...,tau-1}, divided by 60^2.
    Var(S) = sigma_sec^2 * remaining_var_factor(tau)."""
    if tau <= 1:
        return 0.0
    lags = np.arange(1, tau)  # 1..tau-1
    M = np.minimum.outer(lags, lags)
    return M.sum() / (60.0 ** 2)


def estimate_sigma_sec(piv, lo=1, hi=120) -> float:
    """Per-second price-step std (USD) from Binance 1s closes over the final window."""
    sub = piv[list(range(lo, hi + 1))]
    d = sub.diff(axis=1).values.flatten()
    d = d[~np.isnan(d)]
    return float(np.std(d))


def model_pwin(piv, m, tau, sigma_sec, proxy_lookback=96):
    """Return DataFrame indexed by ticker (ordered like m) with: mhat (de-biased settle
    est), strike, margin_hat (mhat-K), sd_S, p_yes (model prob YES), yes (outcome).

    sd_S combines TWO causal variance sources:
      * diffusion of the remaining (tau-1) samples: sigma_sec^2 * remaining_var_factor(tau)
      * proxy / de-bias tracking error: causal trailing std of (mhat - true_settle) over the
        previous `proxy_lookback` settled windows (the Binance-vs-RTI residual; dominant term).
    All causal: window i uses only windows < i for the proxy std.
    """
    raw60 = L.raw_avg60(piv)
    delta = L.causal_bias(m, raw60)  # aligned to m.index
    d_by_ticker = pd.Series(delta.values, index=m["ticker"].values)
    delta_piv = pd.Series(piv.index.map(d_by_ticker), index=piv.index)
    mhat_series = L.estimate(piv, tau, delta_piv)  # indexed by ticker

    var_factor = remaining_var_factor(tau)
    diff_var = sigma_sec ** 2 * var_factor

    # Build a frame in m's chronological order (m is sorted by close_dt) for the causal proxy std.
    out = m[["ticker", "strike", "yes", "true_settle", "close_dt"]].copy()
    out["mhat"] = out["ticker"].map(mhat_series)
    out["margin_hat"] = out["mhat"] - out["strike"]
    # causal proxy variance: trailing std of past (mhat - true_settle)
    err = (out["mhat"] - out["true_settle"])
    proxy_sd = err.shift(1).rolling(proxy_lookback, min_periods=20).std()
    proxy_sd = proxy_sd.fillna(err.expanding().std().shift(1)).fillna(10.0)
    out["proxy_sd"] = proxy_sd.values
    out["sd_S"] = np.sqrt(diff_var + out["proxy_sd"] ** 2)
    out["p_yes"] = norm.cdf(out["margin_hat"] / out["sd_S"].clip(lower=1e-6))
    out = out.set_index("ticker")
    return out


def market_price_at_tau(trades, tau, window=8.0):
    """Causal market-implied prob at decision time tau for each ticker.

    Returns DataFrame indexed by ticker with:
      mkt_yes      : VWAP-ish last yes_price using trades with sec_to_close in [tau, tau+window]
                     (i.e. trades AT or just BEFORE the decision -> causal, no future)
      ask_yes      : price to BUY YES as a taker (most recent trade where taker_side=='yes')
      ask_no       : price to BUY NO  as a taker (most recent trade where taker_side=='no')
      n            : number of trades used
    The ask is the actual fill a taker would have paid for that side.
    """
    t = trades[(trades["sec_to_close"] >= tau) & (trades["sec_to_close"] <= tau + window)].copy()
    # 'most recent' = smallest sec_to_close within the lookback window (closest to tau, still >= tau)
    t = t.sort_values("sec_to_close")  # ascending: first row = closest to close but we need >= tau
    rows = {}
    for tk, g in t.groupby("ticker"):
        g = g.sort_values("sec_to_close")  # nearest to tau is the smallest sec_to_close >= tau
        last = g.iloc[0]
        mkt_yes = float(last["yes_price"])
        gy = g[g["taker_side"] == "yes"]
        gn = g[g["taker_side"] == "no"]
        ask_yes = float(gy.iloc[0]["yes_price"]) if len(gy) else np.nan
        ask_no = float(gn.iloc[0]["no_price"]) if len(gn) else np.nan
        # available size at that ask (sum of size at the nearest sec stamp for that side)
        size_yes = float(gy["size"].sum()) if len(gy) else 0.0
        size_no = float(gn["size"].sum()) if len(gn) else 0.0
        rows[tk] = (mkt_yes, ask_yes, ask_no, size_yes, size_no, len(g))
    df = pd.DataFrame.from_dict(rows, orient="index",
                                columns=["mkt_yes", "ask_yes", "ask_no", "size_yes", "size_no", "n"])
    return df


def fee_cents(p):
    """Kalshi quadratic fee per contract in CENTS: round_up_to_cent(0.07*p*(1-p)) with min 1c.
    p is the trade price in [0,1]. Returns cents (>=1)."""
    p = np.asarray(p, dtype=float)
    raw_dollars = 0.07 * p * (1.0 - p)
    cents = np.ceil(raw_dollars * 100.0)
    cents = np.maximum(cents, 1.0)
    return cents
