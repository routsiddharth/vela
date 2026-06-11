"""TWAP-vs-spot divergence FADE backtest.

Idea: settlement is a 60-sample average over the final 60s. After a late spot move,
naive traders chase spot (last price) and push the Kalshi price toward the new spot,
but the AVERAGE is anchored by samples already locked. So the market may OVER-react
relative to where the TWAP will settle. FADE that: when the model's TWAP-conditional
fair value disagrees with the market-implied prob, BET THE TWAP SIDE at the real ask.

We compute, at each decision time tau and for each window:
  * model p_yes  (causal; fade_lib.model_pwin)
  * market-implied prob & the actual taker ask for each side (causal; fade_lib.market_price_at_tau)
Then a trade rule: pick the TWAP-favored side (model prob > 0.5 -> YES else NO);
the EDGE = model_prob_of_that_side - market_price_of_that_side (i.e. fade the gap where
the side we like is CHEAP relative to the model). Enter as a taker paying that side's ask.
Net per-contract payoff (cents): win -> (100 - ask_cents) - fee ; lose -> (-ask_cents) - fee.

We sweep thresholds on the edge and on the model confidence, split OOS by date.
"""
from __future__ import annotations
import numpy as np, pandas as pd
import backtest.btc_lib as L
import backtest.analysis.fade_lib as F

TAUS = [10, 15, 20, 30, 45]


def build_panel(tau, m, piv, trades, sigma_sec):
    """One row per tradeable window at this tau, with model + market + the fade trade outcome."""
    md = F.model_pwin(piv, m, tau, sigma_sec)
    mk = F.market_price_at_tau(trades, tau)
    df = md.join(mk, how="inner").dropna(subset=["p_yes", "mkt_yes", "yes"])

    # TWAP-favored side
    df["side"] = np.where(df["p_yes"] >= 0.5, "yes", "no")
    df["model_p_side"] = np.where(df["side"] == "yes", df["p_yes"], 1 - df["p_yes"])
    df["mkt_p_side"] = np.where(df["side"] == "yes", df["mkt_yes"], 1 - df["mkt_yes"])
    # ask to BUY the favored side as a taker
    df["ask_side"] = np.where(df["side"] == "yes", df["ask_yes"], df["ask_no"])
    df["size_side"] = np.where(df["side"] == "yes", df["size_yes"], df["size_no"])
    # if no taker print on that side in the lookback, fall back to (1 - other side mid) is not
    # available; require a real ask. Use mkt-implied if ask missing? No -> mark NaN, drop later.

    # EDGE: how much CHEAPER the favored side is in the market vs our model (positive = fade opportunity)
    df["edge"] = df["model_p_side"] - df["mkt_p_side"]

    # realized win of the favored side
    df["win"] = np.where(df["side"] == "yes", df["yes"], 1 - df["yes"]).astype(int)

    df["date"] = pd.to_datetime(df["close_dt"]).dt.date
    df["tau"] = tau
    return df


def trade_pnl(df, ask_col="ask_side"):
    """Net cents/contract for buying the favored side at the given ask, after the quadratic fee."""
    ask_cents = df[ask_col] * 100.0
    fee = F.fee_cents(df[ask_col])
    win_pnl = (100.0 - ask_cents) - fee
    lose_pnl = (-ask_cents) - fee
    return np.where(df["win"] == 1, win_pnl, lose_pnl)


def summarize(df, label=""):
    if len(df) == 0:
        return dict(label=label, n=0)
    pnl = df["net_cents"]
    return dict(
        label=label, n=len(df),
        net_mean=round(float(pnl.mean()), 3),
        net_med=round(float(pnl.median()), 3),
        win_rate=round(float(df["win"].mean()), 4),
        avg_ask=round(float(df["ask_side"].mean()), 4),
        avg_edge=round(float(df["edge"].mean()), 4),
        avg_size=round(float(df["size_side"].mean()), 1),
        total_net=round(float(pnl.sum()), 1),
    )


def main():
    m = L.load_markets()
    piv = L.binance_matrix()
    trades = L.load_trades()
    sigma_sec = F.estimate_sigma_sec(piv)
    print(f"sigma_sec = {sigma_sec:.3f}")

    panels = {}
    for tau in TAUS:
        df = build_panel(tau, m, piv, trades, sigma_sec)
        df = df[df["ask_side"].notna()].copy()
        df["net_cents"] = trade_pnl(df)
        panels[tau] = df
        print(f"tau={tau}: {len(df)} tradeable windows (with a real ask on the favored side)")

    # OOS split by date (first half choose, second half validate)
    all_dates = sorted(m.assign(d=pd.to_datetime(m["close_dt"]).dt.date)["d"].unique())
    split = all_dates[len(all_dates) // 2]
    print(f"\nOOS split date = {split} ({len(all_dates)} dates total)\n")

    edge_thresholds = [0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.2]
    conf_thresholds = [0.5, 0.7, 0.85, 0.95, 0.99]

    print("=== FULL-SAMPLE sweep (net mean cents/contract) ===")
    rows = []
    for tau in TAUS:
        df = panels[tau]
        for et in edge_thresholds:
            for ct in conf_thresholds:
                sel = df[(df["edge"] >= et) & (df["model_p_side"] >= ct)]
                s = summarize(sel)
                s.update(tau=tau, edge_thr=et, conf_thr=ct)
                rows.append(s)
    res = pd.DataFrame(rows)
    res = res[res["n"] >= 30]  # need a meaningful sample
    res_sorted = res.sort_values("net_mean", ascending=False)
    pd.set_option("display.width", 200)
    print(res_sorted.head(25)[["tau", "edge_thr", "conf_thr", "n", "net_mean", "win_rate", "avg_ask", "avg_edge", "avg_size"]].to_string(index=False))

    # In-sample optimize on first half, report OOS on second half for the same cell.
    print("\n=== IN-SAMPLE (1st half) pick -> OUT-OF-SAMPLE (2nd half) ===")
    oos_rows = []
    for tau in TAUS:
        df = panels[tau]
        ins = df[df["date"] < split]
        oos = df[df["date"] >= split]
        for et in edge_thresholds:
            for ct in conf_thresholds:
                si = ins[(ins["edge"] >= et) & (ins["model_p_side"] >= ct)]
                so = oos[(oos["edge"] >= et) & (oos["model_p_side"] >= ct)]
                if len(si) < 20 or len(so) < 20:
                    continue
                oos_rows.append(dict(
                    tau=tau, edge_thr=et, conf_thr=ct,
                    is_n=len(si), is_net=round(float(si["net_cents"].mean()), 3), is_win=round(float(si["win"].mean()), 4),
                    oos_n=len(so), oos_net=round(float(so["net_cents"].mean()), 3), oos_win=round(float(so["win"].mean()), 4),
                ))
    od = pd.DataFrame(oos_rows)
    # Choose the best by IN-SAMPLE net, then look at its OOS.
    od_is = od.sort_values("is_net", ascending=False)
    print("Top 15 by IN-SAMPLE net, with their OOS result:")
    print(od_is.head(15).to_string(index=False))

    # Also: best cells that are positive in BOTH halves
    both = od[(od["is_net"] > 0) & (od["oos_net"] > 0)].sort_values("oos_net", ascending=False)
    print(f"\nCells positive in BOTH halves: {len(both)}")
    if len(both):
        print(both.head(15).to_string(index=False))

    return panels, res, od


if __name__ == "__main__":
    main()
