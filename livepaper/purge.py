"""Purge old data from paper.db files. Run every 6 hours via cron or scheduled script.

Retention policy:
  prices, book_snaps, estimates, trades  — 24 h rolling
  debias                                 — 48 h rolling (model needs multi-day samples)
  events (non-PnL kinds)                 — 24 h rolling
  orders                                 — 24 h rolling
  fills, windows, strong_settle events   — forever (actual PnL records)

Safety: aborts if the DB hasn't been written to in the last 10 minutes.
"""
from __future__ import annotations
import sqlite3
import sys
import time
from pathlib import Path


_24H_S  = 24 * 3600
_48H_S  = 48 * 3600
_10MIN_S = 10 * 60


def purge(db_path: Path, label: str) -> None:
    if not db_path.exists():
        print(f"[purge:{label}] {db_path} not found, skipping")
        return

    now_s  = int(time.time())
    now_ms = now_s * 1000
    cut_24h_ms = (now_s - _24H_S) * 1000
    cut_48h_s  =  now_s - _48H_S   # debias uses epoch seconds, not ms

    db = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
    try:
        # Safety: only purge if the bot is actively writing (prices updated recently)
        row = db.execute("SELECT MAX(ts_ms) FROM prices").fetchone()
        last_write_ms = row[0] if row and row[0] else 0
        age_s = (now_ms - last_write_ms) / 1000
        if age_s > _10MIN_S:
            print(f"[purge:{label}] last price write {age_s/60:.1f} min ago — "
                  f"bot may be down, skipping purge")
            return

        totals: dict[str, int] = {}

        # High-volume model tables: 24 h
        for tbl in ("prices", "book_snaps", "estimates", "trades"):
            n = db.execute(f"DELETE FROM {tbl} WHERE ts_ms < ?",
                           (cut_24h_ms,)).rowcount
            totals[tbl] = n

        # Debias: 48 h (close_ts is epoch seconds)
        n = db.execute("DELETE FROM debias WHERE close_ts < ?",
                       (cut_48h_s,)).rowcount
        totals["debias"] = n

        # Events: 24 h, but keep strong_settle + live_halt forever
        n = db.execute(
            "DELETE FROM events WHERE ts_ms < ? AND kind NOT IN ('strong_settle','live_halt')",
            (cut_24h_ms,)
        ).rowcount
        totals["events"] = n

        # Orders: 24 h
        n = db.execute("DELETE FROM orders WHERE ts_ms < ?",
                       (cut_24h_ms,)).rowcount
        totals["orders"] = n

        db.commit()
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        deleted = sum(totals.values())
        detail = "  ".join(f"{k}={v}" for k, v in totals.items() if v)
        print(f"[purge:{label}] deleted {deleted} rows  ({detail or 'nothing to purge'})")

    finally:
        db.close()


if __name__ == "__main__":
    root = Path(__file__).parent

    targets = [
        (root / "data_btc" / "paper.db", "BTC"),
        (root / "data_eth" / "paper.db", "ETH"),
    ]

    # Allow overriding from CLI: python -m livepaper.purge btc  or  eth
    if len(sys.argv) > 1:
        want = {a.lower() for a in sys.argv[1:]}
        targets = [(p, lbl) for p, lbl in targets if lbl.lower() in want]

    for path, label in targets:
        purge(path, label)
