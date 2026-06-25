"""One-shot: push the current local portfolio.db state up to Supabase.

Idempotent — safe to re-run. Reads the local SQLite ledger and upserts every
settlement, the single portfolio row, and the events audit log to the cloud, then
verifies row counts match.

    python -m livepaper.supabase_backfill            # uses config.SHARED_PORTFOLIO_DB
    python -m livepaper.supabase_backfill --db path  # explicit path
"""
from __future__ import annotations

import argparse
import sqlite3

from . import config as C
from .supabase_sync import make_client, upsert


def _rows(db: sqlite3.Connection, table: str, cols: list[str]) -> list[dict]:
    cur = db.execute(f"SELECT {','.join(cols)} FROM {table}")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _cloud_count(client, base, table: str) -> int:
    r = client.get(f"{base}/{table}", params={"select": "*", "limit": 1},
                   headers={"Prefer": "count=exact"})
    r.raise_for_status()
    # PostgREST returns Content-Range: 0-0/<total>
    rng = r.headers.get("content-range", "*/0")
    return int(rng.split("/")[-1])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(C.SHARED_PORTFOLIO_DB))
    args = ap.parse_args()

    db = sqlite3.connect(args.db)
    settlements = _rows(db, "settlements", ["key", "ticker", "kind", "asset", "net", "ts_ms"])
    portfolio = _rows(db, "portfolio", ["id", "balance", "updated_ts_ms"])
    events = _rows(db, "events", ["ts_ms", "kind", "detail"])
    db.close()

    client, base = make_client()
    print(f"local: {len(settlements)} settlements, {len(portfolio)} portfolio, "
          f"{len(events)} events -> pushing to {base}")

    upsert(client, base, "settlements", settlements, on_conflict="key")
    upsert(client, base, "portfolio", portfolio, on_conflict="id")
    # events has no natural PK; only push when cloud is empty to avoid duplicates.
    if _cloud_count(client, base, "events") == 0:
        upsert(client, base, "events", events)
        print(f"events: pushed {len(events)} (cloud was empty)")
    else:
        print("events: cloud non-empty, skipped (append-only audit; no PK to dedup)")

    cs = _cloud_count(client, base, "settlements")
    cp = _cloud_count(client, base, "portfolio")
    bal = client.get(f"{base}/portfolio", params={"select": "balance", "id": "eq.1"}).json()
    client.close()
    print(f"cloud now: settlements={cs} (local {len(settlements)}), portfolio={cp}, "
          f"balance={bal[0]['balance'] if bal else '?'}")
    print("OK" if cs == len(settlements) else "MISMATCH — re-run or check errors")


if __name__ == "__main__":
    main()
