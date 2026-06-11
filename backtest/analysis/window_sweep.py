"""How much does WIDENING THE TIME WINDOW raise the fill rate (and at what cost)?

Holds THR/CAP fixed and varies the decision time tau and the actionable seconds
[SEC_LO, SEC_HI]. Two distinct levers:
  * SEC_LO down (5->1): take fills closer to close. The outcome is MORE locked
    there, so this should add fills with ~no extra flips (only live latency cost).
  * tau/SEC_HI up (45->60/90): lock the bet earlier with FEWER samples banked ->
    more fills but more flips (the model is less sure that far out).

Run:  python -m backtest.analysis.window_sweep   (from bitcoin/, ingest venv)
"""
from __future__ import annotations
import math
import numpy as np, pandas as pd
from backtest.btc_lib import load_markets, binance_matrix, raw_avg60, causal_bias, estimate

THR, CAP = 10.0, 0.99


def fee(p): return max(0.01, math.ceil(0.07 * p * (1 - p) * 100) / 100)
fee_v = np.vectorize(fee)


def main() -> None:
    m = load_markets(); piv = binance_matrix()
    m = m[m.ticker.isin(piv.index)].reset_index(drop=True)
    raw60 = raw_avg60(piv); m["delta"] = causal_bias(m, raw60).values
    dser = pd.Series(m.delta.values, index=m.ticker.values)
    tr_all = pd.read_parquet("backtest/data/trades.parquet")
    sampled = set(tr_all.ticker.unique())

    print(f"THR=${THR:.0f}  CAP={CAP}   (2488-window trade sample; trades cover the "
          f"final ~180s)\n")
    print(f"{'tau':>4} {'window':>10} | {'armed%':>7} {'hit_rate':>9} {'~1 in':>6} | "
          f"{'net c/ct':>9} {'win%':>7} {'flips':>6}")

    def run(tau, sec_lo, sec_hi):
        shat = estimate(piv.loc[m.ticker], tau, dser)
        mm = m.copy()
        mm["mhat"] = shat.values - mm.strike.values
        mm = mm.dropna(subset=["delta", "mhat"])
        ms = mm[mm.ticker.isin(sampled)].copy()
        N = len(ms)
        armed = ms[ms.mhat.abs() >= THR]
        flips = int((armed.mhat > 0).ne(armed.yes == 1).sum())
        tr = tr_all.merge(armed[["ticker", "mhat", "yes"]], on="ticker", how="inner")
        tr = tr[(tr.sec_to_close >= sec_lo) & (tr.sec_to_close <= sec_hi)].copy()
        tr["bet_yes"] = tr.mhat > 0
        tr["win_px"] = np.where(tr.bet_yes, tr.yes_price, tr.no_price)
        tr["won"] = np.where(tr.bet_yes, tr.yes == 1, tr.yes == 0)
        d = tr[(tr.win_px <= CAP) & (tr.win_px > 0)].copy()
        hit = d.ticker.nunique()
        hr = hit / N
        if len(d):
            d["pnl"] = np.where(d.won, 1 - d.win_px, -d.win_px) - fee_v(d.win_px.values)
            ct = d["size"].sum()
            net_c = (d.pnl * d["size"]).sum() / ct * 100
            win = (d.won * d["size"]).sum() / ct * 100
        else:
            net_c = win = 0.0
        one = (1 / hr) if hr else float("inf")
        return 100 * len(armed) / N, hr, one, net_c, win, flips

    rows = [
        (45, 5, 45, "[5,45]"),     # current
        (45, 3, 45, "[3,45]"),     # SEC_LO down (nearly free)
        (45, 1, 45, "[1,45]"),     # SEC_LO=1 (last-second dumps)
        (60, 1, 60, "[1,60]"),     # decide at 60s (earlier -> more flips)
        (90, 1, 90, "[1,90]"),     # decide at 90s
        (120, 1, 120, "[1,120]"),  # decide at 120s
    ]
    for tau, lo, hi, lbl in rows:
        a, hr, one, nc, win, fl = run(tau, lo, hi)
        flag = "  <- current" if lbl == "[5,45]" else ""
        print(f"{tau:>4} {lbl:>10} | {a:>6.1f}% {hr:>8.1%} {one:>6.1f} | "
              f"{nc:>+9.2f} {win:>7.2f} {fl:>6}{flag}")
    print("\nSEC_LO down = same tau=45 lock, just take fills closer to close: more "
          "fills, flips UNCHANGED (free except live latency).")
    print("tau up = lock the bet earlier with fewer samples banked: more fills but "
          "more flips (costs edge).")


if __name__ == "__main__":
    main()
