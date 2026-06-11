"""Pull ETHUSDT 1m closes over the full markets span -> eth_1m.parquet.
Mirrors binance_1m_full for BTC so we can test BTC->ETH lead-lag."""
import httpx, pandas as pd, time
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
OUT = DATA / "eth_1m.parquet"
HOST = "https://data-api.binance.vision"

m = pd.read_parquet(DATA / "markets.parquet")
m["close_dt"] = pd.to_datetime(m["close_dt"])
start_ms = int(m.close_dt.min().timestamp() * 1000) - 20 * 60 * 1000
end_ms = int(m.close_dt.max().timestamp() * 1000) + 60 * 1000

c = httpx.Client(timeout=30.0)
rows = []
cur = start_ms
while cur < end_ms:
    for attempt in range(6):
        try:
            r = c.get(f"{HOST}/api/v3/klines", params={
                "symbol": "ETHUSDT", "interval": "1m",
                "startTime": cur, "endTime": end_ms, "limit": 1000})
            if r.status_code in (429, 418):
                time.sleep(1.0 * (attempt + 1)); continue
            r.raise_for_status()
            j = r.json()
            break
        except Exception as e:
            time.sleep(0.5 * (attempt + 1)); j = []
    if not j:
        break
    for k in j:
        rows.append((int(k[0]), float(k[4])))
    cur = int(j[-1][0]) + 60_000
    if len(rows) % 20000 < 1000:
        print("fetched", len(rows), "minutes")

df = pd.DataFrame(rows, columns=["open_ms", "close"]).drop_duplicates("open_ms")
df.to_parquet(OUT)
print("DONE eth_1m:", len(df), "minutes",
      pd.to_datetime(df.open_ms.min(), unit="ms"), "->",
      pd.to_datetime(df.open_ms.max(), unit="ms"))
