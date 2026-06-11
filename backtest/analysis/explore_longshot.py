"""Favorite-longshot / systematic-mispricing calibration test for KXBTC15M.

Pure statistical-calibration edge: NO underlying-price modeling. We join executed
trades (yes_price) to market outcomes (result) and ask, per price bucket, what is
the REALIZED win rate of a YES contract bought at that price vs the price itself.

Caveat baked in: backtest/data/trades.parquet is ONLY the final 180s of each
KXBTC15M market (sec_to_close <= 180). Late-window prices are heavily resolved
already (U-shaped). We therefore slice by sec_to_close windows and also pull a
cleaner EARLIER-window sample via REST (see fetch_early_trades()) when asked.

Run:
  python backtest/analysis/explore_longshot.py            # full calibration
  python backtest/analysis/explore_longshot.py --fetch N  # also REST-fetch N mkts of full-window trades
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# ---------- Kalshi fee model (from prompt) ----------
def taker_fee_ct(p: float) -> float:
    """taker fee per contract = ceil_cent(0.07*p*(1-p)), min $0.01."""
    raw = 0.07 * p * (1.0 - p)
    cents = math.ceil(raw * 100 - 1e-9)
    return max(cents, 1) / 100.0


def maker_fee_ct(p: float) -> float:
    """maker fee per contract (qty=1) = ceil_cent(0.0175*p*(1-p)). No stated min."""
    raw = 0.0175 * p * (1.0 - p)
    cents = math.ceil(raw * 100 - 1e-9)
    return cents / 100.0


# ---------- stats helpers ----------
def wilson_ci(k: int, n: int, z: float = 1.96):
    """Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 0.0, 0.0)
    phat = k / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    half = (z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))) / denom
    return (phat, center - half, center + half)


PRICE_EDGES = [0.0, 0.02, 0.05, 0.10, 0.20, 0.30, 0.40, 0.50,
               0.60, 0.70, 0.80, 0.90, 0.95, 0.98, 1.0]
PRICE_MIDS = {  # representative price for fee calc per bucket (use bucket mean in practice)
}


def load():
    t = pd.read_parquet(DATA / "trades.parquet")
    m = pd.read_parquet(DATA / "markets.parquet")
    m = m[["ticker", "result", "strike", "true_settle", "margin"]].copy()
    m["yes_won"] = (m.result == "yes").astype(int)
    df = t.merge(m, on="ticker", how="inner")
    return df


def calibration_table(df: pd.DataFrame, label: str, weight_by_size: bool = False):
    """Per price bucket: trade count, actual YES win rate, price, mispricing, CI."""
    df = df.copy()
    df["bucket"] = pd.cut(df.yes_price, PRICE_EDGES, include_lowest=True)
    rows = []
    for b, g in df.groupby("bucket", observed=True):
        n = len(g)
        if weight_by_size:
            # size-weighted win rate (capacity-aware), but CI uses trade count n
            w = g["size"].values
            wr = float(np.average(g.yes_won.values, weights=w))
            k = int(round(wr * n))
        else:
            k = int(g.yes_won.sum())
            wr = k / n if n else 0.0
        phat, lo, hi = wilson_ci(k, n)
        price = float(g.yes_price.mean())
        rows.append(dict(
            bucket=str(b), n=n, price=round(price, 4),
            actual_winrate=round(wr, 4),
            mispricing=round(wr - price, 4),
            ci_lo=round(lo, 4), ci_hi=round(hi, 4),
            ci_lo_mispr=round(lo - price, 4), ci_hi_mispr=round(hi - price, 4),
            avg_size=round(g["size"].mean(), 1),
        ))
    out = pd.DataFrame(rows)
    print(f"\n===== CALIBRATION [{label}] (n_trades={len(df):,}) =====")
    print(out.to_string(index=False))
    return out


def market_level_calibration(df: pd.DataFrame, label: str, sec_lo: float, sec_hi: float):
    """De-bias from intra-market trade clustering: take ONE representative price per
    market within a sec_to_close window (the VWAP of trades in that window), then
    bucket markets. This gives one observation per market => clean binomial."""
    w = df[(df.sec_to_close >= sec_lo) & (df.sec_to_close < sec_hi)].copy()
    if w.empty:
        print(f"[{label}] no trades in window [{sec_lo},{sec_hi})")
        return None
    # VWAP yes_price per market in this window
    g = w.groupby("ticker").apply(
        lambda x: pd.Series({
            "vwap": np.average(x.yes_price, weights=x["size"]),
            "yes_won": x.yes_won.iloc[0],
        }), include_groups=False
    ).reset_index()
    g["bucket"] = pd.cut(g.vwap, PRICE_EDGES, include_lowest=True)
    rows = []
    for b, gg in g.groupby("bucket", observed=True):
        n = len(gg)
        k = int(gg.yes_won.sum())
        wr = k / n if n else 0.0
        price = float(gg.vwap.mean())
        phat, lo, hi = wilson_ci(k, n)
        rows.append(dict(
            bucket=str(b), n_markets=n, price=round(price, 4),
            actual_winrate=round(wr, 4), mispricing=round(wr - price, 4),
            ci_lo=round(lo, 4), ci_hi=round(hi, 4),
            ci_lo_mispr=round(lo - price, 4), ci_hi_mispr=round(hi - price, 4),
        ))
    out = pd.DataFrame(rows)
    print(f"\n===== MARKET-LEVEL CALIBRATION [{label}] window sec_to_close [{sec_lo},{sec_hi}) "
          f"(n_markets={len(g):,}) =====")
    print(out.to_string(index=False))
    return out


def directional_skew(df: pd.DataFrame):
    print("\n===== DIRECTIONAL / SIDE SKEW =====")
    m = df.drop_duplicates("ticker")
    print(f"markets: {len(m):,}  yes_won rate: {m.yes_won.mean():.4f}  "
          f"(n_yes={int(m.yes_won.sum())}, n_no={int((1-m.yes_won).sum())})")
    # binomial test vs 0.5
    n = len(m); k = int(m.yes_won.sum())
    phat, lo, hi = wilson_ci(k, n)
    print(f"  win-rate 95% CI: [{lo:.4f}, {hi:.4f}]  (0.5 {'INSIDE' if lo<=0.5<=hi else 'OUTSIDE'})")
    # taker side: who is the aggressor and does buying YES (taker yes) win?
    print("\n  taker_side aggressor outcomes (did the taker's side win?):")
    df2 = df.copy()
    df2["taker_won"] = np.where(df2.taker_side == "yes", df2.yes_won, 1 - df2.yes_won)
    for s, g in df2.groupby("taker_side"):
        print(f"    taker={s}: n={len(g):,} taker_won_rate={g.taker_won.mean():.4f} "
              f"avg_entry_yes_price={g.yes_price.mean():.4f}")


def edge_after_fee(out: pd.DataFrame, side_label: str):
    """For each bucket, compute best directional bet (buy YES vs buy NO) and net
    after round-trip... actually holds-to-settlement => only ENTRY taker fee + the
    payoff. No exit fee if held to settlement (winner redeems at $1, no fee)."""
    print(f"\n===== AFTER-FEE EDGE [{side_label}] (hold to settlement, taker entry) =====")
    rows = []
    for _, r in out.iterrows():
        p = r.price
        wr = r.actual_winrate
        nkey = "n" if "n" in r else "n_markets"
        n = r[nkey]
        # BUY YES at p: pay p + fee, win $1 w.p. wr  => EV = wr*1 - p - fee
        f_yes = taker_fee_ct(p)
        ev_yes = wr - p - f_yes
        # BUY NO at (1-p): pay (1-p)+fee, win $1 w.p. (1-wr)
        f_no = taker_fee_ct(1 - p)
        ev_no = (1 - wr) - (1 - p) - f_no
        best = "YES" if ev_yes >= ev_no else "NO"
        ev = max(ev_yes, ev_no)
        # CI-aware: use the pessimistic winrate bound for the chosen side
        if best == "YES":
            ev_lo = r.ci_lo - p - f_yes
        else:
            ev_lo = (1 - r.ci_hi) - (1 - p) - f_no
        rows.append(dict(bucket=r.bucket, n=int(n), price=round(p, 4),
                         actual_wr=round(wr, 4), best_side=best,
                         ev_per_ct=round(ev, 4), ev_ci_pessimistic=round(ev_lo, 4),
                         fee=round(f_yes if best == "YES" else f_no, 4)))
    e = pd.DataFrame(rows)
    print(e.to_string(index=False))
    return e


def per_day_economics(df: pd.DataFrame, edge_tbl: pd.DataFrame, label: str):
    """For buckets where the PESSIMISTIC (CI-bounded) after-fee EV/ct > 0, estimate
    trades/day and $/day on $50 bankroll."""
    print(f"\n===== PER-DAY ECONOMICS [{label}] =====")
    ct = pd.to_datetime(df.created_time, format="ISO8601")
    n_days = (ct.max() - ct.min()).days
    n_days = max(n_days, 1)
    print(f"data span: {n_days} days")
    exploit = edge_tbl[edge_tbl.ev_ci_pessimistic > 0]
    if exploit.empty:
        print("NO bucket has positive after-fee EV at the pessimistic CI bound. No edge.")
        return
    # map buckets back to trade volume
    df2 = df.copy()
    df2["bucket"] = pd.cut(df2.yes_price, PRICE_EDGES, include_lowest=True).astype(str)
    for _, r in exploit.iterrows():
        g = df2[df2.bucket == r.bucket]
        trades_per_day = len(g) / n_days
        size_per_day = g["size"].sum() / n_days
        # $50 bankroll, 1 contract ~ $p. point-estimate $/day if we took ALL such trades
        # but capacity-limited: realistically we put $50 to work. Use point EV.
        ev_pt = edge_tbl[edge_tbl.bucket == r.bucket].ev_per_ct.iloc[0]
        print(f"  bucket {r.bucket}: trades/day={trades_per_day:.0f} size/day={size_per_day:,.0f} ct "
              f"ev/ct(pt)={ev_pt:+.4f} ev/ct(pess)={r.ev_ci_pessimistic:+.4f}")
        # $/day if $50 deployed once per opportunity at avg price r.price, point EV
        cts_per_50 = 50.0 / max(r.price, 1e-3)
        print(f"      $50 buys ~{cts_per_50:.0f} ct at price {r.price:.3f} -> "
              f"per-trade $ = {cts_per_50*ev_pt:+.2f} (point) / {cts_per_50*r.ev_ci_pessimistic:+.2f} (pess)")


def early_window_persistence(path: Path):
    """The decisive test: on a time-diverse REST sample of FULL-window trades
    (>180s before close, the part trades.parquet omits), is the per-bucket
    mispricing STABLE across months? If it flips sign month-to-month it is
    directional regime noise, not a calibration edge."""
    if not path.exists():
        print(f"\n[early_window_persistence] {path.name} missing — run --fetch-strat first")
        return
    et = pd.read_parquet(path)
    m = pd.read_parquet(DATA / "markets.parquet")[["ticker", "result", "close_time"]]
    m["yes_won"] = (m.result == "yes").astype(int)
    et = et.merge(m, on="ticker", how="inner")
    et["yes_price"] = et.yes_price_dollars.astype(float)
    et["ct"] = pd.to_datetime(et.created_time, format="ISO8601")
    et["close"] = pd.to_datetime(et.close_time, format="ISO8601")
    et["stc"] = (et["close"] - et["ct"]).dt.total_seconds()
    et["mon"] = et.ct.dt.month
    w = et[et.stc > 180].copy()
    w["b"] = pd.cut(w.yes_price, PRICE_EDGES, include_lowest=True)
    print(f"\n===== EARLY-WINDOW (>180s) CALIBRATION PERSISTENCE "
          f"(n={len(w):,} trades, {w.ticker.nunique()} time-diverse markets) =====")
    months = sorted(w.mon.unique())
    rows = []
    for b in w.b.cat.categories:
        g = w[w.b == b]
        if len(g) < 100:
            continue
        rec = {"bucket": str(b), "price": round(g.yes_price.mean(), 3)}
        for mo in months:
            gm = g[g.mon == mo]
            rec[f"mis_{mo}"] = round((gm.yes_won.mean() - gm.yes_price.mean()) * 100, 1) if len(gm) else None
        rec["mis_ALL"] = round((g.yes_won.mean() - g.yes_price.mean()) * 100, 1)
        vals = [rec[f"mis_{mo}"] for mo in months if rec[f"mis_{mo}"] is not None]
        rec["all_same_sign"] = "YES" if len(set(np.sign(vals))) == 1 else "no"
        rows.append(rec)
    out = pd.DataFrame(rows)
    print(out.to_string(index=False))
    print("\nINTERPRETATION: a real calibration edge => same sign every month AND "
          "magnitude > round-trip fee (~1-2c). Sign-flipping => directional regime noise.")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", type=int, default=0,
                    help="REST-fetch full-window trades for N recent markets")
    ap.add_argument("--fetch-strat", type=int, default=0,
                    help="REST-fetch full-window trades for N time-diverse (stratified) markets")
    args = ap.parse_args()

    if args.fetch_strat:
        fetch_strat_trades(args.fetch_strat)
    # decisive early-window persistence test (uses pre-fetched stratified sample)
    early_window_persistence(DATA / "trades_strat.parquet")

    df = load()
    print(f"loaded {len(df):,} trades over {df.ticker.nunique():,} markets")

    directional_skew(df)

    # 1) Naive trade-weighted calibration (each trade = 1 obs) — biased by clustering
    full = calibration_table(df, "ALL trades (180s window, trade-weighted)")
    edge_after_fee(full, "ALL trades trade-weighted")

    # 2) Slice by time-to-close to expose the late-window survivorship
    for lo, hi in [(0, 15), (15, 60), (60, 120), (120, 181)]:
        sub = df[(df.sec_to_close >= lo) & (df.sec_to_close < hi)]
        calibration_table(sub, f"sec_to_close [{lo},{hi})")

    # 3) Market-level (one obs per market) in the EARLIEST window we have (~120-180s)
    #    -> cleanest binomial, least resolved
    mlc = market_level_calibration(df, "earliest-available", 120, 181)
    if mlc is not None:
        et = edge_after_fee(mlc, "market-level 120-180s")
        per_day_economics(df[(df.sec_to_close >= 120) & (df.sec_to_close < 181)], et,
                          "market-level 120-180s")

    # also do trade-weighted per-day on the full table for the capacity figure
    et_full = edge_after_fee(full, "full-for-economics")
    per_day_economics(df, et_full, "all-trades")

    if args.fetch:
        fetch_early_trades(args.fetch)


def fetch_early_trades(n_markets: int):
    """Pull FULL-window trade history (incl. early-window) for recent resolved
    markets via REST, to test calibration without the 180s survivorship slice."""
    sys.path.insert(0, str(ROOT))
    from kalshi_client import Kalshi  # noqa
    import time
    k = Kalshi()
    m = pd.read_parquet(DATA / "markets.parquet").sort_values("close_dt")
    tickers = m.ticker.tolist()[-n_markets:]
    recs = []
    for i, tk in enumerate(tickers):
        cursor = None
        while True:
            params = {"ticker": tk, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            try:
                resp = k.get("/markets/trades", params=params)
            except Exception as e:
                print("err", tk, e); break
            for tr in resp.get("trades", []):
                recs.append(tr)
            cursor = resp.get("cursor")
            if not cursor:
                break
        if i % 25 == 0:
            print(f"  fetched {i}/{len(tickers)} markets, {len(recs)} trades")
        time.sleep(0.05)
    et = pd.DataFrame(recs)
    out = DATA / "trades_fullwindow.parquet"
    et.to_parquet(out)
    print(f"saved {len(et):,} full-window trades -> {out}")
    print(et.columns.tolist())
    if not et.empty:
        print(et.head().to_string())


def fetch_strat_trades(n_markets: int):
    """Pull FULL-window trade history for a TIME-DIVERSE (stratified-by-date)
    set of markets so the early-window calibration test isn't all one regime."""
    sys.path.insert(0, str(ROOT))
    from kalshi_client import Kalshi  # noqa
    import time
    k = Kalshi()
    m = pd.read_parquet(DATA / "markets.parquet").sort_values("close_dt").reset_index(drop=True)
    step = max(1, len(m) // n_markets)
    tickers = m.ticker.iloc[list(range(0, len(m), step))].tolist()
    print(f"fetching {len(tickers)} markets stratified across {m.close_dt.min()}..{m.close_dt.max()}")
    recs = []
    for i, tk in enumerate(tickers):
        cursor = None
        while True:
            params = {"ticker": tk, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            try:
                resp = k.get("/markets/trades", params=params)
            except Exception as e:
                print("err", tk, e); break
            recs += resp.get("trades", [])
            cursor = resp.get("cursor")
            if not cursor:
                break
        if i % 50 == 0:
            print(f"  {i}/{len(tickers)} -> {len(recs)} trades")
        time.sleep(0.02)
    et = pd.DataFrame(recs)
    et.to_parquet(DATA / "trades_strat.parquet")
    print(f"saved {len(et):,} trades over {et.ticker.nunique()} markets -> trades_strat.parquet")


if __name__ == "__main__":
    main()
