"""Step 1 — Backfill BTCUSDT 1s spot for Jun 17-27 UTC to 100%, keep full OHLCV.

Only the SPOT side is recoverable (Kalshi book is not). Writes a clean continuous
1s tape to analysis/data/binance_1s_btc_full.parquet and reports how many seconds
were missing from the live `prices` table that this fills.

stdlib HTTP (urllib) since the venv lacks `requests`.
"""
from __future__ import annotations
import json, time, sqlite3, ssl, urllib.request, urllib.error
from pathlib import Path
import certifi
import pandas as pd

# local cert chain has a self-signed root -> system store fails (see feeds.py).
_SSL = ssl.create_default_context(cafile=certifi.where())

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "analysis/data/binance_1s_btc_full.parquet"
BASE = "https://data-api.binance.vision/api/v3/klines"
SYMBOL = "BTCUSDT"

# full days 17-27 inclusive: 06-17 00:00:00 UTC -> 06-28 00:00:00 UTC
START_MS = 1781654400_000   # 2026-06-17 00:00:00 UTC (verified)
END_MS   = 1782604800_000   # 2026-06-28 00:00:00 UTC (verified)


def fetch(start_ms: int, end_ms: int):
    url = (f"{BASE}?symbol={SYMBOL}&interval=1s"
           f"&startTime={start_ms}&endTime={end_ms}&limit=1000")
    req = urllib.request.Request(url, headers={"User-Agent": "vela-backfill/1.0"})
    last_err = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=20, context=_SSL) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 418):
                time.sleep(2 * (attempt + 1)); continue
            last_err = e; break
        except Exception as e:
            last_err = e; time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"fetch failed at {start_ms}: {type(last_err).__name__}: {last_err}")


def main():
    rows = {}
    cur = START_MS
    nreq = 0
    while cur < END_MS:
        batch = fetch(cur, END_MS)
        nreq += 1
        if not batch:
            cur += 1000_000
            continue
        for k in batch:
            sec = int(k[0]) // 1000
            rows[sec] = (sec, float(k[1]), float(k[2]), float(k[3]),
                         float(k[4]), float(k[5]))
        last = int(batch[-1][0])
        cur = last + 1000
        if nreq % 50 == 0:
            print(f"  ...{nreq} reqs, {len(rows)} secs, "
                  f"at {time.strftime('%m-%d %H:%M', time.gmtime(last/1000))}",
                  flush=True)
        time.sleep(0.05)

    df = pd.DataFrame(sorted(rows.values()),
                      columns=["epoch_sec", "open", "high", "low", "close", "volume"])
    df.to_parquet(OUT, index=False)

    span = (END_MS - START_MS) // 1000
    print(f"\nfetched {len(df)} distinct seconds in {nreq} requests")
    print(f"span seconds (17-27): {span}  coverage: {100*len(df)/span:.1f}%")

    # how many of these seconds were MISSING from the live prices table?
    con = sqlite3.connect(f"file:{ROOT}/livepaper/data_btc/paper.db?mode=ro", uri=True)
    live = set(r[0] for r in con.execute(
        "select distinct epoch_sec from prices where symbol='BTCUSDT' "
        "and epoch_sec>=? and epoch_sec<?", (START_MS // 1000, END_MS // 1000)))
    con.close()
    have = set(df["epoch_sec"])
    filled = have - live
    print(f"live prices had {len(live)} secs; backfill has {len(have)}; "
          f"NEWLY FILLED gaps: {len(filled)} ({100*len(filled)/span:.1f}% of the period)")
    print(f"written -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
