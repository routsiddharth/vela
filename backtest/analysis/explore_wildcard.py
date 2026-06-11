"""Wildcard inefficiency hunt for KXBTC15M (and ETH lead-lag).

Mandate: find a STRUCTURAL edge NOT covered by the panic-fade / momentum /
market-making / ladder / calibration / order-flow agents. Focus:
  (e) outcome autocorrelation / streaks
  (a) time-of-day / session effects
  (c) BTC -> ETH cross-asset lead-lag
plus reality checks (open-price, round-number).

EMPIRICAL + brutally honest. Everything gated by: significance (CIs, sample),
OOS split (time-ordered), and FEES at the price you'd actually trade.

Run:  source ../../ingest/venv/bin/activate && python explore_wildcard.py
"""
from __future__ import annotations
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
np.set_printoptions(suppress=True)


# ---------------------------------------------------------------- fee model
def taker_fee(p: float) -> float:
    """Kalshi taker fee/contract = ceil_cent(0.07*p*(1-p)), min $0.01."""
    raw = 0.07 * p * (1 - p)
    cents = math.ceil(raw * 100 - 1e-9)
    return max(cents, 1) / 100.0


def maker_fee(p: float, qty: float = 1.0) -> float:
    raw = 0.0175 * qty * p * (1 - p)
    cents = math.ceil(raw * 100 - 1e-9)
    return max(cents, 0) / 100.0  # maker can be 0 on tiny qty, but per-order rounds


# ------------------------------------------------------------- stats helpers
def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (p, (c - h) / d, (c + h) / d)


def binom_p_twosided(k: int, n: int, p0: float = 0.5):
    """Two-sided binomial test p-value vs p0 (normal approx for big n)."""
    if n == 0:
        return 1.0
    mu = n * p0
    sd = math.sqrt(n * p0 * (1 - p0))
    z = (abs(k - mu) - 0.5) / sd  # continuity-corrected
    # two-sided
    from math import erfc
    return erfc(z / math.sqrt(2))


def load_markets() -> pd.DataFrame:
    m = pd.read_parquet(DATA / "markets.parquet").sort_values("close_dt").reset_index(drop=True)
    m["close_dt"] = pd.to_datetime(m["close_dt"])
    m["up"] = (m["result"] == "yes").astype(int)
    m["ret"] = m["margin"].astype(float)  # settle - strike (dollars)
    m["hour"] = m["close_dt"].dt.hour
    m["dow"] = m["close_dt"].dt.dayofweek  # 0=Mon
    m["date"] = m["close_dt"].dt.date
    # contiguous-run id: break runs where gap != 15 min
    gap = m["close_dt"].diff().dt.total_seconds().div(60)
    m["newrun"] = (gap != 15.0).astype(int)
    m["runid"] = m["newrun"].cumsum()
    return m


def section(t):
    print("\n" + "=" * 78)
    print(t)
    print("=" * 78)


# ============================================================= (e) STREAKS
def analyze_autocorr(m: pd.DataFrame):
    section("(e) OUTCOME AUTOCORRELATION / STREAKS")
    up = m["up"].values
    n = len(up)
    # lag-k autocorr of the up/down sequence (only within contiguous runs)
    print(f"n windows = {n}, base up-rate = {up.mean():.4f}")
    for lag in (1, 2, 3, 4):
        # pair only consecutive within same run
        a, b = [], []
        runid = m["runid"].values
        for i in range(lag, n):
            if runid[i] == runid[i - lag]:
                a.append(up[i - lag])
                b.append(up[i])
        a = np.array(a); b = np.array(b)
        r = np.corrcoef(a, b)[0, 1]
        # P(up | prev up) vs P(up | prev down)
        pu = b[a == 1].mean() if (a == 1).any() else float("nan")
        pd_ = b[a == 0].mean() if (a == 0).any() else float("nan")
        print(f"  lag {lag}: r={r:+.4f}  P(up|prevUp)={pu:.4f}  P(up|prevDown)={pd_:.4f}  "
              f"spread={pu-pd_:+.4f}  npairs={len(a)}")

    # Runs test on the binary sequence (within-run concatenation is fine for sign of dependence)
    section("  Runs test (Wald-Wolfowitz) for serial dependence")
    x = up
    n1 = int(x.sum()); n0 = len(x) - n1
    runs = 1 + int((x[1:] != x[:-1]).sum())
    mu = 1 + 2 * n1 * n0 / (n1 + n0)
    var = 2 * n1 * n0 * (2 * n1 * n0 - n1 - n0) / ((n1 + n0) ** 2 * (n1 + n0 - 1))
    z = (runs - mu) / math.sqrt(var)
    print(f"  runs={runs} expected={mu:.1f}  z={z:+.3f}  "
          f"({'MORE alternation (mean-revert)' if z>0 else 'MORE clustering (momentum)'})")

    # Directional: does prev RETURN (continuous) predict next return? (regression toward edge)
    section("  Continuous: does prev window's signed margin predict next up/down?")
    df = m.copy()
    df["prev_ret"] = df["ret"].shift(1)
    df["prev_same_run"] = (df["runid"] == df["runid"].shift(1))
    d = df[df["prev_same_run"]].dropna(subset=["prev_ret"])
    # bucket by prev_ret sign and magnitude
    for lo, hi, lbl in [(-1e9, 0, "prev DOWN"), (0, 1e9, "prev UP")]:
        sub = d[(d.prev_ret > lo) & (d.prev_ret <= hi)]
        p, lo_ci, hi_ci = wilson(int(sub.up.sum()), len(sub))
        print(f"    {lbl:10s} -> next up={p:.4f} [{lo_ci:.4f},{hi_ci:.4f}] n={len(sub)}")
    # big-move buckets
    print("  By prev |margin| magnitude (does a big prev move mean-revert the NEXT outcome?):")
    for q in [(0, 25), (25, 50), (50, 75), (75, 100)]:
        ql, qh = np.percentile(d.prev_ret.abs(), q[0]), np.percentile(d.prev_ret.abs(), q[1])
        sub = d[(d.prev_ret.abs() >= ql) & (d.prev_ret.abs() < qh)]
        # signal: predict OPPOSITE of prev direction
        pred_up = (sub.prev_ret < 0).astype(int)  # mean-revert
        hit = (pred_up.values == sub.up.values).mean()
        p, l, h = wilson(int((pred_up.values == sub.up.values).sum()), len(sub))
        print(f"    |prev_ret| in [{ql:7.1f},{qh:7.1f}): mean-revert hit={hit:.4f} [{l:.4f},{h:.4f}] n={len(sub)}")
    return d


# ============================================================ (a) TIME-OF-DAY
def analyze_tod(m: pd.DataFrame):
    section("(a) TIME-OF-DAY / SESSION  (UTC hour of close)")
    print("  Per-hour up-rate (is any hour biased away from 0.50?):")
    rows = []
    for h, g in m.groupby("hour"):
        p, l, hi = wilson(int(g.up.sum()), len(g))
        pv = binom_p_twosided(int(g.up.sum()), len(g))
        rows.append((h, len(g), p, l, hi, pv))
    rows.sort(key=lambda r: r[2])
    print("  hour   n    up      95%CI            p(vs.5)")
    for h, n, p, l, hi, pv in sorted(rows):
        flag = " <<<" if pv < 0.05 else ""
        print(f"   {h:2d}  {n:4d}  {p:.4f}  [{l:.4f},{hi:.4f}]  {pv:.3f}{flag}")
    # multiple comparisons: 24 hours, expect ~1.2 false positives at .05
    sig = [r for r in rows if r[5] < 0.05]
    print(f"  -> {len(sig)} of 24 hours significant at .05 (expect ~1.2 by chance). "
          f"Bonferroni .05/24={0.05/24:.4f}: "
          f"{sum(1 for r in rows if r[5] < 0.05/24)} survive.")

    section("  Per-hour ABSOLUTE move (volatility) & realized vol by session")
    vol = m.groupby("hour")["ret"].agg(["mean", "std", lambda s: s.abs().median()])
    vol.columns = ["mean_ret", "std_ret", "med_abs_ret"]
    print(vol.round(2).to_string())

    section("  Day-of-week up-rate")
    for d, g in m.groupby("dow"):
        p, l, hi = wilson(int(g.up.sum()), len(g))
        pv = binom_p_twosided(int(g.up.sum()), len(g))
        nm = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d]
        print(f"   {nm}  n={len(g):4d}  up={p:.4f} [{l:.4f},{hi:.4f}]  p={pv:.3f}")

    section("  US equity open window (13:30 UTC) & weekend low-liq")
    we = m[m.dow >= 5]
    wd = m[m.dow < 5]
    for lbl, g in [("weekday", wd), ("weekend", we)]:
        print(f"   {lbl}: up={g.up.mean():.4f}  med|ret|={g.ret.abs().median():.2f}  "
              f"std_ret={g.ret.std():.1f}  n={len(g)}")


# ====================================================== round-number (b) quick
def analyze_round(m: pd.DataFrame):
    section("(b) ROUND-NUMBER MAGNETISM (quick check)")
    # distance of strike to nearest $500 and $100; does settle drift toward round?
    for rnd in (1000, 500, 100):
        nearest = (m["strike"] / rnd).round() * rnd
        dist0 = m["strike"] - nearest  # signed dist of strike to round level
        # does settle move TOWARD the round number? i.e. sign(ret) == -sign(dist0)?
        move_toward = (np.sign(m["ret"]) == -np.sign(dist0))
        # only where strike is within $X of round
        near = m[dist0.abs() < rnd * 0.15].copy()
        nd = (m["strike"] - nearest)[dist0.abs() < rnd * 0.15]
        toward = (np.sign(near["ret"]) == -np.sign(nd)).mean()
        p, l, h = wilson(int((np.sign(near["ret"]) == -np.sign(nd)).sum()), len(near))
        print(f"   round ${rnd}: P(settle moves TOWARD round | within 15%)={toward:.4f} "
              f"[{l:.4f},{h:.4f}] n={len(near)}")


# ============================================ backtest a directional signal
def backtest_directional(m: pd.DataFrame, signal_col: str, entry_price: float,
                         label: str, oos_frac: float = 0.5):
    """Trade YES (up) or NO (down) per `signal_col` (1=bet up, 0=bet down, -1=skip).
    Buy the chosen side as a TAKER at `entry_price` (the realistic ATM open price).
    Net = payout(1 if correct else 0) - entry_price - taker_fee(entry_price).
    """
    d = m.dropna(subset=[signal_col]).copy()
    d = d[d[signal_col] >= 0]
    if len(d) == 0:
        print(f"   {label}: no trades")
        return
    bet_up = d[signal_col].astype(int).values
    win = (bet_up == d["up"].values).astype(int)
    fee = taker_fee(entry_price)
    # PnL per contract: win pays $1 for entry_price cost; lose costs entry_price
    pnl = np.where(win == 1, 1.0 - entry_price, -entry_price) - fee
    n = len(pnl)
    # time-split OOS
    k = int(n * oos_frac)
    ins, oos = pnl[:k], pnl[k:]
    p, l, h = wilson(int(win.sum()), n)
    print(f"   {label}: n={n} win={p:.4f}[{l:.4f},{h:.4f}] "
          f"net/ct={pnl.mean()*100:+.3f}c  IS={ins.mean()*100:+.3f}c OOS={oos.mean()*100:+.3f}c "
          f"(entry={entry_price:.2f} fee={fee*100:.1f}c)")
    return pnl


def clean_outliers(m: pd.DataFrame) -> pd.DataFrame:
    """Drop the 2 garbage windows whose strike != prior settle (|margin|>2000).
    These were inflating the *continuous* return autocorr to a spurious +0.50."""
    bad = m["ret"].abs() >= 2000
    if bad.any():
        print(f"  [clean] dropping {int(bad.sum())} garbage windows (|margin|>=2000)")
    out = m[~bad].sort_values("close_dt").reset_index(drop=True)
    gap = out["close_dt"].diff().dt.total_seconds().div(60)
    out["runid"] = (gap != 15.0).cumsum()
    return out


def analyze_return_autocorr(m: pd.DataFrame):
    section("RETURN AUTOCORRELATION (continuous) — artifact check")
    # raw (with outliers)
    raw = m.copy()
    mask = raw["runid"].values[1:] == raw["runid"].values[:-1]
    a = raw["ret"].values[:-1][mask]; b = raw["ret"].values[1:][mask]
    print(f"  RAW margin lag1 autocorr (incl outliers): {np.corrcoef(a, b)[0,1]:+.4f}")
    c = clean_outliers(m)
    mask = c["runid"].values[1:] == c["runid"].values[:-1]
    a = c["ret"].values[:-1][mask]; b = c["ret"].values[1:][mask]
    print(f"  CLEAN margin lag1 autocorr (|m|<2000):   {np.corrcoef(a, b)[0,1]:+.4f}  "
          f"<- TRUE value ~0 (no continuous-return edge)")


def analyze_meanrev_economics(m: pd.DataFrame):
    section("OUTCOME MEAN-REVERSION — clean effect + economics + sizing")
    c = clean_outliers(m)
    c["prev_up"] = c["up"].shift(1)
    c["prev_same"] = c["runid"] == c["runid"].shift(1)
    d = c[c["prev_same"]].dropna(subset=["prev_up"]).copy()
    d["revert"] = (1 - d["prev_up"]).astype(int)  # bet opposite of prev outcome
    d["win"] = (d["revert"] == d["up"]).astype(int)
    n = len(d); w = d["win"].mean()
    p, lo, hi = wilson(int(d["win"].sum()), n)
    se = math.sqrt(w * (1 - w) / n); z = (w - 0.5) / se
    print(f"  revert win = {w:.4f} [{lo:.4f},{hi:.4f}] n={n}  z={z:.2f}  "
          f"p={binom_p_twosided(int(d['win'].sum()), n):.4f}")
    print(f"  P(up|prevUp)={d[d.prev_up==1].up.mean():.4f}  "
          f"P(up|prevDown)={d[d.prev_up==0].up.mean():.4f}")
    k = int(n * 0.6)
    print(f"  IS(60%)={d.win.iloc[:k].mean():.4f}  OOS(40%)={d.win.iloc[k:].mean():.4f}")
    d["month"] = d["close_dt"].dt.strftime("%Y-%m")
    print("  by month:", {mo: round(g.win.mean(), 4) for mo, g in d.groupby("month")})
    print("\n  ECONOMICS (must enter the reverted side cheaper than win-rate minus fee):")
    for q in (0.46, 0.47, 0.48, 0.49, 0.50):
        e = w * (1 - q) - (1 - w) * q - taker_fee(q)
        print(f"    taker buy reverted @ {q:.2f}: E/ct = {e*100:+.3f}c  (fee {taker_fee(q)*100:.0f}c)")
    print("\n  -> Edge ~2pp. At a fair 0.50 open it is BREAKEVEN after the 2c taker fee.")
    print("     Profitable ONLY if you can routinely BUY the reverted side <= 0.49,")
    print("     i.e. the market must open the new window LEANING toward momentum")
    print("     (mid>0.50 on the prev-direction side). Needs live book to confirm.")
    return d


def analyze_meanrev_adverse_selection(m: pd.DataFrame):
    """THE KILL TEST for the mean-reversion edge.

    The 52% reversion is real, but to profit you must BUY the reverted side
    cheap (<=0.49). The reverted side is cheap only when early intra-window spot
    has leaned the OTHER way (momentum side). We proxy the open lean with Binance
    spot 1 minute into the window vs the strike, and ask: conditional on the
    reverted side being cheap, what is the reversion win rate?
    """
    section("MEAN-REVERSION — ADVERSE-SELECTION KILL TEST")
    btc = pd.read_parquet(DATA / "binance_1m_full.parquet")
    btc["dt"] = pd.to_datetime(btc.open_ms, unit="ms", utc=True)
    bmap = btc.set_index("dt")["close"]
    c = clean_outliers(m)
    c["open_dt"] = c["close_dt"] - pd.Timedelta(minutes=15)
    c["prev_up"] = c["up"].shift(1)
    c["prev_same"] = c["runid"] == c["runid"].shift(1)

    def spot_at(t):
        i = bmap.index.searchsorted(t)
        return bmap.iloc[i] if i < len(bmap.index) else np.nan

    c["spot1"] = c["open_dt"].apply(lambda t: spot_at(t + pd.Timedelta(minutes=1)))
    d = c[c["prev_same"]].dropna(subset=["prev_up", "spot1"]).copy()
    d["early_up_lean"] = (d["spot1"] > d["strike"]).astype(int)
    d["revert"] = (1 - d["prev_up"]).astype(int)
    d["win"] = (d["revert"] == d["up"]).astype(int)
    d["revert_cheap"] = d["early_up_lean"] != d["revert"]
    sc = d[d["revert_cheap"]]; se = d[~d["revert_cheap"]]
    print(f"  reverted side CHEAP (you'd buy it):     win={sc.win.mean():.4f} n={len(sc)}")
    print(f"  reverted side EXPENSIVE (can't enter):  win={se.win.mean():.4f} n={len(se)}")
    print(f"  early up-lean predicts up: P(up|lean=1)={d[d.early_up_lean==1].up.mean():.4f} "
          f"vs P(up|lean=0)={d[d.early_up_lean==0].up.mean():.4f}")
    print("  -> KILLED: when the reverted side is cheap it WINS only ~42% -> net LOSS.")
    print("     Intra-window momentum is priced into the open. Market is efficient.")


def analyze_open_quote_paper():
    section("(d) OPENING QUOTE — feasibility probe (livepaper, small n)")
    import sqlite3
    db = Path(__file__).resolve().parent.parent.parent / "livepaper" / "data" / "paper.db"
    if not db.exists():
        print("  paper.db not found; skipping")
        return
    con = sqlite3.connect(str(db))
    b = pd.read_sql("select ticker,sec_to_close,best_yes_bid,best_no_bid from book_snaps", con)
    w = pd.read_sql("select ticker,result from windows", con)
    con.close()
    b["series"] = b.ticker.str.extract(r"(KX[A-Z0-9]+?)-")
    b = b[b.series.isin(["KXBTC15M", "KXETH15M"])].copy()
    b["mid"] = (b.best_yes_bid + (1 - b.best_no_bid)) / 2
    early = (b[b.sec_to_close > 600].sort_values("sec_to_close", ascending=False)
             .groupby("ticker").first().reset_index())
    print(f"  open mid: mean={early.mid.mean():.3f} std={early.mid.std():.3f} "
          f"n={len(early)} (|lean|>3c in {(early.mid.sub(.5).abs()>.03).mean()*100:.0f}%)")
    e = early.merge(w, on="ticker", how="inner")
    e["up"] = (e.result == "yes").astype(int)
    if len(e) >= 5:
        acc = ((e.mid > 0.5).astype(int) == e.up).mean()
        print(f"  open-mid>0.5 predicts outcome: acc={acc:.3f} n={len(e)} "
              f"(NOTE: clustered ~6h, not a real OOS sample; CI spans 0.50)")
    print("  -> Market opens ~fair; the open lean is mostly the spot read, not a faddable error.")


def main():
    m = load_markets()
    section("DATA SUMMARY")
    print(f"  {len(m)} KXBTC15M windows  {m.close_dt.min()} -> {m.close_dt.max()}")
    print(f"  base up-rate = {m.up.mean():.4f}  (coin flip baseline)")
    print(f"  median |margin| = ${m.ret.abs().median():.2f}  std=${m.ret.std():.1f}")

    d = analyze_autocorr(m)
    analyze_return_autocorr(m)
    analyze_tod(m)
    analyze_round(m)
    analyze_meanrev_economics(m)
    analyze_meanrev_adverse_selection(m)
    analyze_open_quote_paper()

    section("DIRECTIONAL BACKTESTS (taker at realistic ATM price ~0.50)")
    print("  Fee at p=0.50 = %.1fc/ct (taker). Need >%.1fc edge to break even.\n"
          % (taker_fee(0.50) * 100, taker_fee(0.50) * 100))
    # signal A: mean-revert prev outcome
    md = m.copy()
    md["prev_up"] = md["up"].shift(1)
    md["prev_same"] = md["runid"] == md["runid"].shift(1)
    md.loc[~md["prev_same"], "prev_up"] = np.nan
    md["sig_revert"] = (1 - md["prev_up"])  # bet opposite of prev
    md["sig_momentum"] = md["prev_up"]      # bet same as prev
    backtest_directional(md, "sig_revert", 0.50, "mean-revert prev outcome @0.50")
    backtest_directional(md, "sig_momentum", 0.50, "momentum prev outcome  @0.50")
    # only when prev move was BIG
    md["prev_ret"] = md["ret"].shift(1)
    md.loc[~md["prev_same"], "prev_ret"] = np.nan
    big = md[md["prev_ret"].abs() > md["prev_ret"].abs().median()].copy()
    backtest_directional(big, "sig_revert", 0.50, "mean-revert after BIG prev move @0.50")

    print("\nDone. See explore_wildcard.md for interpretation.")


if __name__ == "__main__":
    main()
