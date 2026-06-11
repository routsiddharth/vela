"""AUDIT 1: look-ahead / leakage.

Questions:
 (a) Does binance_matrix()'s interpolate(limit_direction="both") leak FUTURE
     seconds (sec_to_close < tau) into the price at col[tau]? Quantify how many
     cells get filled and whether any tau-cell is filled from a *later* (smaller
     sec_to_close) observation.
 (b) Recompute the lock-detection win rate WITHOUT any forward/backward fill:
     use only RAW observed prices with sec_to_close >= tau. Compare 99.97%.
 (c) Confirm causal_bias uses only PRIOR settled windows (shift(1)).
"""
from __future__ import annotations
import numpy as np, pandas as pd
from backtest import btc_lib as L

m = L.load_markets()
b = L.load_binance()

# ---------- (a) interpolation leakage characterisation ----------
print("="*70)
print("(a) interpolate leakage characterisation")
print("="*70)
raw = b.pivot_table(index="ticker", columns="sec_to_close", values="price", aggfunc="last")
raw = raw.reindex(columns=range(1, 301))
filled = L.binance_matrix()
# align
common = raw.index.intersection(filled.index)
raw = raw.loc[common]; filled = filled.loc[common, range(1,301)]

raw_na = raw.isna()
fill_na = filled.isna()
n_filled = int((raw_na & ~fill_na).sum().sum())
n_total = raw.size
n_raw_present = int((~raw_na).sum().sum())
print(f"cells total={n_total}  raw present={n_raw_present}  newly filled by interp={n_filled} "
      f"({100*n_filled/n_total:.3f}% of all cells)")

# How many cells at a given tau (col) were filled? Especially the decision columns.
for tau in (30,45,60,90,120):
    col_raw_na = raw[tau].isna()
    col_filled = filled[tau].notna()
    n = int((col_raw_na & col_filled).sum())
    print(f"  tau={tau}: windows where col[tau] was MISSING raw but interp-filled: {n} "
          f"/ {len(raw)}  ({100*n/len(raw):.2f}%)")

# Is a filled tau-cell EVER derived from a strictly-later (smaller sec_to_close) value?
# interpolate(axis=1) linearly interpolates between nearest non-nan neighbours on
# BOTH sides; limit_direction='both' additionally back/forward fills edges.
# A col[tau] filled value uses the next-smaller-sec neighbour => FUTURE leak.
# Detect: for each filled tau cell, find nearest present neighbour with sec<tau (future)
#         and sec>tau (past). If only a future neighbour exists within limit=5 -> pure future leak.
def neighbours(rawrow, tau, limit=5):
    fut = None; past = None
    for d in range(1, limit+1):
        s = tau - d  # smaller sec_to_close = LATER in time = future
        if s in rawrow.index and not pd.isna(rawrow[s]) and fut is None:
            fut = s
        s2 = tau + d
        if s2 in rawrow.index and not pd.isna(rawrow[s2]) and past is None:
            past = s2
    return fut, past

leak_count = {}
for tau in (30,45,60,90,120):
    only_future = 0; both = 0; only_past = 0
    sel = raw[(raw[tau].isna()) & (filled[tau].notna())]
    for tk, row in sel.iterrows():
        f, p = neighbours(row, tau)
        if f is not None and p is None: only_future += 1
        elif f is not None and p is not None: both += 1
        elif f is None and p is not None: only_past += 1
    leak_count[tau] = (only_future, both, only_past)
    print(f"  tau={tau}: filled-from-future-only={only_future}  from-both(=interp,partial future)={both}  past-only={only_past}")

# ---------- (b) raw-only lock detection vs filled ----------
print("="*70)
print("(b) lock-detection win rate: filled vs RAW-only")
print("="*70)

def winrate(piv, tau, thresh, m):
    raw60 = L.raw_avg60(piv)
    delta = L.causal_bias(m, raw60)
    delta.index = m.index
    # map delta back onto tickers via m
    dmap = pd.Series(delta.values, index=m["ticker"].values)
    d_for_piv = piv.index.map(dmap)
    shat = L.estimate(piv, tau, pd.Series(d_for_piv, index=piv.index))
    # strike + outcome from m
    mk = m.set_index("ticker")
    strike = piv.index.map(mk["strike"]); strike = pd.Series(strike, index=piv.index)
    yes = piv.index.map(mk["yes"]); yes = pd.Series(yes, index=piv.index)
    mhat = shat - strike
    pred = (mhat >= 0).astype(int)
    sel = mhat.abs() > thresh
    sel &= mhat.notna() & yes.notna()
    n = int(sel.sum())
    if n == 0: return (0,0,np.nan)
    correct = int((pred[sel] == yes[sel]).sum())
    flips = n - correct
    return (n, flips, correct/n)

# filled matrix (the original claim)
piv_filled = L.binance_matrix()

# RAW-only matrix: NO interpolation at all.
def raw_matrix():
    p = b.pivot_table(index="ticker", columns="sec_to_close", values="price", aggfunc="last")
    return p.reindex(columns=range(1, 301))
piv_raw = raw_matrix()

for tau in (30,45,60,90,120):
    for thr in (50,75,100):
        nf,ff,wf = winrate(piv_filled, tau, thr, m)
        nr,fr,wr = winrate(piv_raw, tau, thr, m)
        print(f"tau={tau:3d} thr={thr:3d} | FILLED n={nf:5d} flips={ff:3d} wr={wf:.5f} "
              f"| RAW n={nr:5d} flips={fr:3d} wr={wr:.5f}")

# ---------- (c) causal_bias structural check ----------
print("="*70)
print("(c) causal_bias structural check (shift(1) => no own/future window)")
print("="*70)
raw60 = L.raw_avg60(piv_filled)
delta = L.causal_bias(m, raw60)
# manual causal recompute and compare
df = m[["ticker","true_settle"]].copy()
df["raw60"] = df["ticker"].map(raw60)
df["err"] = df["raw60"] - df["true_settle"]
manual = df["err"].shift(1).rolling(96, min_periods=20).median()
print("delta matches manual shift(1) recompute:", bool(np.allclose(delta.fillna(-9e9), manual.fillna(-9e9))))
# Adversarial: build a NON-causal (centered, includes own window) bias and see how much
# it would *improve* — if it improves a lot, the causal version is honest but weaker.
noncausal = df["err"].rolling(96, center=True, min_periods=20).median()
# test win rate using non-causal delta (would be leakage)
dmap_nc = pd.Series(noncausal.values, index=m["ticker"].values)
d_nc = piv_filled.index.map(dmap_nc)
shat_nc = L.estimate(piv_filled, 30, pd.Series(d_nc, index=piv_filled.index))
mk = m.set_index("ticker")
strike = pd.Series(piv_filled.index.map(mk["strike"]), index=piv_filled.index)
yes = pd.Series(piv_filled.index.map(mk["yes"]), index=piv_filled.index)
mhat_nc = shat_nc - strike
sel = (mhat_nc.abs()>50)&mhat_nc.notna()&yes.notna()
wr_nc = ((mhat_nc[sel]>=0).astype(int)==yes[sel]).mean()
print(f"NON-CAUSAL (centered, leaky) tau=30 thr=50 winrate={wr_nc:.5f} n={int(sel.sum())} "
      f"(if >> causal, the causal one is honest)")
