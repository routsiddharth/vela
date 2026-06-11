"""Pull Binance BTCUSDT 1s closes for the final 300s of every settled window.

Output: backtest/data/binance_1s.parquet  (ticker, sec_to_close, price)
sec_to_close: integer seconds before close_time (1..300); price = 1s kline close.
Resumable: skips tickers already present.
"""
from __future__ import annotations
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
import httpx, pandas as pd
from pathlib import Path

OUT = Path("backtest/data/binance_1s.parquet")
HOST = "https://data-api.binance.vision"
WINDOW = 300  # seconds before close to fetch

mk = pd.read_parquet("backtest/data/markets.parquet")

done = set()
if OUT.exists():
    done = set(pd.read_parquet(OUT, columns=["ticker"]).ticker.unique())
    print("resume: already have", len(done))

todo = [r for r in mk.itertuples() if r.ticker not in done]
print("to fetch:", len(todo))

_client = httpx.Client(timeout=20.0)

def fetch(r):
    ct = pd.Timestamp(r.close_dt).to_pydatetime()
    end_ms = int(ct.timestamp()*1000)
    start_ms = end_ms - WINDOW*1000
    for attempt in range(5):
        try:
            resp = _client.get(f"{HOST}/api/v3/klines",
                params={"symbol":"BTCUSDT","interval":"1s","startTime":start_ms,"endTime":end_ms,"limit":1000})
            if resp.status_code in (429,418):
                import time; time.sleep(1.0*(attempt+1)); continue
            resp.raise_for_status()
            j = resp.json()
            out=[]
            for c in j:
                open_ms=int(c[0]); close_px=float(c[4])
                sec_to_close = round((end_ms - open_ms)/1000)
                if 0 <= sec_to_close <= WINDOW:
                    out.append((r.ticker, sec_to_close, close_px))
            return out
        except Exception:
            import time; time.sleep(0.5*(attempt+1))
    return []

rows=[]; n=0
BATCH=400
with ThreadPoolExecutor(max_workers=12) as ex:
    futs={ex.submit(fetch,r):r for r in todo}
    for f in as_completed(futs):
        rows.extend(f.result()); n+=1
        if n % 500 == 0: print("fetched", n, "/", len(todo))
        if len(rows) >= BATCH*300:
            df=pd.DataFrame(rows, columns=["ticker","sec_to_close","price"])
            if OUT.exists():
                df=pd.concat([pd.read_parquet(OUT), df], ignore_index=True)
            df.to_parquet(OUT); rows=[]
            print("  checkpoint saved, total tickers:", df.ticker.nunique())

if rows:
    df=pd.DataFrame(rows, columns=["ticker","sec_to_close","price"])
    if OUT.exists():
        df=pd.concat([pd.read_parquet(OUT), df], ignore_index=True)
    df.to_parquet(OUT)
print("DONE. total rows:", len(pd.read_parquet(OUT)), "tickers:", pd.read_parquet(OUT).ticker.nunique())
