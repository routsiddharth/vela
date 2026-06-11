"""
explore_orderflow.py — Backtest directional signals from Kalshi order flow & book
imbalance for KXBTC15M / KXETH15M.

THE TEST (for every signal): conditional on the signal at time T, does the OUTCOME
beat the contract PRICE at T by more than fees? A signal that just tracks the price
(price already moved with it) is LAGGING => no edge. We need a LEADING signal:
the price at T is still "wrong" relative to what the flow knows.

Signals tested:
  (a) AGGRESSOR FLOW  : net taker flow (yes-taker size - no-taker size) over rolling window
  (b) BOOK IMBALANCE  : depth_yes vs depth_no / best-bid size asymmetry  (live DB only)
  (c) LARGE PRINTS    : big taker trades (top size decile) — follow them?
  (d) PRICE MOMENTUM  : Kalshi mid-price short-term drift -> continuation?

Primary dataset: backtest/data/trades.parquet (2497 KXBTC15M windows, final 180s,
trade-level: ts, sec_to_close, yes_price, no_price, size, taker_side) joined to
markets.parquet (result). Book signal (b) uses livepaper/data/paper.db (33 windows).

Fees (Kalshi): TAKER/ct = ceil_cent(0.07*p*(1-p)) min $0.01.
"""
import sqlite3
import math
import numpy as np
import pandas as pd

PARQUET_T = "backtest/data/trades.parquet"
PARQUET_M = "backtest/data/markets.parquet"
LIVE_DB = "file:livepaper/data/paper.db?mode=ro"


def taker_fee(p):
    """Per-contract taker fee in dollars. ceil to cent, min 1c."""
    if not np.isfinite(p):
        return 0.01
    raw = 0.07 * p * (1.0 - p)
    return max(math.ceil(raw * 100) / 100.0, 0.01)


def load_parquet():
    t = pd.read_parquet(PARQUET_T)
    m = pd.read_parquet(PARQUET_M)[["ticker", "result"]]
    t = t.merge(m, on="ticker", how="inner")
    t = t[t.yes_price.notna() & (t.yes_price > 0) & (t.yes_price < 1)].copy()
    t["y"] = (t.result == "yes").astype(int)
    # sort within window by sec_to_close DESCENDING (early -> late)
    t = t.sort_values(["ticker", "sec_to_close"], ascending=[True, False]).reset_index(drop=True)
    return t


# ---------------------------------------------------------------------------
# Helper: build per-window snapshots at a set of "decision times" (sec_to_close).
# At each decision time T we compute the signal from trades in [T+window, T],
# the prevailing yes_price (last trade price at/just before T), and outcome y.
# ---------------------------------------------------------------------------
def window_snapshots(t, decision_secs, flow_window):
    """For each window and each decision sec-to-close T, compute:
        yes_px_T   : last trade yes_price at sec_to_close>=T (the price you'd pay-ish)
        net_flow   : sum(size signed +yes/-no) over trades with sec_to_close in [T, T+flow_window]
        gross_flow : total taker size in that window
        big_flow   : signed flow from top-decile-size trades in that window
        mom        : yes_px_T - yes_px at (T+flow_window)  (recent drift)
        y          : outcome
    """
    rows = []
    # precompute global big-trade threshold (top decile of size)
    big_thr = t["size"].quantile(0.90)
    for tk, g in t.groupby("ticker", sort=False):
        y = g["y"].iloc[0]
        s2c = g["sec_to_close"].values
        yp = g["yes_price"].values
        sz = g["size"].values
        side = g["taker_side"].values
        signed = np.where(side == "yes", sz, -sz)
        for T in decision_secs:
            # price at T: last trade with sec_to_close >= T (most recent before/at decision)
            pre = s2c >= T
            if not pre.any():
                continue
            idx_T = np.argmax(pre[::-1])  # not robust; do explicit
            # most recent trade at or before decision time T (largest s2c that is >= T)
            cand = np.where(pre)[0]
            i_now = cand[np.argmin(s2c[cand])]  # smallest s2c among those >= T = closest to T
            px_T = yp[i_now]
            # flow window: trades with sec_to_close in [T, T+flow_window]
            win = (s2c >= T) & (s2c < T + flow_window)
            if not win.any():
                net = 0.0
                gross = 0.0
                big = 0.0
            else:
                net = signed[win].sum()
                gross = sz[win].sum()
                bigmask = win & (sz >= big_thr)
                big = signed[bigmask].sum() if bigmask.any() else 0.0
            # momentum: price now minus price flow_window-ago
            older = s2c >= T + flow_window
            if older.any():
                co = np.where(older)[0]
                i_old = co[np.argmin(s2c[co])]
                px_old = yp[i_old]
            else:
                px_old = px_T
            mom = px_T - px_old
            rows.append((tk, T, px_T, net, gross, big, mom, y))
    return pd.DataFrame(rows, columns=["ticker", "dsec", "px_T", "net", "gross", "big", "mom", "y"])


def edge_report(df, signal_col, label, side_from_sign=True):
    """For a signal, bucket by sign/strength and report:
       - n
       - realized yes-rate
       - avg price (prob implied)
       - signal HIT vs OUTCOME (does signal sign match outcome)
       - the REAL test: avg after-fee $/contract if you TAKE the signal's side at px_T
    We trade the side the signal points to (yes if signal>0, no if signal<0).
    Buying YES at px pays (1-px) if win else -px ; minus taker fee on px.
    Buying NO  at (1-px) pays px if win(no) else -(1-px) ; fee on (1-px).
    """
    out = []
    s = df[signal_col]
    # strength buckets by absolute value quantiles among nonzero
    nz = df[s != 0]
    if len(nz) == 0:
        return pd.DataFrame()
    thr = nz[signal_col].abs().quantile([0.5, 0.8, 0.95]).values
    buckets = {
        "all_nonzero": nz,
        "|sig|>p50": nz[nz[signal_col].abs() >= thr[0]],
        "|sig|>p80": nz[nz[signal_col].abs() >= thr[1]],
        "|sig|>p95": nz[nz[signal_col].abs() >= thr[2]],
    }
    for bname, b in buckets.items():
        if len(b) == 0:
            continue
        sign = np.sign(b[signal_col].values)
        bet_yes = sign > 0
        px = b["px_T"].values
        y = b["y"].values
        # entry price of the side we bet
        entry = np.where(bet_yes, px, 1 - px)
        won = np.where(bet_yes, y == 1, y == 0)
        payoff = np.where(won, 1 - entry, -entry)
        fee = np.array([taker_fee(p) for p in px])  # fee depends on p(1-p), symmetric
        net = payoff - fee
        # signal hit vs OUTCOME
        hit_outcome = won.mean()
        # signal vs PRICE: is entry price < 0.5 yet we win more than entry implies?
        # the honest metric is just net $/contract (already vs price+fees)
        out.append({
            "signal": label,
            "bucket": bname,
            "n": len(b),
            "hit_vs_outcome": round(hit_outcome, 3),
            "avg_entry_px": round(entry.mean(), 3),
            "implied_winrate": round(entry.mean(), 3),
            "avg_gross_$/ct": round(payoff.mean(), 4),
            "avg_fee_$/ct": round(fee.mean(), 4),
            "avg_net_$/ct": round(net.mean(), 4),
            "net_$/ct_se": round(net.std() / math.sqrt(len(b)), 4),
        })
    return pd.DataFrame(out)


def main():
    print("Loading parquet (2.4M trades)...")
    t = load_parquet()
    print(f"  windows={t.ticker.nunique()} trades={len(t)}")

    # Decision times: test entering EARLY (price still has value) through LATE.
    decision_secs = [150, 120, 90, 60, 45, 30, 20, 10, 5]
    flow_window = 30  # seconds of flow accumulated before decision

    print(f"\nBuilding snapshots at decision secs={decision_secs}, flow_window={flow_window}s ...")
    snaps = window_snapshots(t, decision_secs, flow_window)
    print(f"  snapshots={len(snaps)}")

    # ---- baseline: how predictive is PRICE alone (efficient market check) ----
    print("\n=== PRICE CALIBRATION (baseline; signal must beat THIS) ===")
    cal = snaps.groupby(pd.cut(snaps.px_T, np.linspace(0, 1, 11), include_lowest=True), observed=True).agg(
        n=("y", "size"), avg_px=("px_T", "mean"), realized_yes=("y", "mean")
    )
    print(cal.to_string())

    # ===================================================================
    # Run the edge report at each decision time for each signal
    # ===================================================================
    all_reports = []
    for T in decision_secs:
        sub = snaps[snaps.dsec == T]
        if len(sub) < 50:
            continue
        for col, lab in [("net", "a_aggressor_flow"), ("big", "c_large_prints"), ("mom", "d_price_momentum")]:
            r = edge_report(sub, col, f"{lab}@T={T}")
            if not r.empty:
                r["decision_sec"] = T
                all_reports.append(r)
    rep = pd.concat(all_reports, ignore_index=True)
    pd.set_option("display.width", 200, "display.max_columns", 30, "display.max_rows", 400)

    print("\n=== SIGNAL EDGE REPORT (after-fee $/contract; >0 = profitable) ===")
    # focus on strongest buckets
    show = rep[rep.bucket.isin(["|sig|>p80", "|sig|>p95"])].copy()
    show = show[["signal", "decision_sec", "bucket", "n", "hit_vs_outcome",
                 "avg_entry_px", "avg_net_$/ct", "net_$/ct_se"]]
    print(show.to_string(index=False))

    # ---- momentum-FADE variant (mean reversion) ----
    print("\n=== MOMENTUM FADE (bet AGAINST recent drift) ===")
    fade_reports = []
    for T in decision_secs:
        sub = snaps[snaps.dsec == T].copy()
        if len(sub) < 50:
            continue
        sub["mom_fade"] = -sub["mom"]
        r = edge_report(sub, "mom_fade", f"d_momentum_FADE@T={T}")
        if not r.empty:
            r["decision_sec"] = T
            fade_reports.append(r)
    if fade_reports:
        fr = pd.concat(fade_reports, ignore_index=True)
        fr = fr[fr.bucket.isin(["|sig|>p80", "|sig|>p95"])]
        print(fr[["signal", "decision_sec", "bucket", "n", "hit_vs_outcome",
                  "avg_entry_px", "avg_net_$/ct", "net_$/ct_se"]].to_string(index=False))

    # ===================================================================
    # LEADING vs LAGGING diagnostic: does the signal at T predict the
    # FUTURE price move (T -> T-15), beyond predicting the outcome?
    # If flow predicts outcome ONLY as much as price already does => lagging.
    # ===================================================================
    print("\n=== LEADING TEST: does flow at T predict residual (outcome - price)? ===")
    for T in [120, 90, 60, 45, 30]:
        sub = snaps[snaps.dsec == T]
        if len(sub) < 50:
            continue
        resid = sub["y"] - sub["px_T"]  # how much outcome beats price
        for col in ["net", "big", "mom"]:
            sig = sub[col]
            if sig.std() == 0:
                continue
            corr = np.corrcoef(sig, resid)[0, 1]
            print(f"  T={T:>3} corr({col}, outcome-price) = {corr:+.4f}  (n={len(sub)})")

    # save snapshots for the book analysis / reuse
    snaps.to_parquet("backtest/analysis/orderflow_snaps.parquet")

    # ===================================================================
    # (b) BOOK IMBALANCE — live DB only (33 windows). Small n, directional check.
    # ===================================================================
    print("\n=== (b) BOOK IMBALANCE (live DB, small sample) ===")
    book_imbalance_test()


def book_imbalance_test():
    con = sqlite3.connect(LIVE_DB, uri=True)
    bs = pd.read_sql_query(
        "SELECT ts_ms,ticker,sec_to_close,best_yes_bid,yes_bid_sz,best_no_bid,no_bid_sz,"
        "depth_yes,depth_no,yes_ask FROM book_snaps", con)
    win = pd.read_sql_query("SELECT ticker,result FROM windows", con)
    con.close()
    bs = bs.merge(win, on="ticker", how="inner")
    bs["y"] = (bs.result == "yes").astype(int)
    # yes_ask = 1 - best_no_bid (bids-only book). mid of the *yes* market.
    bs["yes_ask"] = bs["yes_ask"].where(bs.yes_ask.notna(), 1 - bs.best_no_bid)
    bs["mid"] = (bs.best_yes_bid + bs.yes_ask) / 2.0
    # depth imbalance
    bs["depth_imb"] = (bs.depth_yes - bs.depth_no) / (bs.depth_yes + bs.depth_no + 1e-9)
    bs["bid_imb"] = (bs.yes_bid_sz - bs.no_bid_sz) / (bs.yes_bid_sz + bs.no_bid_sz + 1e-9)
    print(f"  book snaps={len(bs)} over {bs.ticker.nunique()} windows")
    print("  KEY: 'imb hit' vs 'PRICE hit' — if imbalance is no better than the price's")
    print("       own sign, it's LAGGING. corr is imbalance vs (outcome - mid): leading test.")
    for T in [120, 90, 60, 45, 30, 15]:
        sub = bs[(bs.sec_to_close >= T) & (bs.sec_to_close < T + 15)].dropna(subset=["mid"])
        if len(sub) < 20:
            continue
        g = sub.sort_values("sec_to_close").groupby("ticker").first().reset_index()
        g = g.dropna(subset=["mid"])
        if len(g) < 10:
            continue
        # PRICE's own directional hit (mid>0.5 => predict yes)
        price_hit = ((g["mid"] > 0.5) == (g["y"] == 1)).mean()
        resid = g["y"] - g["mid"]
        for col in ["depth_imb", "bid_imb"]:
            if g[col].std() == 0 or resid.std() == 0:
                print(f"  T={T:>3} {col:>10}: n={len(g)} (degenerate)")
                continue
            corr = np.corrcoef(g[col].values, resid.values)[0, 1]
            sign = np.sign(g[col])
            imb_hit = ((sign > 0) == (g["y"] == 1)).mean()
            # cases where imbalance DISAGREES with the price sign — the only place it can add edge
            disagree = g[(g[col] > 0) != (g["mid"] > 0.5)]
            dis_imb_hit = ((np.sign(disagree[col]) > 0) == (disagree["y"] == 1)).mean() if len(disagree) else float("nan")
            print(f"  T={T:>3} {col:>10}: n={len(g)} imb_hit={imb_hit:.2f} PRICE_hit={price_hit:.2f} "
                  f"corr(imb,outcome-mid)={corr:+.3f} | when_imb!=price n={len(disagree)} imb_wins={dis_imb_hit:.2f}")


if __name__ == "__main__":
    main()
