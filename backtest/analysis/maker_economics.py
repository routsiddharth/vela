"""Maker-angle backtest for the BTC 15-min near-lock trade.

Question: can we be a MAKER (resting limit BUY on the model-favored side) and net
positive after the REAL Kalshi maker fee AND adverse selection?

Causality: the only signal is btc_lib.estimate() with causal_bias() (trailing-24h
median de-bias, shift(1)). No window uses its own outcome or future windows.

Fee model (multi-source converged, see findings/maker_economics.md):
  taker fee/contract = ceil(0.07 * P * (1-P) * 100)/100   (round UP to cent)
  maker fee/contract = ceil(0.0175 * P * (1-P) * 100)/100  (1/4 rate, round UP)
  Crypto 15-min markets ARE in the fee-bearing subset (maker fee applies).
"""
from __future__ import annotations
import math
import numpy as np, pandas as pd
import backtest.btc_lib as L

CENT = 0.01

def taker_fee(p):
    return math.ceil(0.07 * p * (1 - p) * 100) / 100

def maker_fee(p):
    return math.ceil(0.0175 * p * (1 - p) * 100) / 100

def maker_fee_floor0(p):
    """Hypothetical: if Kalshi rounded maker fee to NEAREST (or floored) instead of up."""
    raw = 0.0175 * p * (1 - p)
    return round(raw, 2)  # nearest cent -> 0 for raw<0.005


def build_model():
    m = L.load_markets()
    piv = L.binance_matrix()
    raw60 = L.raw_avg60(piv)
    delta = L.causal_bias(m, raw60)          # aligned to m.index
    m = m.assign(delta=delta.values)
    # delta keyed by ticker for piv-index alignment
    delta_by_ticker = pd.Series(m["delta"].values, index=m["ticker"].values)
    return m, piv, delta_by_ticker


def model_signal(m, piv, delta_by_ticker, tau):
    """Return DataFrame indexed by ticker: s_hat, strike, mhat (=s_hat-strike),
    side (yes if mhat>=0 else no), outcome (1 yes won), valid (has delta)."""
    delta_aligned = pd.Series(piv.index, index=piv.index).map(delta_by_ticker)
    s_hat = L.estimate(piv, tau, delta_aligned)  # Series indexed by ticker
    df = pd.DataFrame({"s_hat": s_hat})
    mm = m.set_index("ticker")
    df["strike"] = mm["strike"].reindex(df.index)
    df["yes_won"] = mm["yes"].reindex(df.index)
    df["mhat"] = df["s_hat"] - df["strike"]
    df["side"] = np.where(df["mhat"] >= 0, "yes", "no")
    df["valid"] = df["s_hat"].notna() & df["strike"].notna() & df["yes_won"].notna()
    return df


def trade_window_stats(trades, tau_lo, tau_hi):
    """For each ticker, summarize executed trades in window (tau_lo, tau_hi] sec-to-close.
    Returns per-ticker: min yes_price reached, min no_price reached, and per-price-level
    we'll compute fill separately. Here just the extremes for quick fill checks."""
    w = trades[(trades["sec_to_close"] > tau_lo) & (trades["sec_to_close"] <= tau_hi)]
    g = w.groupby("ticker")
    out = pd.DataFrame({
        "yes_min": g["yes_price"].min(),   # cheapest YES traded -> a resting YES bid below this fills
        "no_min": g["no_price"].min(),
        "n_trades": g.size(),
        "vol": g["size"].sum(),
    })
    return out, w


def main():
    m, piv, delta_by_ticker = build_model()
    trades = L.load_trades()
    trade_tickers = set(trades["ticker"].unique())

    # Fee demonstration table
    print("=== FEE TABLE (per contract) ===")
    print(f"{'P':>6} {'taker':>8} {'maker(up)':>10} {'maker_raw':>10} {'maker(near)':>11}")
    for p in [0.50, 0.80, 0.90, 0.95, 0.97, 0.98, 0.99, 0.995]:
        print(f"{p:6.3f} {taker_fee(p)*100:7.2f}c {maker_fee(p)*100:9.2f}c "
              f"{0.0175*p*(1-p)*100:9.4f}c {maker_fee_floor0(p)*100:10.2f}c")
    print()

    # ---- Adverse selection + fill analysis ----
    # Decision at tau; rest a BUY (bid) on the model-favored side.
    # We rest at entry price e (in cents of the favored side's price). The order fills
    # iff the favored side trades at <= e during the resting window (tau, 0].
    # We measure: fill rate, win rate | fill, unconditional win rate, net EV.
    TAUS = [120, 90, 60, 45, 30]
    THRESH = [50, 75, 100, 150]
    # entry as a discount below the side's price-at-tau? Simpler & robust: rest at fixed
    # price levels e in cents. A BUY of the favored side at price e pays e, wins -> +(1-e), fee.
    ENTRY = [0.90, 0.93, 0.95, 0.97, 0.98, 0.99]

    rows = []
    for tau in TAUS:
        sig = model_signal(m, piv, delta_by_ticker, tau)
        sig = sig[sig["valid"]]
        # restrict to windows we have trades for (the fill-realism sample)
        sig = sig[sig.index.isin(trade_tickers)]
        # resting window: from tau down to close. Use trades with sec_to_close <= tau.
        rest = trades[trades["sec_to_close"] <= tau]
        g = rest.groupby("ticker")
        yes_min = g["yes_price"].min()
        no_min = g["no_price"].min()
        # also last traded price of favored side near close for "no-edge" sanity
        for thr in THRESH:
            cand = sig[sig["mhat"].abs() > thr].copy()
            if len(cand) == 0:
                continue
            # favored side's min traded price during resting window
            cand["fav_min"] = np.where(
                cand["side"] == "yes",
                yes_min.reindex(cand.index).values,
                no_min.reindex(cand.index).values,
            )
            # win flag: did the favored side actually win?
            cand["fav_won"] = np.where(
                cand["side"] == "yes", cand["yes_won"], 1 - cand["yes_won"]
            )
            n_cand = len(cand)
            uncond_win = cand["fav_won"].mean()
            for e in ENTRY:
                filled = cand[cand["fav_min"] <= e]
                nf = len(filled)
                if nf == 0:
                    rows.append(dict(tau=tau, thr=thr, entry=e, n_cand=n_cand,
                                     fill_rate=0.0, n_fill=0, win_uncond=uncond_win,
                                     win_fill=np.nan, ev_maker=np.nan, ev_taker=np.nan))
                    continue
                win_fill = filled["fav_won"].mean()
                # EV per filled contract (buy favored side at e):
                #   win: +(1-e) - fee ; lose: -e - fee
                mf = maker_fee(e)
                tf = taker_fee(e)
                ev_maker = win_fill * (1 - e) - (1 - win_fill) * e - mf
                ev_taker = win_fill * (1 - e) - (1 - win_fill) * e - tf
                # also EV if maker fee floored to 0 (best-case rebate-ish scenario)
                ev_maker0 = win_fill * (1 - e) - (1 - win_fill) * e - maker_fee_floor0(e)
                rows.append(dict(tau=tau, thr=thr, entry=e, n_cand=n_cand,
                                 fill_rate=nf / n_cand, n_fill=nf,
                                 win_uncond=uncond_win, win_fill=win_fill,
                                 ev_maker=ev_maker, ev_taker=ev_taker,
                                 ev_maker0=ev_maker0))
    res = pd.DataFrame(rows)
    pd.set_option("display.width", 200, "display.max_rows", 400)
    print("=== MAKER FILL + ADVERSE SELECTION + EV (cents/contract) ===")
    show = res.copy()
    for c in ["ev_maker", "ev_taker", "ev_maker0"]:
        show[c] = (show[c] * 100).round(2)
    show["fill_rate"] = (show["fill_rate"] * 100).round(1)
    show["win_uncond"] = (show["win_uncond"] * 100).round(2)
    show["win_fill"] = (show["win_fill"] * 100).round(2)
    print(show.to_string(index=False))

    # Best positive maker cell with meaningful volume
    ok = res[(res["n_fill"] >= 50)]
    print("\n=== BEST MAKER CELLS (n_fill>=50), by ev_maker ===")
    best = ok.sort_values("ev_maker", ascending=False).head(12)
    b = best.copy()
    for c in ["ev_maker", "ev_taker", "ev_maker0"]:
        b[c] = (b[c] * 100).round(3)
    b["fill_rate"] = (b["fill_rate"] * 100).round(1)
    b["win_uncond"] = (b["win_uncond"] * 100).round(2)
    b["win_fill"] = (b["win_fill"] * 100).round(2)
    print(b[["tau", "thr", "entry", "n_cand", "n_fill", "fill_rate",
             "win_uncond", "win_fill", "ev_maker", "ev_maker0", "ev_taker"]].to_string(index=False))

    res.to_csv("backtest/analysis/maker_results.csv", index=False)
    print("\nwrote backtest/analysis/maker_results.csv")


if __name__ == "__main__":
    main()
