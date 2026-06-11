"""AUDIT 2: proxy validity (Binance BTCUSDT vs CF-Benchmarks RTI).

 (a) How fast can the Binance-minus-RTI bias jump intra-day? Per-hour drift.
 (b) De-bias residual with SHORTER lookbacks; does residual ever blow up and
     flip outcomes silently?
 (c) Is settlement a PLAIN mean of 60, or trimmed? Reconstruct true_settle from
     Binance final-60s under plain-mean vs trimmed-20%, after de-bias, and see
     which matches better. (Also: does raw_avg60 even use the right 60 seconds?)
"""
from __future__ import annotations
import numpy as np, pandas as pd
from scipy import stats
from backtest import btc_lib as L

m = L.load_markets()
b = L.load_binance()
piv = L.binance_matrix()
raw60 = L.raw_avg60(piv)
m = m.copy()
m["raw60"] = m["ticker"].map(raw60)
m["err"] = m["raw60"] - m["true_settle"]      # Binance(proxy)-minus-RTI(truth) bias
m = m.dropna(subset=["err"]).reset_index(drop=True)

print("="*70); print("(a) bias level + intra-day drift speed"); print("="*70)
print(f"overall err: mean={m.err.mean():.2f} median={m.err.median():.2f} std={m.err.std():.2f}")
m["hour"] = m["close_dt"].dt.floor("h")
hourly = m.groupby("hour")["err"].median()
print("hourly median err: min/med/max = "
      f"{hourly.min():.1f} / {hourly.median():.1f} / {hourly.max():.1f}")
# how fast does the hourly median move hour-to-hour?
dh = hourly.diff().dropna()
print(f"hour-to-hour change in median err: std={dh.std():.2f}  q95|abs|={dh.abs().quantile(.95):.2f}  max|abs|={dh.abs().max():.2f}")
# window-to-window (15 min) jump
m_sorted = m.sort_values("close_dt")
dw = m_sorted["err"].diff().dropna()
print(f"window-to-window err change: std={dw.std():.2f} q95|abs|={dw.abs().quantile(.95):.2f} max={dw.abs().max():.2f}")

print("="*70); print("(b) residual under different causal lookbacks"); print("="*70)
def causal_bias_lb(m, raw60, lb):
    df = m[["ticker","true_settle"]].copy()
    df["raw60"] = df["ticker"].map(raw60)
    df["err"] = df["raw60"] - df["true_settle"]
    df["delta"] = df["err"].shift(1).rolling(lb, min_periods=min(20,lb)).median()
    return df["delta"]
def resid_for_lookback(lb):
    d = causal_bias_lb(m, raw60, lb)
    d.index = m.index
    resid = m["err"] - d
    return resid.dropna()
for lb in (8, 24, 48, 96, 192, 384):  # 2h,6h,12h,24h,48h,96h
    r = resid_for_lookback(lb)
    print(f"lookback={lb:4d} ({lb*15/60:5.1f}h): resid mean={r.mean():+.2f} std={r.std():.2f} "
          f"q99|abs|={r.abs().quantile(.99):.1f} max|abs|={r.abs().max():.1f} n={len(r)}")

# (b2) does a big residual silently flip an outcome at the confident tail?
print("--- residual-induced flips at tau=30 thr=50 across lookbacks ---")
def flip_audit(lb, tau=30, thr=50):
    d = causal_bias_lb(m, raw60, lb); d.index = m.index
    dmap = pd.Series(d.values, index=m["ticker"].values)
    dpiv = pd.Series(piv.index.map(dmap), index=piv.index)
    shat = L.estimate(piv, tau, dpiv)
    mk = m.set_index("ticker")
    strike = pd.Series(piv.index.map(mk["strike"]), index=piv.index)
    yes = pd.Series(piv.index.map(mk["yes"]), index=piv.index)
    mhat = shat - strike
    sel = (mhat.abs()>thr) & mhat.notna() & yes.notna()
    pred = (mhat[sel]>=0).astype(int)
    flips = int((pred != yes[sel]).sum())
    return int(sel.sum()), flips
for lb in (8,24,48,96,192):
    n,f = flip_audit(lb)
    print(f"  lookback={lb:4d}: n={n} flips={f} wr={1-f/n:.5f}")

print("="*70); print("(c) settlement: plain mean vs trimmed?"); print("="*70)
# Reconstruct Binance settlement estimate over secs 1..60 under plain vs trimmed,
# then de-bias each (with its OWN causal trailing median of its own err) and compare
# which reconstruction best matches true_settle.
def avg_variant(piv, fn):
    sub = piv[list(range(1,61))]
    return sub.apply(fn, axis=1)

plain = avg_variant(piv, lambda r: np.nanmean(r.values))
def trim20(vals):
    v = np.sort(vals[~np.isnan(vals)])
    if len(v) < 5: return np.nanmean(vals)
    k = int(len(v)*0.10)  # 10% each tail = 20% trimmed
    return v[k:len(v)-k].mean()
trimmed = avg_variant(piv, lambda r: trim20(r.values))
median60 = avg_variant(piv, lambda r: np.nanmedian(r.values))

m["plain"] = m["ticker"].map(plain)
m["trim"] = m["ticker"].map(trimmed)
m["med60"] = m["ticker"].map(median60)
# De-bias each with a static median bias (we want to compare SHAPE match to truth,
# so remove each variant's own mean offset, then compare residual std to true_settle).
for col in ("plain","trim","med60"):
    e = m[col] - m["true_settle"]
    e = e - e.median()      # remove constant bias; pure shape mismatch
    print(f"{col:6s}: after removing const bias -> resid std={e.std():.3f} "
          f"q99|abs|={e.abs().quantile(.99):.3f} mean|resid|={e.abs().mean():.3f}")
# If plain mean has the LOWEST residual std, settlement is plain mean.
e_plain = (m["plain"]-m["true_settle"]); e_plain-=e_plain.median()
e_trim = (m["trim"]-m["true_settle"]); e_trim-=e_trim.median()
print(f"plain better than trimmed (lower std)? {e_plain.std() < e_trim.std()}  "
      f"(plain {e_plain.std():.3f} vs trim {e_trim.std():.3f})")
