"""Path A — scrape Coinbase BTC-USD historical TICKS for Jun 17-27 2026.

Walks /products/BTC-USD/trades backward by trade_id cursor (?after=<id> returns
older trades). Resumable: checkpoints the cursor + flushes ticks to chunk parquets
every N pages, so a crash/kill resumes where it left off. At completion merges chunks
-> coinbase_trades_btc.parquet (raw ticks) + coinbase_1s_btc.parquet (1s last-price).

~15M trades over the window (~16 trades/s) -> ~16k requests. certifi SSL like feeds.py.
Run in background; re-run to resume.
"""
from __future__ import annotations
import json, time, ssl, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone
import certifi
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATADIR = ROOT / "analysis/data"
CHUNKDIR = DATADIR / "coinbase_chunks"
CHUNKDIR.mkdir(parents=True, exist_ok=True)
STATE = DATADIR / "coinbase_scrape_state.json"
RAW_OUT = DATADIR / "coinbase_trades_btc.parquet"
SEC_OUT = DATADIR / "coinbase_1s_btc.parquet"

BASE = "https://api.exchange.coinbase.com/products/BTC-USD/trades"
_SSL = ssl.create_default_context(cafile=certifi.where())

WINDOW_START = 1781654400.0   # 2026-06-17 00:00:00 UTC (matches Binance backfill)
WINDOW_END   = 1782604800.0   # 2026-06-28 00:00:00 UTC

CHECKPOINT_PAGES = 50         # flush a chunk + save state every N pages (~50k trades)
THROTTLE = 0.13               # ~7.7 req/s, under Coinbase's ~10/s public limit


def iso_to_epoch(t: str) -> float:
    return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()


def fetch(after: int | None):
    url = BASE + "?limit=1000" + (f"&after={after}" if after else "")
    req = urllib.request.Request(url, headers={"User-Agent": "vela-cb-scrape/1.0"})
    for attempt in range(8):
        try:
            with urllib.request.urlopen(req, timeout=30, context=_SSL) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = float(e.headers.get("Retry-After", 0)) or (1.5 * (attempt + 1))
                time.sleep(wait); continue
            time.sleep(1.0 * (attempt + 1))
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"fetch failed after retries (after={after})")


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"cursor": None, "pages": 0, "trades": 0, "chunk": 0, "done": False,
            "oldest_iso": None}


def save_state(s):
    STATE.write_text(json.dumps(s, indent=2))


def flush_chunk(buf, idx):
    df = pd.DataFrame(buf, columns=["trade_id", "epoch", "price", "size", "side"])
    df.to_parquet(CHUNKDIR / f"chunk_{idx:05d}.parquet", index=False)


def finalize():
    chunks = sorted(CHUNKDIR.glob("chunk_*.parquet"))
    if not chunks:
        print("no chunks to merge"); return
    df = pd.concat((pd.read_parquet(c) for c in chunks), ignore_index=True)
    df = df.drop_duplicates("trade_id").sort_values("epoch").reset_index(drop=True)
    df = df[(df.epoch >= WINDOW_START) & (df.epoch < WINDOW_END)]
    df.to_parquet(RAW_OUT, index=False)
    # 1s last-trade price (the "close" tape), + ohlc/volume
    df["sec"] = df.epoch.astype("int64")
    g = df.groupby("sec")
    sec = pd.DataFrame({
        "epoch_sec": g.size().index,
        "open": g.price.first().values, "high": g.price.max().values,
        "low": g.price.min().values, "close": g.price.last().values,
        "volume": g["size"].sum().values, "n_trades": g.size().values})
    sec.to_parquet(SEC_OUT, index=False)
    span = int(WINDOW_END - WINDOW_START)
    print(f"\nDONE. {len(df):,} ticks -> {RAW_OUT.name}")
    print(f"1s tape: {len(sec):,} seconds ({100*len(sec)/span:.1f}% of {span} secs) -> {SEC_OUT.name}")


def main():
    s = load_state()
    if s["done"]:
        print("already done; finalizing."); finalize(); return
    buf, pages_since = [], 0
    cursor = s["cursor"]
    t0 = time.time()
    while not s["done"]:
        trades = fetch(cursor)
        if not trades:
            s["done"] = True; break
        for t in trades:
            ep = iso_to_epoch(t["time"])
            if ep >= WINDOW_END:
                continue                       # overshoot (now -> window end): skip
            if ep < WINDOW_START:
                s["done"] = True; break        # walked past the window: stop
            buf.append((t["trade_id"], ep, float(t["price"]), float(t["size"]), t["side"]))
        cursor = trades[-1]["trade_id"]         # lowest id on page -> next older page
        s["cursor"] = cursor
        s["pages"] += 1; pages_since += 1
        s["oldest_iso"] = trades[-1]["time"]
        if pages_since >= CHECKPOINT_PAGES or s["done"]:
            if buf:
                flush_chunk(buf, s["chunk"]); s["chunk"] += 1; s["trades"] += len(buf)
                buf = []
            save_state(s); pages_since = 0
            rate = s["pages"] / max(1e-9, time.time() - t0)
            print(f"  pages={s['pages']} trades~{s['trades']:,} "
                  f"at {s['oldest_iso']}  ({rate:.1f} req/s)", flush=True)
        time.sleep(THROTTLE)
    if buf:
        flush_chunk(buf, s["chunk"]); s["chunk"] += 1; s["trades"] += len(buf)
    save_state(s)
    finalize()


if __name__ == "__main__":
    main()
