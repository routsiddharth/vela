"""AUDIT 3: independent replication of the taker-EV headline.

From-scratch re-derivation. Strategy at decision time tau:
  - compute de-biased estimate S_hat, pick side = sign(S_hat - strike)
  - require |mhat| > threshold (confidence gate)
  - ENTER as a TAKER buying the chosen side. Realistic fill = the price an
    aggressive taker actually paid for that side near tau:
        use trades with taker_side == chosen_side and sec_to_close in [tau-W, tau]
        fill = size-weighted mean of that side's execution price (the ask it lifted)
  - Payoff: win -> contract pays $1, cost = fill ; lose -> cost = fill, payoff 0.
  - Fee: round_up_to_cent(0.07 * P * (1-P)) charged on entry (P = fill price).
EV/contract = winrate*(1-fill) - (1-winrate)*fill - fee  (per $1 contract; in cents *100)
            = (winrate - fill)  ... gross, minus fee.

Compare two fill models:
  A) NAIVE: mean traded price near tau on the chosen side (mix of taker buy/sell)
  B) REALISTIC ASK: only taker-buys of the chosen side (what an aggressor pays)
"""
from __future__ import annotations
import numpy as np, pandas as pd, math
from backtest import btc_lib as L

m = L.load_markets()
piv = L.binance_matrix()
t = L.load_trades()
raw60 = L.raw_avg60(piv)
delta = L.causal_bias(m, raw60); delta.index = m.index
dmap = pd.Series(delta.values, index=m["ticker"].values)
mk = m.set_index("ticker")

def fee_cents(P):
    raw = 0.07 * P * (1.0 - P)          # dollars
    return math.ceil(raw * 100 - 1e-9)  # cents, round UP to next cent

# Precompute, per ticker, the chosen side + win flag at a given tau/threshold.
def decisions(tau, thr):
    dpiv = pd.Series(piv.index.map(dmap), index=piv.index)
    shat = L.estimate(piv, tau, dpiv)
    strike = pd.Series(piv.index.map(mk["strike"]), index=piv.index)
    yes = pd.Series(piv.index.map(mk["yes"]), index=piv.index)
    mhat = shat - strike
    df = pd.DataFrame({"mhat": mhat, "yes": yes})
    df = df[df.mhat.notna() & df.yes.notna()]
    df = df[df.mhat.abs() > thr]
    df["side"] = np.where(df.mhat >= 0, "yes", "no")
    df["win"] = (df["side"] == np.where(df.yes==1,"yes","no")).astype(int)
    return df  # index = ticker

# fill price near tau on a side
W = 10  # +/- window seconds around tau for fills
def fills_for(tau, side_filter_taker):
    """Return per-ticker fill price.
       side_filter_taker: 'naive' -> any trade; 'ask' -> taker bought that side."""
    out = {}
    sub = t[(t.sec_to_close >= tau-W) & (t.sec_to_close <= tau+W)]
    return sub

print("tau thr | model |    n  win%  | fill  gross_c  fee_c | NET c/contract  +tradable")
print("-"*92)
for tau in (30,45,60,90,120):
    for thr in (50,75,100):
        dec = decisions(tau, thr)
        sub = t[(t.sec_to_close >= tau-W) & (t.sec_to_close <= tau+W)]
        for model in ("naive","ask"):
            rows=[]
            for tk, row in dec.iterrows():
                side = row["side"]; win = row["win"]
                tr = sub[sub.ticker==tk]
                if model=="ask":
                    tr = tr[tr.taker_side==side]
                if len(tr)==0:
                    continue
                price_col = "yes_price" if side=="yes" else "no_price"
                w = tr["size"].values; px = tr[price_col].values
                if w.sum()<=0: continue
                fill = float((px*w).sum()/w.sum())
                fill = min(max(fill,0.001),0.999)
                fee = fee_cents(fill)/100.0
                net = (win*(1-fill) - (1-win)*fill) - fee   # dollars/contract
                rows.append((win, fill, net*100, fee*100))
            if not rows:
                print(f"{tau:3d} {thr:3d} | {model:5s} | (no fills)")
                continue
            arr = pd.DataFrame(rows, columns=["win","fill","net_c","fee_c"])
            print(f"{tau:3d} {thr:3d} | {model:5s} | {len(arr):4d} {100*arr.win.mean():5.1f} | "
                  f"{arr.fill.mean():.3f}  {(arr.win.mean()-arr.fill.mean())*100:+6.2f}  "
                  f"{arr.fee_c.mean():.2f} | {arr.net_c.mean():+7.3f}      n={len(arr)}")
