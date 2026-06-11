"""BTC -> ETH 15M lead-lag (idea c).

Hypothesis: within a 15M window, BTC's move-so-far predicts ETH's SAME-window
up/down outcome before the ETH market reprices. If BTC and ETH are tightly
correlated but ETH's order flow lags BTC's, then at time T (e.g. 60-120s before
close) BTC's residual move (beyond ETH's own move so far) forecasts ETH's final
direction -> tradeable in KXETH15M.

We reconstruct windows on a 15M grid from 1m closes (BTC: binance_1m_full,
ETH: eth_1m). For each window:
  strike_eth = ETH price at window OPEN (proxy: close of the minute before open;
               real Kalshi strike = prior-window settlement, ~ open price)
  outcome_eth = sign(ETH_close_at_window_end - strike_eth)
  We mirror Kalshi: settle ~ avg final 60s; with 1m data the last-minute close is
  our best proxy for the settle.

Signal at decision time tau (seconds before close): BTC's % move from window open
to (close-tau) MINUS ETH's % move from window open to (close-tau). If BTC has
moved more up than ETH so far, bet ETH UP (it'll catch up).

Brutally honest: 1m granularity is coarse vs the 60s TWAP settle, so this is a
FEASIBILITY screen, not a fill-accurate backtest. If the edge is large and OOS-
stable it's worth a 1s follow-up; if it's marginal at 1m it's dead.
"""
from __future__ import annotations
import math
from pathlib import Path
import numpy as np, pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (p, (c - h) / d, (c + h) / d)


def taker_fee(p):
    return max(math.ceil(0.07 * p * (1 - p) * 100 - 1e-9), 1) / 100.0


def build_grid():
    btc = pd.read_parquet(DATA / "binance_1m_full.parquet").rename(columns={"close": "btc"})
    eth = pd.read_parquet(DATA / "eth_1m.parquet").rename(columns={"close": "eth"})
    df = btc.merge(eth, on="open_ms", how="inner").sort_values("open_ms").reset_index(drop=True)
    df["dt"] = pd.to_datetime(df.open_ms, unit="ms", utc=True)
    df["minute_of_window"] = (df.dt.dt.minute % 15)  # 0..14
    return df


def windows_with_signal(df, tau_min):
    """Return per-window: open prices, settle proxy, signal at (15-tau_min) minutes in.
    tau_min = minutes before close to decide (e.g. 2 => decide at minute 13)."""
    rows = []
    # group into 15M windows by floor
    df = df.copy()
    df["wid"] = (df.open_ms // (15 * 60_000))
    decide_min = 15 - tau_min  # minutes after open at decision (e.g. 13)
    for wid, g in df.groupby("wid"):
        g = g.sort_values("minute_of_window")
        if len(g) < 15:
            continue
        mw = g.set_index("minute_of_window")
        if 0 not in mw.index or 14 not in mw.index or (decide_min - 1) not in mw.index:
            continue
        btc_open = mw.loc[0, "btc"]; eth_open = mw.loc[0, "eth"]
        # settle proxy = last minute close (minute 14)
        btc_set = mw.loc[14, "btc"]; eth_set = mw.loc[14, "eth"]
        # decision price = close of minute (decide_min-1) (i.e. info available at decide)
        btc_dec = mw.loc[decide_min - 1, "btc"]; eth_dec = mw.loc[decide_min - 1, "eth"]
        rows.append((wid, btc_open, eth_open, btc_dec, eth_dec, btc_set, eth_set))
    w = pd.DataFrame(rows, columns=["wid", "btc_open", "eth_open", "btc_dec",
                                    "eth_dec", "btc_set", "eth_set"])
    # ETH outcome: settle vs open (strike ~ open price)
    w["eth_up"] = (w.eth_set > w.eth_open).astype(int)
    # ETH outcome vs DECISION price (strike ~ open, but residual move from dec->set)
    # BTC move-so-far and ETH move-so-far (% from open to decision)
    w["btc_move"] = w.btc_dec / w.btc_open - 1
    w["eth_move"] = w.eth_dec / w.eth_open - 1
    # lead-lag signal: BTC moved more than ETH so far -> ETH catches up
    w["resid"] = w.btc_move - w.eth_move
    # also: does eth's OWN move-so-far already predict (baseline momentum)
    return w


def main():
    df = build_grid()
    print("merged 1m bars:", len(df), df.dt.min(), "->", df.dt.max())
    print("BTC/ETH 1m return corr:",
          np.corrcoef(df.btc.pct_change().dropna(), df.eth.pct_change().dropna())[0, 1]
          if False else
          np.corrcoef(df.btc.pct_change().fillna(0)[1:], df.eth.pct_change().fillna(0)[1:])[0, 1])

    for tau in (1, 2, 3, 5):
        w = windows_with_signal(df, tau)
        print(f"\n===== decision at {15-tau} min in (tau={tau}min before close), n={len(w)} =====")
        print(f"  ETH base up-rate: {w.eth_up.mean():.4f}")
        # baseline: ETH's own move-so-far predicts ETH outcome (this is priced/trivial)
        own_pred = (w.eth_move > 0).astype(int)
        acc_own = (own_pred == w.eth_up).mean()
        print(f"  ETH-own-move predicts ETH outcome: acc={acc_own:.4f} (baseline, mostly priced)")
        # lead-lag: residual BTC>ETH move -> bet ETH up
        for thr in (0.0, 0.0005, 0.001, 0.002):
            sig = w[w.resid.abs() >= thr].copy()
            pred = (sig.resid > 0).astype(int)
            hit = (pred == sig.eth_up)
            p, lo, hi = wilson(int(hit.sum()), len(sig))
            # OOS time split
            k = int(len(sig) * 0.5)
            oos = hit.iloc[k:].mean() if len(sig) - k > 0 else float("nan")
            print(f"   resid>|{thr:.4f}|: n={len(sig):5d} acc={p:.4f}[{lo:.4f},{hi:.4f}] "
                  f"OOS={oos:.4f}")

        # KEY test: does BTC residual predict ETH outcome BEYOND ETH's own move?
        # logistic-ish: among windows where ETH's own move is ~flat (undecided),
        # does BTC residual break the tie?
        flat = w[w.eth_move.abs() < w.eth_move.abs().median() * 0.5].copy()
        pred = (flat.resid > 0).astype(int)
        hit = (pred == flat.eth_up)
        p, lo, hi = wilson(int(hit.sum()), len(flat))
        print(f"  [ETH ~flat so far] BTC-resid predicts ETH: acc={p:.4f}[{lo:.4f},{hi:.4f}] n={len(flat)}")


if __name__ == "__main__":
    main()
