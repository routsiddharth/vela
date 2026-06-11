"""Step 1b (the skeptic's test): when the model-predicted winning side traded
CHEAP, did that side actually WIN? If cheap fills are concentrated in windows
the model got WRONG, the 'cheap' price is correct and there is no edge.

We condition on the model being confident (|mhat|>=thr) and look at fills <= X.
'won' here is the realized outcome of the model-predicted side (causal label only
used for accounting, never for selection)."""
from __future__ import annotations
import numpy as np, pandas as pd
import backtest.analysis.liq_common as C

def main():
    mdl = C.build_model()
    trades = C.load_trades_cached()
    for tau in C.TAUS:
        for th in [50, 75, 100]:
            f = C.winning_side_fills(trades, mdl, tau, th)
            if len(f) == 0: continue
            print(f"=== tau={tau} thr={th} ===")
            for thr in [1.01, 0.99, 0.97, 0.95, 0.90]:
                sub = f[f.price <= thr]
                if len(sub) == 0:
                    print(f"  price<= {thr:.2f}: (none)")
                    continue
                # win rate weighted by SIZE (a fill on a losing side loses its whole stake)
                sz = sub["size"].values; won = sub["won"].values
                wr_fill = won.mean()
                wr_sz = (won*sz).sum()/sz.sum()
                # per-window: of windows that HAD a cheap fill, how many actually won
                wpw = sub.groupby("ticker")["won"].first()
                print(f"  price<= {thr:.2f}: fills={len(sub):>6} size={sz.sum():>10,.0f} "
                      f"winrate(fill)={wr_fill:.4f} winrate(sizew)={wr_sz:.4f} "
                      f"windows={len(wpw)} window_winrate={wpw.mean():.4f}")
            print()

if __name__ == "__main__":
    main()
