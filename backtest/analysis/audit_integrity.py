"""AUDIT 5 + sharpened settlement test: data integrity & timestamp alignment.

 (i)  Does Binance final-60s mean (de-biased) reconstruct true_settle tightly?
      If the proxy + window alignment are right, after a per-window de-bias the
      reconstructed settle should match true_settle to ~$10, NOT ~$44.
      ($44 in audit_proxy (c) was window-to-window bias drift, not noise.)
 (ii) Timestamp alignment: is sec_to_close=0 exactly close_time? Off-by-one?
      Compare plain-mean over secs[1..60] vs secs[0..59] vs secs[2..61] -> which
      best matches true_settle (after de-bias). Reveals the correct settle window.
 (iii) Missing / garbage windows: which windows lack final-60s data, weird prices.
 (iv) Is true_settle plausibly the settlement price? cross-check magnitude.
"""
from __future__ import annotations
import numpy as np, pandas as pd
from backtest import btc_lib as L

m = L.load_markets()
b = L.load_binance()
piv = L.binance_matrix()

print("="*70); print("(iii) data integrity: coverage & garbage"); print("="*70)
print("markets:", len(m), "binance tickers:", b.ticker.nunique(),
      "missing binance:", len(set(m.ticker)-set(b.ticker)))
# per-window count of secs in [1,60]
sub = b[(b.sec_to_close>=1)&(b.sec_to_close<=60)]
cnt = sub.groupby("ticker").size()
print("windows with <60 settlement secs:", int((cnt<60).sum()),
      " with exactly 60:", int((cnt==60).sum()), " >60:", int((cnt>60).sum()))
print("min/median count in [1,60]:", cnt.min(), cnt.median())
# price sanity
print("binance price range:", b.price.min(), b.price.max())
print("true_settle range:", m.true_settle.min(), m.true_settle.max())
# duplicated (ticker,sec)?
dup = b.duplicated(subset=["ticker","sec_to_close"]).sum()
print("duplicate (ticker,sec) rows:", int(dup))

print("="*70); print("(ii) timestamp window alignment"); print("="*70)
# Build several candidate 60s settlement windows and de-bias each causally, then
# compare residual std to true_settle. The TRUE window minimises residual.
m = m.sort_values("close_dt").reset_index(drop=True)
def avg_window(lo, hi):
    cols = [c for c in range(lo, hi+1) if c in piv.columns]
    return piv[cols].mean(axis=1)
def causal_resid(series):
    mm = m.copy()
    mm["r"] = mm["ticker"].map(series)
    mm["err"] = mm["r"] - mm["true_settle"]
    mm["delta"] = mm["err"].shift(1).rolling(24, min_periods=20).median()
    resid = (mm["err"] - mm["delta"]).dropna()
    return resid
for (lo,hi,label) in [(1,60,"[1..60] (lib default)"),
                      (0,59,"[0..59]"),
                      (2,61,"[2..61]"),
                      (1,61,"[1..61] (61 samples)"),
                      (5,64,"[5..64]")]:
    r = causal_resid(avg_window(lo,hi))
    print(f"window {label:24s}: causal-debias resid std={r.std():.3f} "
          f"mean|r|={r.abs().mean():.3f} q99={r.abs().quantile(.99):.2f}")

print("="*70); print("(i) sharpened: reconstructed settle vs true_settle"); print("="*70)
# This is the SAME as [1..60] above but reported as the headline proxy fidelity.
r = causal_resid(avg_window(1,60))
print(f"de-biased Binance avg60 vs true_settle: mean={r.mean():+.3f} std={r.std():.3f} "
      f"q99|abs|={r.abs().quantile(.99):.2f}  => this is the REAL proxy residual")

print("="*70); print("(iv) true_settle plausibility"); print("="*70)
# true_settle should sit inside the min..max of the 60 settlement spot samples
# (de-biased) for nearly all windows.
chk = m.copy()
lo = piv[list(range(1,61))].min(axis=1); hi = piv[list(range(1,61))].max(axis=1)
chk["lo"] = chk["ticker"].map(lo); chk["hi"] = chk["ticker"].map(hi)
# de-bias the band by the trailing bias
chk["err"] = chk["ticker"].map(avg_window(1,60)) - chk["true_settle"]
chk["delta"] = chk["err"].shift(1).rolling(24, min_periods=20).median()
chk["lo_adj"] = chk["lo"]-chk["delta"]; chk["hi_adj"]=chk["hi"]-chk["delta"]
inside = ((chk["true_settle"]>=chk["lo_adj"]-30)&(chk["true_settle"]<=chk["hi_adj"]+30))
print(f"true_settle within de-biased [min-30,max+30] band: {inside.mean():.4f} of windows")
# strike == prior window settlement?
m2 = m.copy()
m2["prev_settle"] = m2["true_settle"].shift(1)
d = (m2["strike"]-m2["prev_settle"]).abs().dropna()
print(f"strike == prior window true_settle? median|diff|={d.median():.4f} "
      f"frac within 1$={float((d<1).mean()):.4f}")
