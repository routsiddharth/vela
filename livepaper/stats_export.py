"""Read-only public-stats exporter.

Reads the shared settlements ledger (data_shared/portfolio.db) and emits a small
JSON payload of headline live-trading metrics for public display (personal site).

It NEVER touches the bot, places no orders, and opens the DB read-only. Designed to
run on a cron/launchd timer; the JSON is then pushed to Cloudflare KV (see
publish_stats.py) and served by a Worker route.

    python -m livepaper.stats_export                 # -> stdout
    python -m livepaper.stats_export -o stats.json   # -> file

Metric definitions (settlements: one row per settled window per pathway, `net` is
the real per-window PnL in USD):
  - net == 0 rows are no-fill windows (gate fired, bid unhit) -> NOT counted as a
    trade, but counted in windows_total.
  - traded = rows with net != 0; wins net>0; losses net<0; win_rate = wins/traded.
  - equity curve is built from traded rows only (net==0 doesn't move the line), so
    the payload stays compact and every point is a real PnL step.
  - days bucket in UTC (timezone re-buckets near midnight; see CLAUDE.md). ts_ms is
    included so the client can rebucket to local if desired.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import config as C

SEED = float(C.BANKROLL)  # $50 reset baseline; return % is measured against this


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    # read-only URI so the exporter can never mutate the live ledger
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def compute(db_path: Path, *, updated_ms: int) -> dict:
    con = _connect_ro(db_path)
    try:
        rows = con.execute(
            "select net, asset, kind, ts_ms from settlements order by ts_ms asc"
        ).fetchall()
    finally:
        con.close()

    windows_total = len(rows)
    traded = [r for r in rows if r[0] != 0]
    wins = sum(1 for r in traded if r[0] > 0)
    losses = sum(1 for r in traded if r[0] < 0)
    total_net = round(sum(r[0] for r in rows), 4)

    # equity curve from traded rows only (each point is a real PnL step)
    equity_curve = []
    equity = SEED
    peak = SEED
    max_dd_pct = 0.0
    for net, _asset, _kind, ts_ms in traded:
        equity = round(equity + net, 4)
        equity_curve.append([ts_ms, equity])
        peak = max(peak, equity)
        if peak > 0:
            dd = (peak - equity) / peak * 100.0
            max_dd_pct = max(max_dd_pct, dd)

    # daily buckets (UTC)
    daily: dict[str, dict] = {}
    for net, _asset, _kind, ts_ms in rows:
        day = _utc_day(ts_ms)
        d = daily.setdefault(day, {"day": day, "net": 0.0, "traded": 0, "wins": 0})
        d["net"] = round(d["net"] + net, 4)
        if net != 0:
            d["traded"] += 1
            if net > 0:
                d["wins"] += 1
    daily_list = sorted(daily.values(), key=lambda d: d["day"])
    days_with_trades = [d for d in daily_list if d["traded"] > 0]

    by_asset: dict[str, float] = {}
    by_kind: dict[str, float] = {}
    for net, asset, kind, _ts in rows:
        by_asset[asset] = round(by_asset.get(asset, 0.0) + net, 4)
        by_kind[kind] = round(by_kind.get(kind, 0.0) + net, 4)

    win_rate = round(wins / len(traded), 4) if traded else 0.0
    avg_daily_net = (
        round(total_net / len(days_with_trades), 4) if days_with_trades else 0.0
    )
    return_pct = round(total_net / SEED * 100.0, 3) if SEED else 0.0

    return {
        "updated_ms": updated_ms,
        "seed": SEED,
        "summary": {
            "total_net": total_net,
            "return_pct": return_pct,
            "win_rate": win_rate,
            "traded": len(traded),
            "wins": wins,
            "losses": losses,
            "windows_total": windows_total,
            "days_live": len(daily_list),
            "days_traded": len(days_with_trades),
            "avg_daily_net": avg_daily_net,
            "max_drawdown_pct": round(max_dd_pct, 3),
        },
        "by_asset": by_asset,
        "by_kind": by_kind,
        "daily": daily_list,
        "equity_curve": equity_curve,
    }


def _utc_day(ts_ms: int) -> str:
    import datetime as _dt

    return _dt.datetime.fromtimestamp(ts_ms / 1000, tz=_dt.timezone.utc).strftime(
        "%Y-%m-%d"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Export public live-trading stats JSON.")
    ap.add_argument("-o", "--out", help="write JSON here (default: stdout)")
    ap.add_argument(
        "--db", default=str(C.SHARED_PORTFOLIO_DB), help="path to portfolio.db"
    )
    ap.add_argument(
        "--now-ms",
        type=int,
        default=None,
        help="override the updated_ms stamp (default: wall clock)",
    )
    args = ap.parse_args(argv)

    if args.now_ms is None:
        import time

        args.now_ms = int(time.time() * 1000)

    payload = compute(Path(args.db), updated_ms=args.now_ms)
    text = json.dumps(payload, separators=(",", ":"))
    if args.out:
        Path(args.out).write_text(text)
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
