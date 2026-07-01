"""Step 5 — book-at-placement coverage.

For every order PLACEMENT (place/*), find the nearest book_snaps row for the same
ticker and measure |dt|. Orders with no book within ~1s have unreliable features and
must be excluded from fill-model fitting. Run BEFORE step 3.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
con = sqlite3.connect(f"file:{ROOT}/livepaper/data_btc/paper.db?mode=ro", uri=True)

placements = con.execute(
    "select ts_ms, ticker, status from orders where action='place' order by ts_ms"
).fetchall()

def nearest_dt(ticker, ts):
    row = con.execute(
        "select min(abs(ts_ms-?)) from book_snaps "
        "where ticker=? and ts_ms between ? and ?",
        (ts, ticker, ts - 10000, ts + 10000)).fetchone()
    return row[0]  # ms, or None if no snap within +/-10s

buckets = {"<=1s": 0, "1-2s": 0, "2-5s": 0, "5-10s": 0, "none(>10s)": 0}
by_status = {}
worst = []
for ts, ticker, status in placements:
    dt = nearest_dt(ticker, ts)
    if dt is None:
        b = "none(>10s)"
    elif dt <= 1000:
        b = "<=1s"
    elif dt <= 2000:
        b = "1-2s"
    elif dt <= 5000:
        b = "2-5s"
    else:
        b = "5-10s"
    buckets[b] += 1
    by_status.setdefault(status, {k: 0 for k in buckets})[b] += 1

con.close()

n = len(placements)
print(f"order placements (action='place'): {n}\n")
print("nearest book_snaps (same ticker) to each placement:")
for k in ["<=1s", "1-2s", "2-5s", "5-10s", "none(>10s)"]:
    print(f"  {k:12} {buckets[k]:5}  ({100*buckets[k]/n:5.1f}%)")

usable = buckets["<=1s"] + buckets["1-2s"]
print(f"\nUSABLE (book within 2s): {usable}/{n} ({100*usable/n:.1f}%)")
print(f"EXCLUDE (no book within 2s): {n-usable} ({100*(n-usable)/n:.1f}%)")

print("\nby placement type:")
for status, bk in sorted(by_status.items()):
    tot = sum(bk.values())
    good = bk["<=1s"] + bk["1-2s"]
    print(f"  {status:10} n={tot:5}  within2s={good:5} ({100*good/tot:5.1f}%)")
