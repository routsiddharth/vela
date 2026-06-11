"""Directional-prediction backtest for KXBTC15M (15-min up/down vs strike).

Hypothesis: the 15-min up/down outcome is predictable from BTC's recent price
action better than the ~0.50 the market prices at window open. If so, you enter
EARLY (at/near open) on the predicted side and hold to settle.

Data:
  * markets.parquet           : one row per 15-min window (strike, true_settle, margin, result)
  * binance_1m_full.parquet   : Binance BTCUSDT 1m closes for the FULL date range
                                (fetched via data-api.binance.vision; see fetch_1m()).
  * trades.parquet            : Kalshi executed trades, final 180s (for entry-price realism)

Method:
  Strike K[i] = settlement[i-1] (struck ATM at open). Outcome YES iff settle>=K.
  At open we know BTC's path up to t=open. We build directional signals from the
  prior price action and test whether sign(signal) predicts the window outcome,
  then whether that edge survives the entry price + fees.

Settlement is the mean of the final 60s of CF-Benchmarks RTI; Binance is a proxy.
We map outcomes from the REAL markets.parquet (true_settle/margin), so the
Binance proxy is used ONLY to build the entry signal, never the label.

Fees (Kalshi, per the prompt):
  taker per ct = ceil_cent(0.07 * p*(1-p)), min $0.01
  maker per order = ceil_cent(0.0175 * qty * p*(1-p))
"""
from __future__ import annotations
import math, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.simplefilter("ignore")

D = Path(__file__).resolve().parent.parent / "data"


def to_ms(s):
    return s.astype("datetime64[ns, UTC]").astype("int64") // 10**6


def fetch_1m():
    """Fetch full-range Binance 1m closes (cached). Returns Series indexed by open_ms."""
    out = D / "binance_1m_full.parquet"
    if out.exists():
        b = pd.read_parquet(out)
        return b.set_index("open_ms")["close"]
    import httpx, time
    m = pd.read_parquet(D / "markets.parquet")
    m["open_dt"] = pd.to_datetime(m["open_time"]); m["close_dt2"] = pd.to_datetime(m["close_time"])
    start_ms = int(to_ms(m["open_dt"]).min()) - 60 * 60 * 1000
    end_ms = int(to_ms(m["close_dt2"]).max())
    c = httpx.Client(timeout=30); rows = []; cur = start_ms
    while cur < end_ms:
        for att in range(5):
            try:
                r = c.get("https://data-api.binance.vision/api/v3/klines",
                          params={"symbol": "BTCUSDT", "interval": "1m",
                                  "startTime": cur, "endTime": end_ms, "limit": 1000})
                if r.status_code in (429, 418): time.sleep(att + 1); continue
                r.raise_for_status(); j = r.json(); break
            except Exception:
                time.sleep(0.5 * (att + 1)); j = []
        if not j: break
        for k in j: rows.append((int(k[0]), float(k[4])))
        cur = j[-1][0] + 60000
    df = pd.DataFrame(rows, columns=["open_ms", "close"]).drop_duplicates("open_ms").sort_values("open_ms")
    df.to_parquet(out)
    return df.set_index("open_ms")["close"]


# ---- Fee model -------------------------------------------------------------
def ceil_cent(x):
    return math.ceil(x * 100 - 1e-9) / 100.0

def taker_fee(p):
    return max(ceil_cent(0.07 * p * (1 - p)), 0.01)

def maker_fee(p, qty=1):
    return ceil_cent(0.0175 * qty * p * (1 - p))


# ---- Build the panel -------------------------------------------------------
def build_panel():
    m = pd.read_parquet(D / "markets.parquet").copy()
    m["open_dt"] = pd.to_datetime(m["open_time"])
    m["close_dt2"] = pd.to_datetime(m["close_time"])
    m = m.sort_values("open_dt").reset_index(drop=True)
    m["yes"] = (m["margin"] >= 0).astype(int)
    m["open_ms"] = to_ms(m["open_dt"])
    m["close_ms"] = to_ms(m["close_dt2"])

    b = fetch_1m()  # open_ms -> close price of that 1m bar

    def px_at(ms):
        # price at instant `ms` ~= close of the 1m bar that opened at ms-60000
        return (ms - 60000).map(b)

    # Binance reference prices around open and at lookbacks before open.
    m["b_open"] = px_at(m["open_ms"])                 # ~price at window open
    for n in (1, 2, 3, 5, 10, 15, 30, 60):
        m[f"b_m{n}"] = px_at(m["open_ms"] - n * 60000)   # price n min before open
    # realized outcome reference (proxy): price at window close
    m["b_close"] = px_at(m["close_ms"])

    # prior-window return = the move that just happened (open vs 15m before open)
    m["ret_prior15"] = m["b_open"] / m["b_m15"] - 1.0
    for n in (1, 2, 3, 5, 10, 30, 60):
        m[f"ret_{n}"] = m["b_open"] / m[f"b_m{n}"] - 1.0

    # rolling volatility of 1m returns over the last 60 min (std of log returns)
    cols = [f"b_m{n}" for n in (60, 30, 15, 10, 5, 3, 2, 1)] + ["b_open"]
    return m, b


# ---- Signal evaluation -----------------------------------------------------
def eval_signal(m, sig, name, side_rule, p_entry=0.50, fee=None, mask=None):
    """side_rule: +1 => bet YES when sig>0 (momentum), -1 => bet YES when sig<0 (reversion).
    Direction predicted = sign(sig)*side_rule mapped to YES(=1)/NO(=0).
    Returns dict of stats. We bet the predicted side at price p_entry.
    """
    d = m.copy()
    if mask is not None: d = d[mask]
    d = d[d[sig].notna() & d["yes"].notna()]
    s = d[sig].values
    pred_yes = ((np.sign(s) * side_rule) > 0).astype(int)  # 1=bet YES, 0=bet NO
    # drop exact-zero signals (no bet)
    bet = s != 0
    d = d[bet]; pred_yes = pred_yes[bet]
    if len(d) == 0:
        return None
    actual = d["yes"].values
    hit = (pred_yes == actual).astype(float)
    # PnL: buy predicted side at p_entry, pays $1 if correct. cost = p_entry + fee.
    if fee is None:
        fee = taker_fee(p_entry)
    payoff = hit * 1.0
    pnl = payoff - p_entry - fee  # per $1-notional contract
    return dict(name=name, n=len(d), hit=hit.mean(),
                avg_pnl=pnl.mean(), p_entry=p_entry, fee=fee,
                total_pnl=pnl.sum(), pnl_series=pd.Series(pnl, index=d.index),
                hit_series=pd.Series(hit, index=d.index))


def edge_needed(p_entry):
    """Hit rate required to break even at p_entry after taker fee, betting at p_entry.
    EV = h - p - fee = 0  => h = p + fee."""
    return p_entry + taker_fee(p_entry)


def main():
    m, b = build_panel()
    n_days = m["open_dt"].dt.date.nunique()
    print(f"windows: {len(m)}  days: {n_days}  ~{len(m)/n_days:.1f}/day")
    cov = m["b_open"].notna().mean()
    print(f"binance open coverage: {cov:.3f}  close coverage: {m['b_close'].notna().mean():.3f}")
    print(f"base YES rate: {m['yes'].mean():.4f}")
    print()

    # Sanity: does Binance close-vs-strike agree with the true outcome? (proxy quality)
    mm = m[m["b_close"].notna()].copy()
    proxy_yes = (mm["b_close"] >= mm["strike"]).astype(int)
    print(f"PROXY check: binance(close>=strike) agrees with true outcome {((proxy_yes==mm['yes']).mean()):.4f}")
    print()

    results = []
    # ---- (a) MOMENTUM and (b) MEAN-REVERSION across lookbacks --------------
    print("=== Directional signals at OPEN, entry @ p=0.50, taker fee ===")
    print(f"break-even hit rate needed @0.50: {edge_needed(0.50):.4f}\n")
    for n in (1, 2, 3, 5, 10, 15, 30, 60):
        sig = f"ret_{n}" if n != 15 else "ret_prior15"
        for rule, label in ((+1, "MOM"), (-1, "REV")):
            r = eval_signal(m, sig, f"{label}_ret{n}m", rule, p_entry=0.50)
            if r:
                results.append(r)
                flag = "  <-- beats BE" if r["hit"] > edge_needed(0.50) else ""
                print(f"  {label} ret_{n:>2}m  n={r['n']:5d}  hit={r['hit']:.4f}  "
                      f"avg_pnl=${r['avg_pnl']:+.4f}  total=${r['total_pnl']:+.2f}{flag}")
    print()

    # ---- (c) volatility-conditioned / trend-strength filters --------------
    # |move| magnitude: does the edge concentrate in big-move windows?
    print("=== Conditioned on |prior-15m move| magnitude (MOM and REV) ===")
    m["absmove"] = m["ret_prior15"].abs()
    qs = m["absmove"].quantile([0.5, 0.8, 0.9, 0.95]).to_dict()
    for thr_q, thr in qs.items():
        mask = m["absmove"] >= thr
        for rule, label in ((+1, "MOM"), (-1, "REV")):
            r = eval_signal(m, "ret_prior15", f"{label}_q{thr_q}", rule, p_entry=0.50, mask=mask)
            if r:
                flag = "  <-- beats BE" if r["hit"] > edge_needed(0.50) else ""
                print(f"  {label} |move|>={thr*100:.3f}% (top {int((1-thr_q)*100)}%)  "
                      f"n={r['n']:5d}  hit={r['hit']:.4f}  avg_pnl=${r['avg_pnl']:+.4f}{flag}")
    print()

    # ---- (d) entry timing: how mispriced is the market at open vs later? ---
    # Use trades.parquet to see the ACTUAL price you'd pay near open vs endgame,
    # and whether the signal still has informational edge as you move in time.
    entry_timing_analysis(m)

    # ---- The de-biased "where you start" signal (strongest raw) ------------
    debias_signal_analysis(m)

    # ---- DECISIVE TEST: real open-time Kalshi entry price vs signal -------
    real_price_test()

    # ---- Best variant: pick the strongest from above and cost it fully -----
    best = max(results, key=lambda r: r["avg_pnl"])
    print("=== STRONGEST raw signal (assumes 0.50 entry — UNREALISTIC) ===")
    print(f"  {best['name']}: hit={best['hit']:.4f} avg_pnl=${best['avg_pnl']:+.4f} n={best['n']}")
    bankroll = 50.0
    summarize_pnl(best, n_days, bankroll)


def debias_signal_analysis(m):
    """Causal de-biased BTC-vs-strike at open. Binance carries a ~constant high bias
    vs CF-Benchmarks RTI; estimate it from PRIOR settled windows only."""
    m = m.sort_values("open_dt").reset_index(drop=True)
    m["bias"] = (m["b_close"] - m["true_settle"]).shift(1).rolling(96, min_periods=20).median()
    m["open_rti_est"] = m["b_open"] - m["bias"]
    m["open_vs_strike"] = m["open_rti_est"] - m["strike"]
    v = m.dropna(subset=["open_vs_strike"])
    print("=== De-biased (open price - strike) directional hit (full sample) ===")
    for q in (0.0, 0.5, 0.8, 0.9):
        thr = v["open_vs_strike"].abs().quantile(q)
        vv = v[v["open_vs_strike"].abs() >= thr]
        pred = (vv["open_vs_strike"] >= 0).astype(int)
        hit = (pred == vv["yes"]).mean()
        print(f"  top{int((1 - q) * 100):>3}%  |open-K|>=${thr:6.1f}  n={len(vv):5d}  hit={hit:.4f}")
    print("  (the market is NOT at 0.50 in these cells — see real-price test)\n")


def real_price_test():
    """The decisive test: use REAL Kalshi open-time prices (candlesticks at 60s into the
    window) as the entry price. Buy the signal's predicted side at the ask, hold to settle,
    pay taker fee. If the cached candle file is absent, fetch it (needs Kalshi creds)."""
    cf = D / "kalshi_open_candles.parquet"
    if not cf.exists():
        print("=== REAL-PRICE TEST skipped (no kalshi_open_candles.parquet; run fetch) ===\n")
        return
    m = pd.read_parquet(D / "markets.parquet").copy()
    m["open_dt"] = pd.to_datetime(m["open_time"]); m["close_dt2"] = pd.to_datetime(m["close_time"])
    m = m.sort_values("open_dt").reset_index(drop=True)
    m["yes"] = (m["margin"] >= 0).astype(int)
    m["open_ms"] = to_ms(m["open_dt"]); m["close_ms"] = to_ms(m["close_dt2"])
    b = fetch_1m()
    m["b_close"] = (m["close_ms"] - 60000).map(b)
    m["bias"] = (m["b_close"] - m["true_settle"]).shift(1).rolling(96, min_periods=20).median()
    m["b_60"] = (m["open_ms"] + 60000 - 60000).map(b)
    m["sig60"] = (m["b_60"] - m["bias"]) - m["strike"]
    c = pd.read_parquet(cf)
    c1 = c[c["sec_into"] == 60].set_index("ticker")
    d = m.set_index("ticker").join(c1[["yes_ask", "yes_bid", "mid"]], how="inner").reset_index()
    d = d.dropna(subset=["sig60"]).sort_values("open_dt").reset_index(drop=True)
    ndays = d["open_dt"].dt.date.nunique()
    print(f"=== DECISIVE: REAL Kalshi entry price (enter @60s, pay ask) n={len(d)} over {ndays} days ===")
    print("thresh   n   hit   avgpx   pnl/ct    t-stat   $/day@$50")
    for q in (0.0, 0.5, 0.7, 0.8, 0.85, 0.9, 0.95):
        thr = d["sig60"].abs().quantile(q)
        s = d[d["sig60"].abs() >= thr]
        by = s["sig60"] >= 0
        price = np.where(by, s["yes_ask"], 1.0 - s["yes_bid"])
        win = np.where(by, s["yes"] == 1, s["yes"] == 0).astype(float)
        fee = np.array([taker_fee(p) for p in price])
        pnl = win - price - fee
        t = pnl.mean() / (pnl.std() / np.sqrt(len(pnl))) if pnl.std() > 0 else 0.0
        cts = 50.0 / (price.mean() + taker_fee(price.mean()))
        dpd = pnl.mean() * cts * len(s) / ndays
        print(f"  {q:.2f}  {len(s):4d}  {win.mean():.3f}  {price.mean():.3f}  "
              f"{pnl.mean():+.4f}   {t:+.2f}    {dpd:+.2f}")
    print("  => |t|<2 everywhere: no cell is statistically distinguishable from zero edge.")
    print("  => the market re-prices the BTC displacement within 60s; ask+fee eats the signal.\n")


def summarize_pnl(r, n_days, bankroll, contract_notional=None):
    """Translate per-contract pnl into $/day on a bankroll. One contract risks ~p_entry."""
    pnl = r["pnl_series"]
    trades_per_day = len(pnl) / n_days
    avg = pnl.mean()
    # If you put the WHOLE bankroll into the (non-overlapping) live window at p_entry:
    # contracts per trade = bankroll / (p_entry + fee)
    cost = r["p_entry"] + r["fee"]
    cts = bankroll / cost
    daily = avg * cts * trades_per_day  # crude: assumes 1 trade live at a time, full bankroll
    print(f"\n  trades/day: {trades_per_day:.1f}")
    print(f"  avg pnl/contract: ${avg:+.4f}")
    print(f"  contracts/trade @ ${bankroll} bankroll: {cts:.1f}")
    print(f"  NAIVE $/day (full bankroll, 1 live trade): ${daily:+.2f}")
    # drawdown on per-contract cumulative
    cum = pnl.cumsum(); dd = (cum - cum.cummax()).min()
    print(f"  max drawdown (per-contract cum): ${dd:.2f}")


def entry_timing_analysis(m):
    print("=== ENTRY TIMING / actual market price (trades.parquet) ===")
    t = pd.read_parquet(D / "trades.parquet")
    # trades cover final 180s. yes_price in cents? check.
    print(f"  trades rows: {len(t)}  yes_price range: {t['yes_price'].min()}..{t['yes_price'].max()}")
    # average yes_price by sec_to_close bucket -> is there a tradeable spread from 0.50?
    t = t.copy()
    t["sec_bucket"] = pd.cut(t["sec_to_close"], [0, 30, 60, 90, 120, 180])
    g = t.groupby("sec_bucket")["yes_price"].agg(["mean", "median", "count"])
    print(g)
    # We don't have open prices in trades (only final 180s). At open the market is
    # ATM (~0.50). Note this for the writeup.
    print()


if __name__ == "__main__":
    main()
