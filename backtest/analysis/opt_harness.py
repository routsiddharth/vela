"""Shared evaluator for optimizing the CURRENT strategy (confidence-gated panic
fade) over its parameters. One function evaluate(**params) -> metrics, with an
OOS split (train H1 / test H2) and per-month breakdown so the swarm can't overfit.

Strategy replicated (matches livepaper/engine.py):
  At TAU, p_side = norm_cdf(|margin_hat| / sd_S) where margin_hat = de-biased TWAP
  estimate - strike and sd_S = sqrt(diffusion_var + proxy_sd^2) (fade_lib.model_pwin).
  Gate: p_side >= P_SIDE_MIN. Bet the favored side. Over trades in [SEC_LO,SEC_HI],
  fill the winning-side print at win_px iff FLOOR <= win_px <= CAP and p_side > win_px
  (the EV guard; the Q-blend is algebraically inert -> drop it). Size NOTIONAL$/window
  (=PORTFOLIO_FRACTION*BANK, capped), integer contracts, maker fee per order. Hold to
  settle. Win -> qty*(1-px); lose -> -qty*px.

Data: BTC KXBTC15M only (the backtest set). trades.parquet = final 180s of a
2500/6308 sample, so $/day is scaled by SAMPLE_FRAC and the period length.

Run a baseline + tiny grid:  python -m backtest.analysis.opt_harness
"""
from __future__ import annotations
import math
import numpy as np, pandas as pd
import backtest.btc_lib as L
from backtest.analysis import fade_lib as F

SAMPLE_FRAC = 2500 / 6308
BANK = 50.0

_M = L.load_markets()
_PIV = L.binance_matrix()
_M = _M[_M.ticker.isin(_PIV.index)].reset_index(drop=True)
_SIGMA = F.estimate_sigma_sec(_PIV)
_TR = L.load_trades()
DAYS = (_M.close_dt.max() - _M.close_dt.min()).days
_MID = _M.close_dt.quantile(0.5)
_MODEL_CACHE: dict[int, pd.DataFrame] = {}


def _model(tau: int) -> pd.DataFrame:
    if tau not in _MODEL_CACHE:
        pw = F.model_pwin(_PIV, _M, tau, _SIGMA)        # idx=ticker: mhat,margin_hat,sd_S,p_yes,yes,close_dt
        pw = pw.reset_index()
        pw["bet_yes"] = pw.margin_hat > 0
        pw["p_side"] = np.where(pw.bet_yes, pw.p_yes, 1 - pw.p_yes)
        _MODEL_CACHE[tau] = pw
    return _MODEL_CACHE[tau]


def maker_fee(qty: float, p: float) -> float:
    return math.ceil(0.0175 * qty * p * (1 - p) * 100) / 100.0


def window_pnl(P_SIDE_MIN=0.99, FLOOR=0.55, CAP=0.99, SEC_LO=1, SEC_HI=45,
               TAU=45, NOTIONAL=5.0):
    """Return the per-window pnl DataFrame (ticker, close_dt, px, qty, won, pnl)
    for custom splits / bootstrap, or None."""
    pw = _model(TAU)
    armed = pw[pw.p_side >= P_SIDE_MIN]
    if armed.empty:
        return None
    d = _TR.merge(armed[["ticker", "bet_yes", "p_side", "yes", "close_dt"]],
                  on="ticker", how="inner")
    d = d[(d.sec_to_close >= SEC_LO) & (d.sec_to_close <= SEC_HI)]
    d["win_px"] = np.where(d.bet_yes, d.yes_price, d.no_price)
    d = d[(d.win_px >= FLOOR) & (d.win_px <= CAP) & (d.p_side > d.win_px) & (d.win_px > 0)]
    if d.empty:
        return None
    d["won"] = np.where(d.bet_yes, d.yes == 1, d.yes == 0)
    rows = []
    for tk, g in d.groupby("ticker"):
        avail = g["size"].sum()
        px = float((g.win_px * g["size"]).sum() / avail)
        won = bool(g.won.iloc[0])
        qty = min(avail, max(1.0, round(NOTIONAL / px)))
        pnl = qty * ((1 - px) if won else -px) - maker_fee(qty, px)
        rows.append((tk, g.close_dt.iloc[0], px, qty, won, pnl))
    return pd.DataFrame(rows, columns=["ticker", "close_dt", "px", "qty", "won", "pnl"])


def evaluate(P_SIDE_MIN=0.99, FLOOR=0.55, CAP=0.99, SEC_LO=1, SEC_HI=45,
             TAU=45, NOTIONAL=5.0):
    pw = _model(TAU)
    armed = pw[pw.p_side >= P_SIDE_MIN]
    if armed.empty:
        return None
    d = _TR.merge(armed[["ticker", "bet_yes", "p_side", "yes", "close_dt"]],
                  on="ticker", how="inner")
    d = d[(d.sec_to_close >= SEC_LO) & (d.sec_to_close <= SEC_HI)]
    d["win_px"] = np.where(d.bet_yes, d.yes_price, d.no_price)
    d = d[(d.win_px >= FLOOR) & (d.win_px <= CAP) & (d.p_side > d.win_px) & (d.win_px > 0)]
    if d.empty:
        return None
    d["won"] = np.where(d.bet_yes, d.yes == 1, d.yes == 0)

    # per-window sizing: NOTIONAL$ at the size-weighted avg fill price, integer ct,
    # capped by available printed size.
    rows = []
    for tk, g in d.groupby("ticker"):
        avail = g["size"].sum()
        px = float((g.win_px * g["size"]).sum() / avail)
        won = bool(g.won.iloc[0])
        qty = min(avail, max(1.0, round(NOTIONAL / px)))
        pnl = qty * ((1 - px) if won else -px) - maker_fee(qty, px)
        rows.append((tk, g.close_dt.iloc[0], px, qty, won, pnl))
    w = pd.DataFrame(rows, columns=["ticker", "close_dt", "px", "qty", "won", "pnl"])

    def stats(x):
        if x.empty:
            return dict(usd_day=0.0, n=0, ct=0.0, winpct=0.0, net_c=0.0)
        ct = x.qty.sum()
        return dict(usd_day=x.pnl.sum() / SAMPLE_FRAC / DAYS, n=len(x), ct=ct,
                    winpct=100 * (x.won * x.qty).sum() / ct,
                    net_c=100 * x.pnl.sum() / ct,
                    worst=x.pnl.min())

    full = stats(w)
    h1 = stats(w[w.close_dt < _MID])
    h2 = stats(w[w.close_dt >= _MID])
    w["month"] = pd.to_datetime(w.close_dt, utc=True).dt.to_period("M").astype(str)
    months = {m: stats(g)["usd_day"] for m, g in w.groupby("month")}
    return dict(usd_day=full["usd_day"], windows=full["n"], contracts=full["ct"],
                winpct=full["winpct"], net_c=full["net_c"], worst=full.get("worst", 0.0),
                oos_usd_day=h2["usd_day"], is_usd_day=h1["usd_day"],
                months=months, min_month=min(months.values()) if months else 0.0)


if __name__ == "__main__":
    print(f"period {DAYS} days, sample frac {SAMPLE_FRAC:.3f}\n")
    base = evaluate()  # current live config
    print("CURRENT live config (P_SIDE_MIN=0.99, FLOOR=0.55, CAP=0.99, SEC=[1,45], TAU=45):")
    for k, v in base.items():
        print(f"   {k}: {v}")
    print("\nquick sensitivity on P_SIDE_MIN (others fixed):")
    print(f"{'p_min':>6} {'$/day':>7} {'OOS$/d':>7} {'minMo':>7} {'win%':>6} {'net_c':>7} {'wins':>5}")
    for pm in (0.999, 0.99, 0.98, 0.97, 0.95, 0.92, 0.90):
        r = evaluate(P_SIDE_MIN=pm)
        if r:
            print(f"{pm:>6} {r['usd_day']:>7.2f} {r['oos_usd_day']:>7.2f} "
                  f"{r['min_month']:>7.2f} {r['winpct']:>6.1f} {r['net_c']:>7.2f} {r['windows']:>5}")
