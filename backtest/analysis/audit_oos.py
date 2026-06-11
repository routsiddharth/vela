"""AUDIT 4: out-of-sample / regime split.

 (a) Split the period into month-1 vs month-2. The causal de-bias is already
     purely trailing (no global fit), so test whether lock-detection win rate
     holds in month-2 when nothing is tuned globally.
 (b) Threshold look-ahead: were $50/$75 picked using the full sample? Re-derive
     the minimum threshold needed for >=99.9% win rate on month-1 ONLY, apply it
     to month-2 unseen, and report month-2 win rate (true OOS).
 (c) Taker EV OOS: net EV per half, ensure sign is stable across regimes.
"""
from __future__ import annotations
import numpy as np, pandas as pd, math
from backtest import btc_lib as L

m = L.load_markets().sort_values("close_dt").reset_index(drop=True)
piv = L.binance_matrix()
raw60 = L.raw_avg60(piv)
delta = L.causal_bias(m, raw60); delta.index = m.index
dmap = pd.Series(delta.values, index=m["ticker"].values)
mk = m.set_index("ticker")

# global mhat / win at tau (causal delta is fine OOS — purely trailing)
def build(tau):
    dpiv = pd.Series(piv.index.map(dmap), index=piv.index)
    shat = L.estimate(piv, tau, dpiv)
    strike = pd.Series(piv.index.map(mk["strike"]), index=piv.index)
    yes = pd.Series(piv.index.map(mk["yes"]), index=piv.index)
    mhat = shat - strike
    df = pd.DataFrame({"mhat":mhat,"yes":yes})
    df["close_dt"] = df.index.map(mk["close_dt"])
    df = df.dropna(subset=["mhat","yes"])
    df["pred"] = (df.mhat>=0).astype(int)
    df["win"] = (df.pred==df.yes).astype(int)
    return df

# month boundary ~ midpoint in time
mid = m["close_dt"].min() + (m["close_dt"].max()-m["close_dt"].min())/2
print("split at", mid)

print("="*70); print("(a) win rate by half, tau x thr (causal, no global tuning)"); print("="*70)
for tau in (30,60,90):
    df = build(tau)
    h1 = df[df.close_dt<mid]; h2 = df[df.close_dt>=mid]
    for thr in (50,75,100):
        def wr(d):
            s=d[d.mhat.abs()>thr];
            return (len(s), int((s.pred!=s.yes).sum()), (s.win.mean() if len(s) else float('nan')))
        n1,f1,w1=wr(h1); n2,f2,w2=wr(h2)
        print(f"tau={tau:3d} thr={thr:3d} | H1 n={n1:4d} flips={f1:2d} wr={w1:.5f} | H2 n={n2:4d} flips={f2:2d} wr={w2:.5f}")

print("="*70); print("(b) TRUE OOS threshold: tune on H1, apply to H2"); print("="*70)
for tau in (30,60,90):
    df = build(tau); h1=df[df.close_dt<mid]; h2=df[df.close_dt>=mid]
    # minimum thr (grid) s.t. H1 win rate >= 0.999 with >=200 trades
    best=None
    for thr in range(20,300,5):
        s=h1[h1.mhat.abs()>thr]
        if len(s)>=200 and s.win.mean()>=0.999:
            best=thr; break
    if best is None:
        print(f"tau={tau}: no thr hits 99.9% on H1"); continue
    s2=h2[h2.mhat.abs()>best]
    print(f"tau={tau:3d}: H1-tuned thr={best} -> H2 OOS n={len(s2)} flips={int((s2.pred!=s2.yes).sum())} "
          f"wr={s2.win.mean():.5f}")

print("="*70); print("(c) taker NET EV by half (ask-fill model)"); print("="*70)
t = L.load_trades()
def fee_c(P): return math.ceil(0.07*P*(1-P)*100-1e-9)
W=10
for tau in (30,60,90):
    df=build(tau)
    sub=t[(t.sec_to_close>=tau-W)&(t.sec_to_close<=tau+W)]
    for thr in (50,75):
        for half,lab in [(df[df.close_dt<mid],"H1"),(df[df.close_dt>=mid],"H2")]:
            d=half[half.mhat.abs()>thr]; d=d.assign(side=np.where(d.mhat>=0,"yes","no"))
            nets=[]
            for tk,row in d.iterrows():
                tr=sub[(sub.ticker==tk)&(sub.taker_side==row.side)]
                if len(tr)==0: continue
                pc="yes_price" if row.side=="yes" else "no_price"
                w=tr["size"].values; px=tr[pc].values
                if w.sum()<=0: continue
                fill=min(max(float((px*w).sum()/w.sum()),.001),.999)
                win=int(row.win)
                nets.append((win*(1-fill)-(1-win)*fill)-fee_c(fill)/100.0)
            if nets:
                print(f"tau={tau:3d} thr={thr:3d} {lab}: net={np.mean(nets)*100:+.3f} c/contract  n={len(nets)}")
