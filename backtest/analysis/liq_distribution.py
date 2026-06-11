"""Step 1: FULL distribution of the winning side's takeable fill prices in
confident windows. Not the mean — the tail. How often / how much size trades
cheap (<=0.95, <=0.97)?"""
from __future__ import annotations
import numpy as np, pandas as pd
import backtest.analysis.liq_common as C

def main():
    mdl = C.build_model()
    trades = C.load_trades_cached()
    print(f"Model windows: {len(mdl)}; trade-sampled windows: {trades.ticker.nunique()}\n")

    for tau in C.TAUS:
        for th in [50, 75, 100]:
            f = C.winning_side_fills(trades, mdl, tau, th)
            if len(f) == 0:
                continue
            # restrict to windows that are actually in the trade sample
            n_win = f.ticker.nunique()
            p = f["price"].values
            sz = f["size"].values
            tot_sz = sz.sum()
            # size-weighted price stats
            def szfrac(thr):
                return sz[p <= thr].sum() / tot_sz
            def cnt_win_with_cheap(thr):
                # windows where ANY takeable winning-side fill happened <= thr
                return f[f.price <= thr].ticker.nunique()
            print(f"--- tau={tau} thr={th} | windows(traded)={n_win} fills={len(f)} totsize={tot_sz:,.0f} ---")
            print(f"   price: mean={p.mean():.4f} szw_mean={(p*sz).sum()/tot_sz:.4f} "
                  f"min={p.min():.3f} p1={np.percentile(p,1):.3f} p5={np.percentile(p,5):.3f} "
                  f"p10={np.percentile(p,10):.3f} med={np.median(p):.3f}")
            for thr in [0.99, 0.97, 0.95, 0.90, 0.85, 0.80]:
                print(f"   p<= {thr:.2f}: fills={ (p<=thr).sum():>6} "
                      f"size={sz[p<=thr].sum():>10,.0f} ({szfrac(thr)*100:5.2f}% of size) "
                      f"windows_w_cheap={cnt_win_with_cheap(thr):>4} ({cnt_win_with_cheap(thr)/n_win*100:5.2f}%)")
            print()

if __name__ == "__main__":
    main()
