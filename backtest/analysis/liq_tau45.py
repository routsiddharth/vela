"""Step 1c: zoom on tau=45 — the regime where the model is reliable (15 of 60
settlement seconds already locked) yet cheap winning-side fills still appear with
100% window win-rate. Quantify: how many windows, how much size, at what prices,
and is win-rate robust as we widen the confidence threshold (more windows)."""
from __future__ import annotations
import numpy as np, pandas as pd
import backtest.analysis.liq_common as C

def main():
    mdl = C.build_model()
    trades = C.load_trades_cached()
    tau = 45
    print(f"tau={tau}. Trade-sampled fraction of all confident windows matters for capacity.\n")
    for th in [40, 50, 60, 75, 100]:
        f = C.winning_side_fills(trades, mdl, tau, th)
        if len(f)==0: continue
        nwin_all = (mdl[f'mhat_{tau}'].abs()>=th).sum()
        nwin_traded = f.ticker.nunique()
        print(f"--- thr={th}: confident windows(all)={nwin_all} traded={nwin_traded} ---")
        for thr in [0.99, 0.98, 0.97, 0.96, 0.95]:
            sub = f[f.price <= thr]
            if len(sub)==0:
                print(f"   p<={thr}: none"); continue
            sz=sub['size'].values; won=sub['won'].values
            wpw=sub.groupby('ticker')['won'].first()
            # avg cheap size per cheap-window (capacity per opportunity)
            sz_per_win = sub.groupby('ticker')['size'].sum()
            print(f"   p<={thr}: fills={len(sub):>5} totsize={sz.sum():>9,.0f} "
                  f"winrate(fill)={won.mean():.3f} szw={((won*sz).sum()/sz.sum()):.3f} "
                  f"cheap_windows={len(wpw)} win={wpw.mean():.3f} "
                  f"median_size/win={sz_per_win.median():.0f}")
        print()

    # net EV for a concrete rule at tau=45: buy winning side at any offered p<=X
    print("=== NET EV (cents/contract) buying winning side <= X at tau=45 ===")
    print("payoff: win -> (100-100p) - fee ; lose -> (-100p) - fee  [cents]")
    for th in [40,50,60,75]:
        f = C.winning_side_fills(trades, mdl, tau, th)
        for X in [1.0, 0.99, 0.98, 0.97, 0.96, 0.95]:
            sub=f[f.price<=X]
            if len(sub)==0: continue
            p=sub['price'].values; sz=sub['size'].values; won=sub['won'].values
            fee=C.fee_cents(p)
            pnl = np.where(won==1, 100-100*p, -100*p) - fee   # cents per contract
            ev = (pnl*sz).sum()/sz.sum()
            n_ev = pnl.mean()
            print(f"  thr={th} p<={X}: size={sz.sum():>9,.0f} EV_sizew={ev:+.3f}c EV_unw={n_ev:+.3f}c "
                  f"winrate={won.mean():.3f}")
        print()

if __name__ == "__main__":
    main()
