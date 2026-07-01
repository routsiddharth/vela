"""Step 3 — fill-model calibration table (the keystone for notebook 04).

Population: panic-fade MAKER resting bids (orders.action='place', status='resting').
For each, attach features from the book + model estimate at placement (<=2s, validated
100% coverage in step 5), label filled/not via 'live maker fill' rows, and attach the
window PnL/outcome. Then:
  - overall maker fill rate
  - Beta-Binomial posterior P(fill | bucket) with 95% credible intervals
  - ADVERSE SELECTION: win-rate of filled trades vs the model p_side that justified them
  - E[PnL | fill]

Writes analysis/data/fill_calibration_btc.parquet.
"""
from __future__ import annotations
import sqlite3, sys
from pathlib import Path
import pandas as pd
from scipy.stats import norm, beta

sys.path.insert(0, str(Path(__file__).resolve().parent))
import regimes

ROOT = Path(__file__).resolve().parent.parent
Z084 = norm.ppf(0.84)   # gate z-score: p_side = Phi(|margin|/thr_abs * Z084)

con = sqlite3.connect(f"file:{ROOT}/livepaper/data_btc/paper.db?mode=ro", uri=True)


def nearest(table, cols, ticker, ts, win=2000):
    row = con.execute(
        f"select {cols}, abs(ts_ms-?) dt from {table} "
        f"where ticker=? and ts_ms between ? and ? order by dt limit 1",
        (ts, ticker, ts - win, ts + win)).fetchone()
    return row


# one maker attempt per (ticker): the first resting placement (re-places = same opportunity)
orders = con.execute(
    "select ticker, side, min(ts_ms) ts, avg(price) price, avg(count) ct "
    "from orders where action='place' and status='resting' group by ticker"
).fetchall()

# window outcomes
wins = {r[0]: r for r in con.execute(
    "select ticker, net_pnl, won, true_settle, close_ts from windows").fetchall()}

# maker fills index: ticker -> list of (ts, side, price, qty)
mfills = {}
for ts, tk, side, px, qty in con.execute(
    "select ts_ms, ticker, bet_side, price, qty from fills where reason like 'live maker%'"):
    mfills.setdefault((tk, side), []).append((ts, px, qty))

rows = []
for ticker, side, ts, our_bid, ct in orders:
    bk = nearest("book_snaps",
                 "best_yes_bid,yes_bid_sz,best_no_bid,no_bid_sz,yes_ask,no_ask,depth_yes,depth_no,sec_to_close",
                 ticker, ts)
    es = nearest("estimates", "margin_hat,thr_abs,s_hat_binance,spot,sec_to_close", ticker, ts)
    if bk is None or es is None:
        continue
    (byb, ybsz, bnb, nbsz, ya, na, dy, dn, bk_sec, _bdt) = bk
    (margin, thr, shat, spot, est_sec, _edt) = es

    # our side's book: bid/ask/mid + how far below mid we rested + depth ahead (queue proxy)
    if side == "yes":
        best_bid, ask, our_depth = byb, ya, dy
    else:
        best_bid, ask, our_depth = bnb, na, dn
    mid = (best_bid + ask) / 2 if (best_bid is not None and ask is not None) else None
    dist_to_mid = (mid - our_bid) if mid is not None else None     # >0: we rest below mid
    spread = (ask - best_bid) if (ask is not None and best_bid is not None) else None

    p_side = norm.cdf(abs(margin) / thr * Z084) if thr and thr > 0 else None
    ttc = bk_sec if bk_sec is not None else est_sec

    # filled? any maker fill on (ticker, side) at/after placement, within the window life
    filled = 0
    fpx = None
    for fts, px, qty in mfills.get((ticker, side), []):
        if ts - 2000 <= fts <= ts + 70000:
            filled = 1; fpx = px; break

    w = wins.get(ticker)
    net_pnl = w[1] if (w and filled) else (0.0 if w else None)
    won = w[2] if w else None

    rows.append(dict(
        ticker=ticker, side=side, ts_ms=ts, regime=regimes.label(ts),
        our_bid=our_bid, contracts=ct, best_bid=best_bid, ask=ask, mid=mid,
        dist_to_mid=dist_to_mid, spread=spread, our_depth=our_depth,
        time_to_close=ttc, p_side=p_side, vol_shat=shat, spot=spot,
        filled=filled, fill_px=fpx, net_pnl=net_pnl, won=won))

con.close()
df = pd.DataFrame(rows)
OUT = ROOT / "analysis/data/fill_calibration_btc.parquet"
df.to_parquet(OUT, index=False)

# ----------------------------------------------------------------------------- report
N = len(df); F = int(df.filled.sum())
print(f"maker-fade attempts (distinct tickers): {N}")
print(f"filled: {F}   overall fill rate: {100*F/N:.1f}%\n")

print("fill rate by regime:")
for reg, g in df.groupby("regime"):
    k, n = int(g.filled.sum()), len(g)
    print(f"  {reg:11} {k:4}/{n:<4} = {100*k/n:5.1f}%")


def bb(g):
    k, n = int(g.filled.sum()), len(g)
    a, b = 1 + k, 1 + (n - k)
    return n, k, (a) / (a + b), beta.ppf(.025, a, b), beta.ppf(.975, a, b)


print("\nP(fill | distance-of-bid-below-mid)  [Beta-Binomial posterior, 95% CI]:")
df["dist_bucket"] = pd.cut(df.dist_to_mid, [-1, 0, .02, .05, .10, 1],
                           labels=["<=0 (at/above mid)", "0-2c", "2-5c", "5-10c", ">10c"])
for bkt, g in df.groupby("dist_bucket", observed=True):
    n, k, m, lo, hi = bb(g)
    print(f"  {str(bkt):20} n={n:4} fill={100*m:5.1f}%  [{100*lo:4.1f}, {100*hi:4.1f}]")

print("\nP(fill | time-to-close):")
df["ttc_bucket"] = pd.cut(df.time_to_close, [-5, 5, 15, 30, 45, 100],
                          labels=["0-5s", "5-15s", "15-30s", "30-45s", ">45s"])
for bkt, g in df.groupby("ttc_bucket", observed=True):
    n, k, m, lo, hi = bb(g)
    print(f"  {str(bkt):10} n={n:4} fill={100*m:5.1f}%  [{100*lo:4.1f}, {100*hi:4.1f}]")

# ---- ADVERSE SELECTION: do filled trades win as often as the model expected? ----
fl = df[df.filled == 1].dropna(subset=["won", "p_side"])
unfl = df[df.filled == 0].dropna(subset=["p_side"])
print("\n=== ADVERSE SELECTION ===")
print(f"  model p_side at decision (filled trades) : {fl.p_side.mean():.4f}")
print(f"  REALIZED win rate (filled trades)        : {fl.won.mean():.4f}  (n={len(fl)})")
print(f"  gap (realized - model)                   : {fl.won.mean()-fl.p_side.mean():+.4f}")
print(f"  mean p_side of UNFILLED                  : {unfl.p_side.mean():.4f}  (n={len(unfl)})")
print(f"  E[PnL | fill] per attempt                : ${fl.net_pnl.mean():.4f}")
print(f"  mean contracts (filled)                  : {fl.contracts.mean():.1f}")
print(f"\nwritten -> {OUT.relative_to(ROOT)}  ({len(df.columns)} cols)")
