"""Step 4 — config-regime map for the BTC live period.

Tags each event/window by the frozen-config regime it ran under, derived from
STRATEGY.md's change-log and confirmed against the orders/events tables. Entry logic
(p_side gate 0.84, strong-take 0.95) is constant across the whole live period; the
boundaries below are the SIZING switch and the v1->v2 endpoint dead window.

Import `label(ts_ms)` in the calibration / significance code.
"""
from __future__ import annotations

# epoch-ms boundaries (UTC), verified
T_410_ONSET = 1781819100_000   # 2026-06-18 21:45:00 — v1 endpoint starts returning HTTP 410
T_V2_FIX    = 1781942160_000   # 2026-06-20 07:56:00 — migrated to v2 create-order endpoint

REGIMES = [
    # label,  start_ms,   end_ms,        keep?,  note
    ("R0_fixed5",  0,            T_410_ONSET, True,
     "06-16->06-18 21:45: gate 0.84, strong-take 0.95, ~5 contracts (ledger ~$50). Orders working."),
    ("DEAD_410",   T_410_ONSET,  T_V2_FIX,    False,
     "06-18 21:45->06-20 07:56: v1 endpoint HTTP 410 -> orders SILENTLY FAILED. 06-19=0 fills. EXCLUDE."),
    ("R1_stable",  T_V2_FIX,     1 << 62,     True,
     "06-20 07:56->end: v2 endpoint, gate 0.84, strong-take 0.95, dynamic 10% sizing (5.7->8.8 ct). "
     "The clean frozen-config bulk (~8 days) — primary period for fill model + significance."),
]


def label(ts_ms: int) -> str:
    for name, lo, hi, _keep, _ in REGIMES:
        if lo <= ts_ms < hi:
            return name
    return "UNKNOWN"


def keep(ts_ms: int) -> bool:
    """False for the DEAD_410 window (orders failed) — exclude from fill analysis."""
    for _name, lo, hi, k, _ in REGIMES:
        if lo <= ts_ms < hi:
            return k
    return False


if __name__ == "__main__":
    import sqlite3
    from pathlib import Path
    ROOT = Path(__file__).resolve().parent.parent
    con = sqlite3.connect(f"file:{ROOT}/livepaper/data_btc/paper.db?mode=ro", uri=True)
    print("placements per regime (action='place'):")
    rows = con.execute("select ts_ms from orders where action='place'").fetchall()
    from collections import Counter
    c = Counter(label(r[0]) for r in rows)
    for name, *_ in REGIMES:
        print(f"  {name:11} {c.get(name,0):5}")
    con.close()
