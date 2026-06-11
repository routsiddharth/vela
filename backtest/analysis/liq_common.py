"""Common infrastructure for the cheap-fill (liquidity) analysis.

Goal: build, per window and per decision-time tau, a CAUSAL model estimate of the
winning side (yes/no), then look at the FULL distribution of traded prices for the
winning side in the final seconds — focusing on whether the winning side ever
traded CHEAP (<=0.95, <=0.97) and how much SIZE was takeable at those prices.

All signals here are causal:
  * estimate(tau)   uses only Binance prices at sec_to_close >= tau
  * causal_bias     uses only prior settled windows
  * "winning side"  is the model's PREDICTED winner (sign of mhat), validated
                    against the realized outcome only for win-rate accounting.

A "cheap takeable fill of the winning side" = a trade where the TAKER bought the
(model-predicted) winning side at price p:
  - predicted winner = yes  -> trade has taker_side=='yes', price = yes_price
  - predicted winner = no   -> trade has taker_side=='no',  price = no_price
Such a trade is a real fill we could have JOINED as a taker (lift the same offer)
because someone else already lifted it at that price (the offer existed).
"""
from __future__ import annotations
import numpy as np, pandas as pd
import backtest.btc_lib as L

TAUS = [15, 30, 45, 60]

_TRADES = None
def load_trades_cached():
    global _TRADES
    if _TRADES is None:
        _TRADES = L.load_trades()
    return _TRADES


def build_model(taus=TAUS):
    """Return a DataFrame indexed by ticker with, for each tau:
        mhat_{tau}  = S_hat - strike   (model margin estimate, RTI units; sign=predicted winner)
    plus realized: yes (1/0 outcome), strike, true_settle, close_dt, margin, volume_fp.
    Causal: estimate uses Binance>=tau, delta uses prior windows only.
    """
    m = L.load_markets()
    piv = L.binance_matrix()
    raw60 = L.raw_avg60(piv)
    delta = L.causal_bias(m, raw60)            # aligned to m.index
    # map delta onto piv index (ticker)
    m_idx = m.set_index("ticker")
    delta_by_ticker = pd.Series(delta.values, index=m["ticker"])

    out = m_idx[["close_dt", "strike", "true_settle", "margin", "yes", "volume_fp"]].copy()
    # align piv to the markets we have
    common = [t for t in out.index if t in piv.index]
    out = out.loc[common]
    piv = piv.loc[common]
    d = delta_by_ticker.reindex(common)

    for tau in taus:
        s_hat = L.estimate(piv, tau, d)        # Series indexed by ticker
        out[f"mhat_{tau}"] = s_hat - out["strike"]
    out["delta"] = d
    return out.dropna(subset=[f"mhat_{t}" for t in taus])


def winning_side_fills(trades, model, tau, conf_thresh, win_lo=0.0, win_hi=None):
    """For each trade within [tau-? ] we use a decision WINDOW: trades occurring at
    sec_to_close in (0, tau] are 'after the decision at tau' (we decided at tau and
    can act on any offer from then to close). We take the model's PREDICTED winner
    from mhat_{tau} and keep only trades where the TAKER bought that side.

    Returns a DataFrame of takeable winning-side fills with columns:
        ticker, sec_to_close, price (winning-side fill price), size, pred_yes,
        won (1 if predicted winner actually won), mhat, close_dt
    Only windows with |mhat_tau| >= conf_thresh are included (model confident).
    """
    mh = f"mhat_{tau}"
    mdl = model[[mh, "yes", "close_dt"]].copy()
    mdl = mdl[mdl[mh].abs() >= conf_thresh]
    mdl["pred_yes"] = (mdl[mh] >= 0).astype(int)
    mdl["won"] = (mdl["pred_yes"] == mdl["yes"]).astype(int)

    t = trades[trades.ticker.isin(mdl.index)].copy()
    # decision at tau: act on offers from tau down to close
    t = t[t.sec_to_close <= tau]
    t = t.join(mdl, on="ticker")
    t = t.dropna(subset=[mh])
    # keep trades where the TAKER lifted the predicted winning side
    pred_yes = t["pred_yes"] == 1
    take_yes = (t["taker_side"] == "yes") & pred_yes
    take_no  = (t["taker_side"] == "no") & (~pred_yes)
    keep = take_yes | take_no
    t = t[keep].copy()
    t["price"] = np.where(t["pred_yes"] == 1, t["yes_price"], t["no_price"])
    return t[["ticker", "sec_to_close", "price", "size", "pred_yes",
              "won", mh, "close_dt"]].rename(columns={mh: "mhat"})


def fee_cents(p):
    """Quadratic Kalshi fee per contract in CENTS: round_up_to_cent(0.07*p*(1-p)),
    min 1 cent. p in [0,1]. Vectorized."""
    p = np.asarray(p, dtype=float)
    raw_dollars = 0.07 * p * (1.0 - p)
    cents = np.ceil(raw_dollars * 100.0 - 1e-9)   # round up to whole cent
    cents = np.maximum(cents, 1.0)
    return cents
