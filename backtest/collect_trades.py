"""Pull Kalshi executed trades for the final 180s of a sample of settled windows.

Output: backtest/data/trades.parquet
columns: ticker, created_time, sec_to_close, yes_price, no_price, size, taker_side
Used to measure the real mispricing gap + available liquidity at favorable prices.
Resumable: skips tickers already present.
"""
from __future__ import annotations
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from pathlib import Path
from backtest.kalshi_client import Kalshi

OUT = Path("backtest/data/trades.parquet")
FINAL = 180
N_SAMPLE = 2500

mk = pd.read_parquet("backtest/data/markets.parquet").reset_index(drop=True)
# even sample across the whole period
if len(mk) > N_SAMPLE:
    idx = (pd.Series(range(N_SAMPLE)) * (len(mk)-1) / (N_SAMPLE-1)).round().astype(int).unique()
    sample = mk.iloc[idx].reset_index(drop=True)
else:
    sample = mk
print("sampling", len(sample), "windows for trades")

done=set()
if OUT.exists():
    done=set(pd.read_parquet(OUT, columns=["ticker"]).ticker.unique())
    print("resume: have", len(done))
todo=[r for r in sample.itertuples() if r.ticker not in done]
print("to fetch:", len(todo))

k = Kalshi()

def fetch(r):
    ct = pd.Timestamp(r.close_dt).to_pydatetime()
    end_ms_s = int(ct.timestamp())
    min_ts = end_ms_s - FINAL
    max_ts = end_ms_s
    rows=[]; cursor=None
    for _ in range(20):  # page cap
        p={"ticker":r.ticker,"limit":1000,"min_ts":min_ts,"max_ts":max_ts}
        if cursor: p["cursor"]=cursor
        try:
            resp=k.get("/markets/trades", p)
        except Exception:
            break
        tr=resp.get("trades",[])
        for t in tr:
            tts=dt.datetime.fromisoformat(t["created_time"].replace("Z","+00:00"))
            rows.append((r.ticker, t["created_time"], (ct-tts).total_seconds(),
                         float(t["yes_price_dollars"]), float(t["no_price_dollars"]),
                         float(t["count_fp"]), t.get("taker_side","")))
        cursor=resp.get("cursor")
        if not cursor or not tr: break
    return rows

rows=[]; n=0
with ThreadPoolExecutor(max_workers=6) as ex:
    futs={ex.submit(fetch,r):r for r in todo}
    for f in as_completed(futs):
        rows.extend(f.result()); n+=1
        if n%200==0: print("fetched", n, "/", len(todo), "rows so far", len(rows))
        if len(rows)>=150000:
            df=pd.DataFrame(rows, columns=["ticker","created_time","sec_to_close","yes_price","no_price","size","taker_side"])
            if OUT.exists(): df=pd.concat([pd.read_parquet(OUT),df],ignore_index=True)
            df.to_parquet(OUT); rows=[]
            print("  checkpoint, tickers:", df.ticker.nunique())

if rows:
    df=pd.DataFrame(rows, columns=["ticker","created_time","sec_to_close","yes_price","no_price","size","taker_side"])
    if OUT.exists(): df=pd.concat([pd.read_parquet(OUT),df],ignore_index=True)
    df.to_parquet(OUT)
d=pd.read_parquet(OUT)
print("DONE rows", len(d), "tickers", d.ticker.nunique())
