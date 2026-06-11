"""Hit-rate vs edge frontier for the panic-fade, swept over (THR, CAP).

Grounds the 'how lenient?' decision in data: for each gate threshold THR (USD
margin) and price cap CAP, over the 2500-window trade sample, report
  - hit_rate : fraction of ALL windows that produce >=1 fill  ('1 in N intervals')
  - armed%   : fraction of windows that pass the |mhat|>=THR gate
  - net c/ct : net cents per contract after Kalshi fees
  - win%     : contract-weighted win rate
  - flips    : armed windows whose model side was WRONG (the ruin risk)

Run:  python -m backtest.analysis.leniency_sweep   (from bitcoin/, ingest venv)
"""
from __future__ import annotations
import math
import numpy as np, pandas as pd
from backtest.btc_lib import load_markets, binance_matrix, raw_avg60, causal_bias, estimate

TAU, SEC_LO, SEC_HI = 45, 5, 45


def fee(p): return max(0.01, math.ceil(0.07 * p * (1 - p) * 100) / 100)
fee_v = np.vectorize(fee)


def main() -> None:
    m = load_markets(); piv = binance_matrix()
    m = m[m.ticker.isin(piv.index)].reset_index(drop=True)
    raw60 = raw_avg60(piv); m["delta"] = causal_bias(m, raw60).values
    dser = pd.Series(m.delta.values, index=m.ticker.values)
    shat = estimate(piv.loc[m.ticker], TAU, dser)
    m["mhat"] = shat.values - m.strike.values
    m = m.dropna(subset=["delta", "mhat"])

    tr = pd.read_parquet("backtest/data/trades.parquet")
    sampled = set(tr.ticker.unique())
    ms = m[m.ticker.isin(sampled)].copy()       # windows with trade data
    N = len(ms)
    tr = tr.merge(ms[["ticker", "mhat", "yes"]], on="ticker", how="inner")
    tr = tr[(tr.sec_to_close >= SEC_LO) & (tr.sec_to_close <= SEC_HI)].copy()
    tr["bet_yes"] = tr.mhat > 0
    tr["win_px"] = np.where(tr.bet_yes, tr.yes_price, tr.no_price)
    tr["won"] = np.where(tr.bet_yes, tr.yes == 1, tr.yes == 0)

    print(f"sample windows with trades: {N}\n")
    print(f"{'THR':>4} {'CAP':>5} | {'armed%':>7} {'hit_rate':>9} {'~1 in':>6} | "
          f"{'net c/ct':>9} {'win%':>7} {'flips':>6} {'contracts':>10}")
    for THR in (50, 40, 30, 20, 10, 0):
        armed = ms[ms.mhat.abs() >= THR]
        armed_set = set(armed.ticker)
        armed_pct = 100 * len(armed) / N
        d0 = tr[tr.ticker.isin(armed_set)]
        # model flips among armed windows (side wrong at settlement)
        flips = int((armed.mhat > 0).ne(armed.yes == 1).sum())
        for CAP in (0.97, 0.98, 0.99, 0.995, 0.999):
            d = d0[(d0.win_px <= CAP) & (d0.win_px > 0)].copy()
            hit_windows = d.ticker.nunique()
            hit_rate = hit_windows / N
            if len(d):
                d["pnl_ct"] = np.where(d.won, 1 - d.win_px, -d.win_px) - fee_v(d.win_px.values)
                ct = d["size"].sum()
                net_c = (d.pnl_ct * d["size"]).sum() / ct * 100
                win_pct = (d.won * d["size"]).sum() / ct * 100
            else:
                net_c = win_pct = ct = 0.0
            one_in = (1 / hit_rate) if hit_rate else float("inf")
            print(f"{THR:>4} {CAP:>5} | {armed_pct:>6.1f}% {hit_rate:>8.1%} "
                  f"{one_in:>6.1f} | {net_c:>+9.2f} {win_pct:>7.2f} {flips:>6} {ct/1000:>9.1f}k")
        print()
    print("'~1 in N' = one fill every N windows (15-min intervals). Target: ~4.")
    print("flips = armed windows the model called wrong (each is a near-total loss).")


if __name__ == "__main__":
    main()
