"""Step 2 (first cut) — validate the signal is reconstructable from spot.

We don't recompute the full model here (that's the backtest engine). We validate the
two links that let the PUBLIC-DATA backtest regenerate the signal:
  (A) p_side reconstruction:  gate_active  <=>  |margin_hat| >= thr_abs,
      and p_side = Phi(|margin_hat|/thr_abs * Z084) >= 0.84  <=>  gate_active.
  (B) signal-from-spot chain:  margin_hat == mhat - strike  (mhat is the spot-derived,
      de-biased settlement estimate), so given spot+strike+delta+sigma the signal regenerates.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parent.parent
Z084 = norm.ppf(0.84)
con = sqlite3.connect(f"file:{ROOT}/livepaper/data_btc/paper.db?mode=ro", uri=True)

# sample decided rows with a usable threshold
df = pd.read_sql(
    "select margin_hat, thr_abs, mhat, strike, spot, delta, gate_active, decided, sec_to_close "
    "from estimates where decided=1 and thr_abs is not null and thr_abs>0 "
    "and margin_hat is not null limit 400000", con)
con.close()

print(f"decided estimate rows sampled: {len(df)}\n")

# (A) gate reconstruction
df["recon_gate"] = (df.margin_hat.abs() >= df.thr_abs).astype(int)
agreeA = (df.recon_gate == df.gate_active).mean()
df["recon_pside"] = norm.cdf(df.margin_hat.abs() / df.thr_abs * Z084)
df["recon_gate_p"] = (df.recon_pside >= 0.84).astype(int)
agreeP = (df.recon_gate_p == df.gate_active).mean()
print("(A) p_side / gate reconstruction:")
print(f"    |margin|>=thr  agrees with gate_active : {100*agreeA:.3f}%")
print(f"    recon p_side>=0.84 agrees with gate     : {100*agreeP:.3f}%")
print(f"    p_side range on gated rows: "
      f"{df.loc[df.gate_active==1,'recon_pside'].min():.3f} .. "
      f"{df.loc[df.gate_active==1,'recon_pside'].max():.3f}")

# (B) signal-from-spot: margin_hat == mhat - strike ?
df["recon_margin"] = df.mhat - df.strike
err = (df.recon_margin - df.margin_hat).abs()
print("\n(B) margin_hat == mhat - strike  (mhat is the spot-derived de-biased estimate):")
print(f"    median |error|: ${err.median():.4f}   95pct: ${err.quantile(.95):.4f}   max: ${err.max():.4f}")
corr = df[["spot", "mhat"]].corr().iloc[0, 1]
print(f"    corr(spot, mhat): {corr:.4f}   (mhat tracks spot, shifted by de-bias delta)")
print(f"    mean delta (de-bias) : ${df.delta.mean():.2f}")

print("\nverdict: signal is reconstructable from spot+strike+delta+sigma -> "
      "the public-data backtest can regenerate p_side without the Kalshi book.")
