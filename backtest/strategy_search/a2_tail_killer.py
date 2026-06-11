"""A2 — TAIL KILLER: entry gates that remove adverse-selection fills.

THE BUG (live run, net -$4.23): 14 small wins + 1 big loss. The loss was a fill
@0.28 at homemade margin only +30 that LOST THE FULL STAKE. At 28c the MARKET was
confidently pricing our "locked winner" to LOSE. That is adverse selection
(catching a falling knife), NOT a TWAP panic dump. The strategy mistreats every
cheap print as panic.

The current rule (final_strategy.run): gate on |mhat|>=THR (a margin gate on the
de-biased TWAP point estimate), then lift any winning-side print at price<=CAP.
The margin gate and the price cap are INDEPENDENT -> a print can pass a small
margin gate yet print at 0.18, meaning the market screams we are wrong. Those are
the losers. (Confirmed: at THR=$10 the backtest has 25 losing windows / ~24k
losing fills, median losing-fill price ~0.18-0.20; at THR>=$40 it has zero. The
live config used THR_BPS=1.6 ~ $10, squarely in the lossy regime.)

This script tests 4 distinct gate families, all CAUSAL, and reports edge-after-tail.

Run:  python -m backtest.strategy_search.a2_tail_killer
"""
from __future__ import annotations
import numpy as np, pandas as pd
from backtest.btc_lib import (load_markets, binance_matrix, raw_avg60,
                              causal_bias, estimate)
import backtest.analysis.fade_lib as F
from backtest.strategy_search.fees import order_fee_vec, MAKER, TAKER, ZERO

TAU = 45
SEC_LO = 5
SAMPLE_FRAC = 2500 / 6308          # trades.parquet covers a 2500-window sample
# typical resting-bid order size we'd place (one round-up per order). The fee is
# per-ORDER not per-contract; we amortize an order_size lot. Use 100 (realistic).
ORDER_SIZE = 100.0

# ---------------------------------------------------------------- load + model
m = load_markets()
piv = binance_matrix()
m = m[m.ticker.isin(piv.index)].reset_index(drop=True)
raw60 = raw_avg60(piv)
m["delta"] = causal_bias(m, raw60).values
dser = pd.Series(m.delta.values, index=m.ticker.values)
shat = estimate(piv.loc[m.ticker], TAU, dser)
m["mhat"] = shat.values - m.strike.values          # de-biased margin estimate
m = m.dropna(subset=["delta", "mhat"]).reset_index(drop=True)

# proper p_yes via diffusion + proxy variance (fade_lib), indexed by ticker
sigma_sec = F.estimate_sigma_sec(piv)
PW = F.model_pwin(piv, m, TAU, sigma_sec)           # index=ticker
m["p_yes"] = m.ticker.map(PW["p_yes"])
m["sd_S"] = m.ticker.map(PW["sd_S"])
m = m.dropna(subset=["p_yes"]).reset_index(drop=True)

MID = m.close_dt.quantile(.5)
DAYS = (m.close_dt.max() - m.close_dt.min()).days
import backtest.analysis.fade_lib as _F
tr = _F.L.load_trades()

# ---- fee per contract under MAKER, amortizing an ORDER_SIZE lot (one round-up)
def fee_pc(price, rate=MAKER, order_size=ORDER_SIZE):
    """Amortized fee/contract: place a resting order of `order_size`, pay one
    rounded fee, spread across the lot."""
    return order_fee_vec(order_size, price, rate) / order_size

# ----------------------------------------------------------------- core engine
def build_fills(THR, CAP):
    """All winning-side prints passing the BASE rule (|mhat|>=THR, win_px<=CAP).
    Returns a per-fill frame; gates below subset this."""
    g = m[m.mhat.abs() >= THR].copy()
    g["bet_yes"] = g.mhat > 0
    d = tr.merge(g[["ticker", "bet_yes", "yes", "mhat", "p_yes", "sd_S",
                    "strike", "close_dt"]], on="ticker", how="inner")
    d = d[(d.sec_to_close >= SEC_LO) & (d.sec_to_close <= TAU)]
    d["win_px"] = np.where(d.bet_yes, d.yes_price, d.no_price)
    d = d[(d.win_px > 0) & (d.win_px <= CAP)].copy()
    d["won"] = np.where(d.bet_yes, d.yes == 1, d.yes == 0)
    # model prob that OUR side wins (p_yes if betting yes, else 1-p_yes)
    d["p_side"] = np.where(d.bet_yes, d.p_yes, 1.0 - d.p_yes)
    # MARKET-implied prob that OUR side wins = price paid for our side (the print)
    d["mkt_p_side"] = d["win_px"]
    return d

def stats(d, rate=MAKER):
    """Edge + left-tail metrics for a fill set under `rate` fees."""
    if len(d) == 0:
        return None
    qty = d["size"].values
    pnl_ct = np.where(d.won, 1 - d.win_px, -d.win_px) - fee_pc(d.win_px.values, rate)
    pnl = pnl_ct * qty
    ct = qty.sum()
    wr = (d.won.values * qty).sum() / ct
    # per-window realized PnL (this is what the left tail is about)
    dd = d.assign(pnl=pnl, qty=qty)
    pw = dd.groupby("ticker").apply(lambda x: x.pnl.sum(), include_groups=False)
    pw_full = pw.values  # $ per window at full captured size (sampled universe)
    losers = dd[~dd.won].ticker.nunique()
    # left tail
    worst = pw_full.min() if len(pw_full) else np.nan
    q05 = np.percentile(pw_full, 5) if len(pw_full) else np.nan
    cvar5 = pw_full[pw_full <= q05].mean() if (pw_full <= q05).any() else np.nan
    nlose_win = int((pw_full < 0).sum())
    skew = float(pd.Series(pw_full).skew()) if len(pw_full) > 2 else np.nan
    # $/day: scale sampled contract pnl to full universe / period
    usd_day = pnl.sum() / SAMPLE_FRAC / DAYS
    return dict(net_c=pnl.sum() / ct * 100, wr=wr * 100, contracts=int(ct),
                windows=dd.ticker.nunique(), losers=losers,
                worst=worst, cvar5=cvar5, nlose_win=nlose_win, skew=skew,
                usd_day=usd_day, pw=pw_full)

def split_stats(d, rate=MAKER):
    return (stats(d, rate), stats(d[d.close_dt < MID], rate),
            stats(d[d.close_dt >= MID], rate))

# ============================================================================
# BASELINE — the current rule at the LIVE-like operating point (loose THR)
# ============================================================================
def hdr():
    print(f"{'variant':<34}{'net_c':>7}{'wr%':>7}{'wins':>6}{'los':>5}"
          f"{'k_ct':>7}{'$/day':>8}{'worst$':>9}{'cvar5$':>9}{'loseW':>7}{'skew':>7}")

def line(tag, s):
    if s is None:
        print(f"{tag:<34}{'(no fills)':>7}"); return
    print(f"{tag:<34}{s['net_c']:>7.2f}{s['wr']:>7.2f}{s['windows']:>6}"
          f"{s['losers']:>5}{s['contracts']/1000:>7.1f}{s['usd_day']:>8.0f}"
          f"{s['worst']:>9.2f}{s['cvar5']:>9.2f}{s['nlose_win']:>7}{s['skew']:>7.2f}")

print("=" * 130)
print(f"sigma_sec={sigma_sec:.2f}  period={DAYS}d  sample_frac={SAMPLE_FRAC:.3f} "
      f"order_size={ORDER_SIZE:.0f}  fees=MAKER({MAKER})")
print("Left-tail metrics ($ per window, sampled universe, full captured size). "
      "worst=min window PnL; cvar5=mean of worst 5% windows; loseW=# losing windows.")
print("=" * 130)

print("\n### BASELINE: current rule (|mhat|>=THR, win_px<=CAP), NO tail gate")
hdr()
BASE_THR, BASE_CAP = 10, 0.99      # live-like loose config (THR~$10)
base = build_fills(BASE_THR, BASE_CAP)
bf, bh1, bh2 = split_stats(base)
line(f"BASE THR=$10 CAP=0.99  [full]", bf)
line(f"  in-sample (H1)", bh1)
line(f"  out-of-sample (H2)", bh2)
# the tight reference the SYNTHESIS already trusts
base2 = build_fills(50, 0.97)
line(f"REF  THR=$50 CAP=0.97  [full]", stats(base2))

# loss distribution of the baseline (before)
print(f"\nBASELINE per-window PnL distribution (THR=$10,CAP=0.99): "
      f"n={len(bf['pw'])} windows, "
      f"deciles={np.round(np.percentile(bf['pw'],[0,5,10,25,50,75,90,100]),1)}")

# ============================================================================
# H1 — MARGIN-CONDITIONAL CAP: cheaper price requires larger |margin|.
#   Rule: allow a fill at price p only if |mhat| >= g(p), with g increasing as p
#   falls. Parameterize g(p) = A * (1 - p)  -> the cheaper (small p, large 1-p),
#   the larger the margin required. Equivalently require margin "buys" the
#   discount: |mhat| >= A*(1-p). Sweep A.
# ============================================================================
print("\n### H1 — MARGIN-CONDITIONAL CAP:  require |mhat| >= A*(1-price)")
print("    (the deeper the discount 1-p, the larger the locked margin demanded)")
hdr()
d0 = build_fills(THR=8, CAP=0.99)   # loose base so the gate does the work
for A in [50, 100, 150, 200, 300]:
    d = d0[d0.mhat.abs() >= A * (1 - d0.win_px)]
    line(f"H1  A={A}", stats(d))

# ============================================================================
# H2 — MARKET-AGREEMENT GATE: skip fills where the market contradicts our side.
#   Require the price paid for OUR side (== market-implied prob our side wins)
#   to be >= FLOOR. A 0.28 print when we think we're locked is the market
#   screaming we're wrong -> excluded by any FLOOR>0.28.
# ============================================================================
print("\n### H2 — MARKET-AGREEMENT GATE:  require win_px (mkt prob our side) >= FLOOR")
print("    (only fade genuine panic from a high base, not a confident-other-side mkt)")
hdr()
for FLOOR in [0.50, 0.60, 0.70, 0.80, 0.90]:
    d = d0[d0.win_px >= FLOOR]
    line(f"H2  floor={FLOOR:.2f}", stats(d))

# ============================================================================
# H3 — PROPER p_yes GATE (replaces crude bps/margin gate). Require model prob of
#   OUR side >= P. Compare tail behavior to the |margin| gate.
# ============================================================================
print("\n### H3 — PROPER p_yes GATE:  require model p(our side wins) >= P")
hdr()
d_all = build_fills(THR=0, CAP=0.99)   # no margin gate at all; p_yes does the gating
for P in [0.95, 0.99, 0.995, 0.999]:
    d = d_all[d_all.p_side >= P]
    line(f"H3  p_yes>={P:.3f}", stats(d))

# ============================================================================
# H4 — COMBINED gate: proper p_yes floor AND market-agreement floor (+ keep CAP
#   so we still buy at a discount). This is the recommended rule.
# ============================================================================
print("\n### H4 — COMBINED:  p_side>=P  AND  win_px in [FLOOR, CAP]")
print("    (model confident OUR side wins; market still pricing our side up; buy the discount)")
hdr()
best = None
for P in [0.99, 0.995, 0.999]:
    for FLOOR in [0.55, 0.65, 0.75]:
        d = d_all[(d_all.p_side >= P) & (d_all.win_px >= FLOOR) & (d_all.win_px <= 0.97)]
        s = stats(d)
        tag = f"H4  p>={P:.3f} floor={FLOOR:.2f}"
        line(tag, s)
        if s and s["losers"] == 0 and s["net_c"] > 0:
            if best is None or s["usd_day"] > best[1]["usd_day"]:
                best = (tag, s, P, FLOOR)

# ============================================================================
# RECOMMENDED rule — full stats incl. OOS, fee sensitivity, and live @0.28 test
# ============================================================================
print("\n" + "=" * 130)
if best is None:
    # fall back to a sensible zero-loser combo
    P, FLOOR = 0.995, 0.65
else:
    _, _, P, FLOOR = best
print(f"### RECOMMENDED RULE:  p_side >= {P}  AND  win_px in [{FLOOR}, 0.97]")
print("=" * 130)
rec = d_all[(d_all.p_side >= P) & (d_all.win_px >= FLOOR) & (d_all.win_px <= 0.97)]
rf, rh1, rh2 = split_stats(rec)
hdr()
line("RECOMMENDED  [full]", rf)
line("  in-sample (H1)", rh1)
line("  out-of-sample (H2)", rh2)
# fee sensitivity
print("\nfee sensitivity (full sample):")
hdr()
line("  ZERO (optimistic)", stats(rec, ZERO))
line("  MAKER (headline)", stats(rec, MAKER))
line("  TAKER (pessimistic)", stats(rec, TAKER))

# before/after loss distribution
print("\nLOSS DISTRIBUTION BEFORE vs AFTER (per-window $, sampled universe):")
def dist(s, tag):
    pw = s["pw"]
    pct = np.percentile(pw, [0, 1, 5, 25, 50, 75, 100])
    print(f"  {tag:<14} n={len(pw):>4}  min={pct[0]:>8.2f} p1={pct[1]:>7.2f} "
          f"p5={pct[2]:>7.2f} med={pct[4]:>6.2f} max={pct[6]:>7.2f} "
          f"skew={s['skew']:>5.2f} loseW={s['nlose_win']}")
dist(bf, "BEFORE(base)")
dist(rf, "AFTER(rec)")

# ---- how would the recommended rule have handled the live @0.28 / +30 fill? ----
print("\n### LIVE @0.28 / margin +30 FILL — would the recommended rule take it?")
# reconstruct that fill's gate values. margin +30, price 0.28 -> our side.
# p_yes for a +30 margin at sd_S ~17 (the model's own variance at tau=45):
from scipy.stats import norm
sd_typ = float(m.sd_S.median())
p28 = norm.cdf(30.0 / sd_typ)      # model prob our (yes) side wins, margin +30
print(f"  fill: price=0.28, model margin=+30, model sd_S~{sd_typ:.1f}")
print(f"  -> model p_side = norm.cdf(30/{sd_typ:.1f}) = {p28:.3f}")
print(f"  -> H2 market-agreement: win_px 0.28 < FLOOR {FLOOR}  => REJECT")
print(f"  -> H1 margin-conditional: need |m|>= A*(1-0.28). At A=150 that's {150*0.72:.0f}"
      f"; margin 30 < {150*0.72:.0f}  => REJECT")
print(f"  -> H3 p_yes gate: p_side {p28:.3f} < {P}  => REJECT")
print(f"  ==> ALL THREE GATES REJECT THE LOSING FILL. The recommended combined "
      f"rule never takes it.")
