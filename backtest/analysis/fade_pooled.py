"""Pooled + tail-aware analysis of the TWAP fade.

Pools the fade trades across all taus to gain statistical power, then:
  * characterises the payoff distribution (it is fat-tailed: cheap wins are small, a flip at a
    high ask costs ~90c),
  * sweeps on EDGE and on ASK (only fade when the favored side is genuinely CHEAP -> bounded loss),
  * bootstraps a CI on net cents/contract,
  * does the OOS (date) split on the pooled rule.

A fade only makes sense when the side we like is cheap: a loss costs ask_cents, so capping ask
caps the downside. The real overreaction cases are large-edge / lower-ask.
"""
from __future__ import annotations
import numpy as np, pandas as pd
import backtest.btc_lib as L
import backtest.analysis.fade_lib as F
import backtest.analysis.fade_backtest as B

TAUS = [10, 15, 20, 30, 45]
RNG = np.random.default_rng(7)


def pooled_panel():
    m = L.load_markets(); piv = L.binance_matrix(); trades = L.load_trades()
    sig = F.estimate_sigma_sec(piv)
    parts = []
    for tau in TAUS:
        df = B.build_panel(tau, m, piv, trades, sig)
        df = df[df["ask_side"].notna()].copy()
        df["net_cents"] = B.trade_pnl(df)
        parts.append(df)
    P = pd.concat(parts, ignore_index=False)
    return P


def boot_ci(x, n=5000):
    x = np.asarray(x, dtype=float)
    if len(x) < 5:
        return (np.nan, np.nan)
    means = x[RNG.integers(0, len(x), size=(n, len(x)))].mean(axis=1)
    return (round(float(np.percentile(means, 2.5)), 3), round(float(np.percentile(means, 97.5)), 3))


def report(df, label):
    if len(df) == 0:
        print(f"{label}: n=0"); return None
    pnl = df["net_cents"].values
    lo, hi = boot_ci(pnl)
    d = dict(label=label, n=len(df), net_mean=round(float(pnl.mean()), 3),
             ci95=(lo, hi), win=round(float(df["win"].mean()), 4),
             med=round(float(np.median(pnl)), 2), p10=round(float(np.percentile(pnl, 10)), 1),
             worst=round(float(pnl.min()), 1), best=round(float(pnl.max()), 1),
             avg_ask=round(float(df["ask_side"].mean()), 3), avg_edge=round(float(df["edge"].mean()), 3),
             total=round(float(pnl.sum()), 0))
    print(f"{label:42s} n={d['n']:5d} net={d['net_mean']:+7.3f} ci95={d['ci95']} "
          f"win={d['win']:.3f} med={d['med']:+5.1f} worst={d['worst']:+6.1f} "
          f"avgask={d['avg_ask']:.3f} avgedge={d['avg_edge']:+.3f}")
    return d


def main():
    P = pooled_panel()
    print(f"pooled tradeable windows: {len(P)}\n")

    print("=== baseline: ALL fades (any edge>0) ===")
    report(P[P["edge"] > 0], "edge>0 (all)")
    report(P, "every window (edge any sign)")

    print("\n=== sweep EDGE threshold (pooled, all ask) ===")
    for et in [0.0, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.2, 0.3]:
        report(P[P["edge"] >= et], f"edge>={et}")

    print("\n=== sweep ASK cap (only fade CHEAP side; bounds the loss) within edge>=0.02 ===")
    base = P[P["edge"] >= 0.02]
    for cap in [0.5, 0.6, 0.7, 0.8, 0.9, 0.97, 1.0]:
        report(base[base["ask_side"] <= cap], f"edge>=.02 & ask<={cap}")

    print("\n=== 2D: edge x ask cap (net mean) ===")
    for et in [0.02, 0.05, 0.1, 0.2]:
        for cap in [0.6, 0.75, 0.85, 0.95]:
            sel = P[(P["edge"] >= et) & (P["ask_side"] <= cap)]
            if len(sel) >= 25:
                report(sel, f"edge>={et} ask<={cap}")

    # ---- OOS split on the most promising pooled rules ----
    dates = sorted(pd.to_datetime(P["close_dt"]).dt.date.unique())
    split = dates[len(dates) // 2]
    Pd = P.assign(d=pd.to_datetime(P["close_dt"]).dt.date)
    ins = Pd[Pd["d"] < split]; oos = Pd[Pd["d"] >= split]
    print(f"\n=== OOS split {split}: IS n={len(ins)} OOS n={len(oos)} ===")
    rules = [
        ("edge>=.02", lambda x: x[x["edge"] >= 0.02]),
        ("edge>=.05", lambda x: x[x["edge"] >= 0.05]),
        ("edge>=.10", lambda x: x[x["edge"] >= 0.10]),
        ("edge>=.05 & ask<=.85", lambda x: x[(x["edge"] >= 0.05) & (x["ask_side"] <= 0.85)]),
        ("edge>=.05 & ask<=.75", lambda x: x[(x["edge"] >= 0.05) & (x["ask_side"] <= 0.75)]),
        ("edge>=.10 & ask<=.75", lambda x: x[(x["edge"] >= 0.10) & (x["ask_side"] <= 0.75)]),
        ("edge>=.02 & ask<=.9", lambda x: x[(x["edge"] >= 0.02) & (x["ask_side"] <= 0.9)]),
    ]
    for name, fn in rules:
        si = fn(ins); so = fn(oos)
        si_m = si["net_cents"].mean() if len(si) else np.nan
        so_m = so["net_cents"].mean() if len(so) else np.nan
        lo, hi = boot_ci(so["net_cents"].values) if len(so) >= 5 else (np.nan, np.nan)
        print(f"{name:28s} IS n={len(si):4d} net={si_m:+7.3f} win={si['win'].mean():.3f} | "
              f"OOS n={len(so):4d} net={so_m:+7.3f} ci95={(lo,hi)} win={so['win'].mean():.3f}")

    return P


if __name__ == "__main__":
    main()
