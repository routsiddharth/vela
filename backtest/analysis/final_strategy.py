"""Definitive backtest of the TWAP-anchor panic-fade strategy.

RULE (fully causal):
  At decision tau=45s, compute mhat = de-biased TWAP estimate of (settle - strike).
  If |mhat| >= THR, target the model-winning side.
  Over actionable seconds sec_to_close in [SEC_LO, 45], lift any print on the winning
  side offered at price <= CAP, capturing CAP_FRAC of that printed size (competition haircut),
  up to PER_WIN cap. Hold to settlement.
  PnL/contract: win -> (1-price) - fee ; lose -> -price - fee ; fee = ceil_cent(0.07 p(1-p)), min 1c.

Reports: net c/contract, win rate, contracts, est $/day, split IN-SAMPLE (H1) vs OOS (H2),
with a bootstrap CI on per-window PnL.
"""
from __future__ import annotations
import numpy as np, pandas as pd, math
from backtest.btc_lib import *

SAMPLE_FRAC = 2500/6308   # trades.parquet covers an even 2500-window sample

def fee(p): return max(0.01, math.ceil(0.07*p*(1-p)*100)/100)
fee_v = np.vectorize(fee)

m=load_markets(); piv=binance_matrix()
m=m[m.ticker.isin(piv.index)].reset_index(drop=True)
raw60=raw_avg60(piv); m["delta"]=causal_bias(m,raw60).values
dser=pd.Series(m.delta.values,index=m.ticker.values)
shat=estimate(piv.loc[m.ticker],45,dser); m["mhat"]=shat.values-m.strike.values
m=m.dropna(subset=["delta","mhat"])
tr=load_trades()
mid=m.close_dt.quantile(.5)

def run(THR, CAP, SEC_LO=5, CAP_FRAC=1.0, PER_WIN=10**9):
    g=m[m.mhat.abs()>=THR].copy()
    g["bet_yes"]=g.mhat>0
    d=tr.merge(g[["ticker","bet_yes","yes","close_dt"]],on="ticker",how="inner")
    d=d[(d.sec_to_close>=SEC_LO)&(d.sec_to_close<=45)]
    d["win_px"]=np.where(d.bet_yes,d.yes_price,d.no_price)
    d=d[(d.win_px<=CAP)&(d.win_px>0)]
    d["won"]=np.where(d.bet_yes,d.yes==1,d.yes==0)
    d["qty"]=np.minimum(d["size"]*CAP_FRAC, PER_WIN)
    d["pnl_ct"]=np.where(d.won,1-d.win_px,-d.win_px)-fee_v(d.win_px.values)
    d["pnl"]=d.pnl_ct*d["qty"]
    def stats(x):
        if len(x)==0: return None
        ct=x["qty"].sum(); pnl=x.pnl.sum()
        wr=(x.won*x["qty"]).sum()/ct
        nwin=x.ticker.nunique()
        # per-window pnl for bootstrap
        pw=x.groupby("ticker").pnl.sum().values
        return dict(net_c=pnl/ct*100, wr=wr*100, contracts=int(ct), windows=nwin,
                    losers=x[~x.won].ticker.nunique(), pw=pw)
    full=stats(d); h1=stats(d[d.close_dt<mid]); h2=stats(d[d.close_dt>=mid])
    return full,h1,h2

def boot(pw, n=2000):
    if len(pw)<5: return (float('nan'),float('nan'))
    rng=np.random.default_rng(0)
    means=[rng.choice(pw,len(pw),replace=True).sum() for _ in range(n)]
    # report total pnl CI scaled per-window
    return np.percentile(means,2.5)/len(pw), np.percentile(means,97.5)/len(pw)

DAYS=(m.close_dt.max()-m.close_dt.min()).days
print(f"period {DAYS} days; sample coverage {SAMPLE_FRAC:.2f}\n")
print(f"{'THR':>4}{'CAP':>6}{'capfr':>7} | {'net_c':>7}{'wr%':>8}{'wins/los':>10}{'k_ct':>8}{'$/day*':>8} | {'OOS net_c':>10}{'OOS wr':>8}{'OOS los':>8}")
for THR in [40,50,75]:
    for CAP in [0.97,0.99]:
        for CF in [1.0,0.25]:
            full,h1,h2=run(THR,CAP,CAP_FRAC=CF)
            if not full: continue
            # $/day: scale sampled contracts to full universe / period
            usd_day = full['pnl_total'] if False else (full['net_c']/100)*full['contracts']/SAMPLE_FRAC/DAYS
            lo,hi=boot(full['pw'])
            oos = f"{h2['net_c']:>10.2f}{h2['wr']:>8.2f}{h2['losers']:>8d}" if h2 else " "*26
            print(f"{THR:>4}{CAP:>6}{CF:>7} | {full['net_c']:>7.2f}{full['wr']:>8.3f}"
                  f"{str(full['windows'])+'/'+str(full['losers']):>10}{full['contracts']/1000:>8.1f}{usd_day:>8.0f} |{oos}")
print("\n* $/day assumes you capture the modeled CAP_FRAC of cheap printed volume across the FULL universe.")
print("bootstrap 95% CI on net_c (THR50,CAP0.99,CF1.0):")
full,_,_=run(50,0.99); print("  full-sample net_c %.2f  CI(%.2f, %.2f) c/contract-window-weighted" % (full['net_c'], *[x*100/ (full['contracts']/full['windows']) for x in boot(full['pw'])]))
