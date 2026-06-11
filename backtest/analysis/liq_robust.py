"""Step 3b: robustness of the tau=45 cheap-fill rule.
- Why tau=60 is a TRAP: it has only 1 locked settlement second, so a confident
  mhat is pure martingale -> cheap offers are informed reversals -> they LOSE OOS.
- tau=45 has 16 locked seconds. Test stability month-by-month, and the sensitivity
  of win-rate to X. Also: are these fills genuinely takeable (taker actually lifted
  the winning side -> the offer existed and cleared)?"""
from __future__ import annotations
import numpy as np, pandas as pd
import backtest.analysis.liq_common as C

def rule_stats(f, X):
    sub=f[f.price<=X]
    if len(sub)==0: return None
    p=sub['price'].values; sz=sub['size'].values; won=sub['won'].values
    fee=C.fee_cents(p)
    pnl=np.where(won==1,100-100*p,-100*p)-fee
    return dict(fills=len(sub),size=float(sz.sum()),windows=int(sub.ticker.nunique()),
                ev=float((pnl*sz).sum()/sz.sum()),wr=float(won.mean()),
                losers=int((won==0).sum()),loser_size=float(sz[won==0].sum()),
                pnl=float((pnl*sz).sum()/100.0))

def main():
    mdl=C.build_model(); trades=C.load_trades_cached()
    mdl=mdl.copy(); mdl['month']=mdl['close_dt'].dt.to_period('M')

    print("=== tau=45 thr=40, monthly stability ===")
    for X in [0.99,0.98,0.97]:
        print(f"-- X<= {X} --")
        for mo,g in mdl.groupby('month'):
            tk=set(g.index)
            f=C.winning_side_fills(trades[trades.ticker.isin(tk)],mdl.loc[list(tk)],45,40)
            s=rule_stats(f,X)
            if s: print(f"  {mo}: fills={s['fills']:>5} size={s['size']:>8,.0f} windows={s['windows']:>3} "
                        f"ev={s['ev']:+.2f}c wr={s['wr']:.3f} losers={s['losers']}({s['loser_size']:,.0f})")
        print()

    print("=== Compare tau regimes side by side (full sample, thr=50, X<=0.97) ===")
    print("  tau | locked_secs | cheap_size | windows | win_rate | ev")
    for tau in [15,30,45,60]:
        f=C.winning_side_fills(trades,mdl,tau,50); s=rule_stats(f,0.97)
        nlock=max(0,61-tau) if tau<=60 else 0
        if s: print(f"   {tau:>3} | {nlock:>11} | {s['size']:>10,.0f} | {s['windows']:>7} | {s['wr']:>8.3f} | {s['ev']:+.2f}c")

    # takeability: distribution of fill sizes and taker presence
    print("\n=== Takeability check: tau=45 thr=40 X<=0.99 cheap fills ===")
    f=C.winning_side_fills(trades,mdl,45,40); sub=f[f.price<=0.99]
    print(f"  all winning-side fills here come from trades where the TAKER lifted our side")
    print(f"  -> a real resting offer existed and cleared. n={len(sub)} median_size={sub['size'].median():.0f} "
          f"mean={sub['size'].mean():.0f} p90={sub['size'].quantile(.9):.0f} max={sub['size'].max():.0f}")
    print(f"  size distribution of fills: {sub['size'].describe().to_dict()}")

if __name__=='__main__':
    main()
