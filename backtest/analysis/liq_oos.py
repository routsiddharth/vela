"""Step 3: out-of-sample backtest of the cheap-fill rule.
Rule: at decision time tau, if |mhat_tau| >= thr (model confident) AND the winning
side is OFFERED at price <= X, buy up to the available size at that fill.
We approximate 'takeable size' as the size of trades where the taker lifted the
winning side at p<=X within (0, tau] sec-to-close (a real offer existed; we join it).

Split by date: first half -> choose best (tau,thr,X) by net EV*capacity; second
half -> report realized net EV, win rate, capacity. All causal."""
from __future__ import annotations
import numpy as np, pandas as pd
import backtest.analysis.liq_common as C

def fills_for(mdl, trades, tau, thr):
    return C.winning_side_fills(trades, mdl, tau, thr)

def rule_stats(f, X):
    sub = f[f.price <= X]
    if len(sub)==0:
        return dict(n=0, size=0.0, ev=np.nan, ev_unw=np.nan, wr=np.nan, windows=0,
                    total_pnl_dollars=0.0)
    p=sub['price'].values; sz=sub['size'].values; won=sub['won'].values
    fee=C.fee_cents(p)
    pnl=np.where(won==1, 100-100*p, -100*p)-fee     # cents/contract
    ev=(pnl*sz).sum()/sz.sum()
    return dict(n=len(sub), size=float(sz.sum()), ev=float(ev), ev_unw=float(pnl.mean()),
                wr=float(won.mean()), windows=int(sub.ticker.nunique()),
                total_pnl_dollars=float((pnl*sz).sum()/100.0))

def main():
    mdl=C.build_model()
    trades=C.load_trades_cached()
    # date per ticker
    cdt=mdl['close_dt']
    median_date=cdt.median()
    print(f"Split date: {median_date}")
    train_tk=set(mdl.index[cdt< median_date]); test_tk=set(mdl.index[cdt>=median_date])
    mtr=mdl[mdl.index.isin(train_tk)]; mte=mdl[mdl.index.isin(test_tk)]
    ttr=trades[trades.ticker.isin(train_tk)]; tte=trades[trades.ticker.isin(test_tk)]
    n_days = (cdt.max()-cdt.min()).days or 1
    n_days_test=(cdt[cdt>=median_date].max()-median_date).days or 1
    print(f"train windows={len(mtr)} test windows={len(mte)} total_days={n_days} test_days={n_days_test}\n")

    grid_tau=[15,30,45,60]; grid_thr=[40,50,60,75,100]; grid_X=[0.99,0.98,0.97,0.96,0.95,0.93,0.90]
    rows=[]
    for tau in grid_tau:
        ftr=fills_for(mtr,ttr,tau,0)  # compute once at thr=0 then re-filter by |mhat|
        for thr in grid_thr:
            f=ftr[ftr['mhat'].abs()>=thr]
            for X in grid_X:
                s=rule_stats(f,X)
                if s['n']==0: continue
                rows.append(dict(tau=tau,thr=thr,X=X,**{k:s[k] for k in ('n','size','ev','wr','windows','total_pnl_dollars')}))
    tr=pd.DataFrame(rows)
    # require meaningful capacity in-sample: size>=2000 and positive EV
    cand=tr[(tr['ev']>0)&(tr['size']>=2000)].copy()
    # score = ev * size  (total expected cents)  -> proxy for $/period
    cand['score']=cand['ev']*cand['size']
    cand=cand.sort_values('score',ascending=False)
    print("=== IN-SAMPLE (train) top rules by EV*size ===")
    print(cand.head(12).to_string(index=False))

    if len(cand)==0:
        print("No positive-EV rule with capacity in-sample."); return
    best=cand.iloc[0]
    print(f"\nChosen rule: tau={int(best.tau)} thr={int(best.thr)} X={best.X}")

    # Validate OOS
    print("\n=== OUT-OF-SAMPLE (test) for chosen rule ===")
    fte=fills_for(mte,tte,int(best.tau),int(best.thr))
    s=rule_stats(fte,best.X)
    print(s)
    # also report a few robust variants OOS
    print("\n=== OOS for several rules (robustness) ===")
    for tau,thr,X in [(45,40,0.99),(45,40,0.98),(45,50,0.99),(45,40,0.97),(30,50,0.99),(60,75,0.97)]:
        f=fills_for(mte,tte,tau,thr); s=rule_stats(f,X)
        # scale capacity to per-day in the FULL universe: trade sample covers ~40% of windows
        print(f"  tau={tau} thr={thr} X={X}: {s}")

    # $/day estimate accounting for trade-sample coverage
    print("\n=== Capacity / $/day estimate (chosen rule, OOS) ===")
    # fraction of confident windows that are in the trade sample
    conf_all=(mte[f'mhat_{int(best.tau)}'].abs()>=best.thr).sum()
    conf_traded=fte.ticker.nunique()
    cov = conf_traded/conf_all if conf_all else float('nan')
    print(f"confident test windows: all={conf_all} traded(sampled)={conf_traded} coverage={cov:.2%}")
    pnl_d=s['total_pnl_dollars']
    print(f"OOS realized PnL on SAMPLED cheap fills: ${pnl_d:,.2f} over {n_days_test} test days")
    print(f"  -> sampled $/day: ${pnl_d/n_days_test:,.2f}")
    if cov and cov>0:
        print(f"  -> full-universe $/day (scaled by 1/coverage): ${pnl_d/n_days_test/cov:,.2f}")

if __name__=='__main__':
    main()
