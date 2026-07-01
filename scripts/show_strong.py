#!/usr/bin/env python3
"""show_strong.py — history of the 'strong take' (>=0.95 taker) pathway.

Run from the repo root:
    python3 scripts/show_strong.py            # full history + summary
    python3 scripts/show_strong.py --tail     # also tail the [strong] lines from run.log

Read-only. Reconstructs every strong-pathway take from data_btc/paper.db (fills
tagged 'strong095 taker' + orders tagged 'strong095'), joins each to the window's
settled result, and computes per-take and cumulative REAL PnL. This pathway runs
ALONGSIDE the panic-fade; its trades are kept in a separate book, so this view is
ONLY the >=0.95 taker rule, not the panic-fade.
"""
import sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "livepaper" / "data_btc" / "paper.db"
LOG = ROOT / "livepaper" / "data_btc" / "run.log"
TAG_FILL, TAG_ORDER = "strong095 taker", "strong095"


def main() -> None:
    if not DB.exists():
        print(f"(no db yet at {DB})"); return
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    results = {r[0]: r[1] for r in c.execute("SELECT ticker, result FROM windows")}
    # one row per window the strong pathway filled
    takes = list(c.execute(
        "SELECT ticker, bet_side, SUM(qty), SUM(cost), SUM(fee), MIN(ts_ms), COUNT(*) "
        "FROM fills WHERE reason=? GROUP BY ticker ORDER BY MIN(ts_ms)", (TAG_FILL,)))
    # orders placed (incl. those that never filled)
    n_orders = c.execute("SELECT COUNT(*) FROM orders WHERE detail=? AND action='place'",
                         (TAG_ORDER,)).fetchone()[0]
    skips = c.execute("SELECT COUNT(*) FROM events WHERE kind='strong_skip'").fetchone()[0]
    errs = c.execute("SELECT COUNT(*) FROM events WHERE kind='strong_order_err'").fetchone()[0]

    print(f"strong-take pathway  (db: {DB.name})")
    print(f"orders placed: {n_orders}   filled windows: {len(takes)}   "
          f"skipped(cap): {skips}   errors: {errs}")
    if not takes:
        print("\n(no strong-pathway fills yet)")
        _maybe_tail()
        return

    print(f"\n{'time(utc)':<20}{'window':<18}{'side':<5}{'qty':>4}{'avgpx':>7}"
          f"{'fee':>6}{'result':>9}{'net$':>9}{'cum$':>9}")
    realized, pending, wins, settled = 0.0, 0, 0, 0
    from datetime import datetime, timezone
    for tk, side, qty, cost, fee, ts_ms, nf in takes:
        avg = cost / qty if qty else 0.0
        mk = tk.split("-", 1)[1] if "-" in tk else tk
        t = datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%m-%d %H:%M:%S")
        res = results.get(tk)
        if res is None:
            print(f"{t:<20}{mk:<18}{side:<5}{qty:>4.0f}{avg:>7.2f}{fee:>6.2f}"
                  f"{'PENDING':>9}{'—':>9}{'—':>9}")
            pending += 1
            continue
        won = (res == side)
        net = (qty if won else 0.0) - cost - fee
        realized += net
        settled += 1
        wins += int(won)
        print(f"{t:<20}{mk:<18}{side:<5}{qty:>4.0f}{avg:>7.2f}{fee:>6.2f}"
              f"{res:>9}{net:>+9.2f}{realized:>+9.2f}")

    wr = (wins / settled) if settled else 0.0
    settled_ct = sum(q for tk, _, q, *_ in takes if results.get(tk) is not None)
    print(f"\nsettled: {settled}  win-rate: {wr:.1%}  REAL realized: ${realized:+.2f}"
          + (f"   |   {pending} pending" if pending else ""))
    if settled_ct:
        print(f"net per contract: {100 * realized / settled_ct:+.2f}c  "
              f"(over {settled_ct:.0f} settled contracts)")
    _maybe_tail()


def _maybe_tail() -> None:
    if "--tail" in sys.argv and LOG.exists():
        lines = [l for l in LOG.read_text(errors="replace").splitlines() if "[strong]" in l]
        print(f"\n=== last {min(20, len(lines))} [strong] log lines ===")
        for l in lines[-20:]:
            print(l)


if __name__ == "__main__":
    main()
