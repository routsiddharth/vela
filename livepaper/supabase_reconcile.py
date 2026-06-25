"""Reconcile Supabase against the local portfolio.db — local is the source of truth.

Run on a schedule (cron, every 6h). Makes the cloud settlements/portfolio match the
local ledger exactly: pushes rows missing in the cloud, deletes cloud rows that no
longer exist locally (e.g. after a manual reset), and syncs the balance. Prints a
short discrepancy report and exits non-zero only on an unrecoverable error.

    python -m livepaper.supabase_reconcile
"""
from __future__ import annotations

import argparse
import sqlite3
import sys

from . import config as C
from .supabase_sync import make_client, upsert


def _fetch_cloud_keys(client, base) -> set[str]:
    keys, offset, page = set(), 0, 1000
    while True:
        r = client.get(f"{base}/settlements", params={"select": "key"},
                       headers={"Range-Unit": "items", "Range": f"{offset}-{offset + page - 1}"})
        r.raise_for_status()
        batch = [row["key"] for row in r.json()]
        keys.update(batch)
        if len(batch) < page:
            break
        offset += page
    return keys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(C.SHARED_PORTFOLIO_DB))
    args = ap.parse_args()

    db = sqlite3.connect(args.db)
    local = {r[0]: r for r in db.execute(
        "SELECT key,ticker,kind,asset,net,ts_ms FROM settlements")}
    prow = db.execute("SELECT id,balance,updated_ts_ms FROM portfolio WHERE id=1").fetchone()
    db.close()

    client, base = make_client()
    try:
        cloud_keys = _fetch_cloud_keys(client, base)
        local_keys = set(local)

        missing = local_keys - cloud_keys          # in local, not cloud -> push
        extra = cloud_keys - local_keys            # in cloud, not local -> delete (reset)

        if missing:
            rows = [dict(zip(["key", "ticker", "kind", "asset", "net", "ts_ms"], local[k]))
                    for k in missing]
            upsert(client, base, "settlements", rows, on_conflict="key")

        for k in extra:
            r = client.delete(f"{base}/settlements", params={"key": f"eq.{k}"})
            r.raise_for_status()

        if prow:
            upsert(client, base, "portfolio",
                   [{"id": prow[0], "balance": prow[1], "updated_ts_ms": prow[2]}],
                   on_conflict="id")

        print(f"reconcile: local={len(local_keys)} cloud={len(cloud_keys)} "
              f"pushed={len(missing)} deleted={len(extra)} "
              f"balance={prow[1] if prow else '?'}")
        return 0
    except Exception as e:
        print(f"reconcile FAILED: {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
