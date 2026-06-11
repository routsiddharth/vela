"""Binance-vs-CF-Benchmark RTI drift test for any asset's 15M up/down series.

For BTC we already know: Binance BTCUSDT runs ~$25 HIGH vs CF BRTI and the bias
DRIFTS (weekly median -$31 -> +$97 over 2 months), so the de-bias must be causal.
This checks whether ETH (ETHUSDT vs CF ERTI) behaves the same way, so we know the
same causal trailing-median de-bias is viable before trading KXETH15M.

Pulls recent settled <SERIES> markets (expiration_value = RTI settle + close_time),
fetches Binance <SYMBOL> 1s klines for the 60s before each close, computes
  err = binance_avg60 - rti_settle
and reports magnitude, sign, dispersion, and drift over time.

Run:  python -m backtest.analysis.drift_test BTC | ETH   (from bitcoin/, ingest venv)
"""
from __future__ import annotations
import sys, time, statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import httpx
from backtest.kalshi_client import Kalshi

ASSETS = {
    "BTC": ("KXBTC15M", "BTCUSDT", "BRTI"),
    "ETH": ("KXETH15M", "ETHUSDT", "ERTI"),
}
BINANCE = "https://data-api.binance.vision"
N = 240   # settled windows to sample (~2.5 days of 15M)


def epoch(iso): return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def main() -> None:
    asset = (sys.argv[1] if len(sys.argv) > 1 else "ETH").upper()
    series, symbol, index = ASSETS[asset]
    k = Kalshi(); http = httpx.Client(timeout=20.0)

    # 1) recent settled windows: RTI settle (expiration_value) + close_time
    wins, cursor = [], None
    while len(wins) < N:
        p = {"series_ticker": series, "status": "settled", "limit": 100}
        if cursor: p["cursor"] = cursor
        r = k.get("/markets", p)
        ms = r.get("markets", [])
        for m in ms:
            ev = m.get("expiration_value")
            if ev and m.get("close_time"):
                try:
                    wins.append((m["ticker"], epoch(m["close_time"]), float(ev)))
                except ValueError:
                    pass
        cursor = r.get("cursor")
        if not cursor or not ms:
            break
    wins.sort(key=lambda x: x[1])
    wins = wins[-N:]
    print(f"\n{asset}: {series} settled windows={len(wins)}  index={index}  binance={symbol}")

    # 2) Binance avg60 over the 60s before each close
    def avg60(close_ts):
        end = close_ts * 1000; start = end - 60 * 1000
        for a in range(4):
            try:
                r = http.get(f"{BINANCE}/api/v3/klines", params={
                    "symbol": symbol, "interval": "1s",
                    "startTime": start, "endTime": end, "limit": 70})
                if r.status_code in (429, 418): time.sleep(1.0 * (a + 1)); continue
                r.raise_for_status()
                cs = [float(c[4]) for c in r.json() if start <= int(c[0]) < end]
                return sum(cs) / len(cs) if cs else None
            except Exception:
                time.sleep(0.5 * (a + 1))
        return None

    with ThreadPoolExecutor(max_workers=12) as ex:
        avgs = list(ex.map(lambda w: avg60(w[1]), wins))

    errs, rows = [], []
    for (tk, ct, rti), a in zip(wins, avgs):
        if a is None: continue
        errs.append(a - rti)
        rows.append((ct, a - rti, rti))
    if not errs:
        print("  no data"); return

    rel = 100 * statistics.mean(errs) / statistics.mean([r[2] for r in rows])
    print(f"  samples with binance data: {len(errs)}")
    print(f"  err = binance_avg60 - {index}_settle  (USD):")
    print(f"    mean   {statistics.mean(errs):+8.2f}   ({rel:+.3f}% of price)")
    print(f"    median {statistics.median(errs):+8.2f}")
    print(f"    stdev  {statistics.pstdev(errs):8.2f}")
    print(f"    min/max {min(errs):+.2f} / {max(errs):+.2f}")

    # 3) drift: bucket the errors into 6 time slices, show the median per slice
    rows.sort()
    b = max(1, len(rows) // 6)
    print(f"  drift (median err per ~{b}-window slice, oldest->newest):")
    line = []
    for i in range(0, len(rows), b):
        sl = rows[i:i + b]
        med = statistics.median([e for _, e, _ in sl])
        t0 = datetime.fromtimestamp(sl[0][0], timezone.utc).strftime("%m-%d %H:%M")
        line.append(f"{t0} {med:+.1f}")
    print("    " + "  |  ".join(line))
    # 4) viability of a causal trailing-24h median de-bias: residual after removing it
    res = []
    look = 96
    for i in range(len(rows)):
        if i < 20: continue
        prior = [e for _, e, _ in rows[max(0, i - look):i]]
        res.append(rows[i][1] - statistics.median(prior))
    if res:
        print(f"  AFTER causal trailing-median de-bias: residual mean {statistics.mean(res):+.2f} "
              f"std {statistics.pstdev(res):.2f}  (this is what the live gate sees)")


if __name__ == "__main__":
    main()
