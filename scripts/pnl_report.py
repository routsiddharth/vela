#!/usr/bin/env python3
"""Vela live PnL report — screenshottable day-by-day table.

Run from the repo root:
    python scripts/pnl_report.py            # local time (machine TZ, UTC+3)
    python scripts/pnl_report.py --utc      # bucket days in UTC instead

All dollar figures are INDEXED to a base of 100 at the very start (real start
balance $50 -> 100), so the screenshot never reveals the actual account size.
Daily % and totals are scale-free and identical either way.
"""
import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from livepaper import config as C  # noqa: E402

DB = C.SHARED_PORTFOLIO_DB
START_DAY = "2026-06-18"          # first full bot day after the $50 reset
BASE_INDEX = 100.0
SCALE = BASE_INDEX / C.BANKROLL   # $ -> index points (2.0 at $50 base)


def fetch(use_utc: bool):
    """Return {day: {'BTC': [net,...], 'ETH': [net,...]}} for traded windows."""
    tz = "" if use_utc else ",'localtime'"
    sql = f"""
        select date(ts_ms/1000,'unixepoch'{tz}) as day, asset, net
        from settlements
        where net != 0 and date(ts_ms/1000,'unixepoch'{tz}) >= ?
        order by day
    """
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = con.execute(sql, (START_DAY,)).fetchall()
    con.close()
    days = {}
    for day, asset, net in rows:
        days.setdefault(day, {"BTC": [], "ETH": []})
        days[day].setdefault(asset, []).append(net)
    return days


def wl(nets):
    return sum(1 for n in nets if n > 0), sum(1 for n in nets if n < 0)


def fmt_pnl(x):
    return f"{x:+.2f}"


def fmt_wl(w, l):
    return f"{w}/{l}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true",
                    help="bucket days in machine local time (UTC+3) instead of UTC")
    ap.add_argument("--vertical", "-v", action="store_true",
                    help="slim portrait table: day, net P&L, W/L, gain%% only")
    args = ap.parse_args()

    days = fetch(use_utc=not args.local)
    if not days:
        print("No settled windows since", START_DAY)
        return

    bal = C.BANKROLL  # real running balance ($)
    rows = []
    total_btc = total_eth = 0.0
    tb_w = tb_l = te_w = te_l = 0

    # walk EVERY calendar day in the range so no-trade days show as zero rows
    # (never skip a day — skipping silently changes the day count).
    first = date.fromisoformat(START_DAY)
    last = max(date.fromisoformat(d) for d in days)
    all_days = []
    d = first
    while d <= last:
        all_days.append(d.isoformat())
        d += timedelta(days=1)

    wide_rows = []
    slim_rows = []
    for day in all_days:
        bucket = days.get(day, {})
        btc = bucket.get("BTC", [])
        eth = bucket.get("ETH", [])
        bp, ep = sum(btc), sum(eth)
        net = bp + ep
        bw, bl = wl(btc)
        ew, el = wl(eth)
        start_idx = bal * SCALE
        day_pct = (net / bal * 100) if bal else 0.0
        wide_rows.append((
            day[5:], f"{start_idx:7.2f}",
            fmt_pnl(bp * SCALE), fmt_wl(bw, bl),
            fmt_pnl(ep * SCALE), fmt_wl(ew, el),
            fmt_pnl(net * SCALE), fmt_wl(bw + ew, bl + el),
            f"{day_pct:+.2f}%",
        ))
        slim_rows.append((
            day[5:], fmt_pnl(net * SCALE),
            fmt_wl(bw + ew, bl + el), f"{day_pct:+.2f}%",
        ))
        bal += net
        total_btc += bp
        total_eth += ep
        tb_w += bw; tb_l += bl; te_w += ew; te_l += el

    n_days = len(wide_rows)
    final_idx = bal * SCALE
    total_pct = (bal / C.BANKROLL - 1) * 100
    # compounded average daily return (geometric mean)
    avg_daily = ((bal / C.BANKROLL) ** (1 / n_days) - 1) * 100

    if args.vertical:
        hdr = ["Day", "Net P&L", "W/L", "Gain %"]
        rows = slim_rows
    else:
        hdr = ["Day", "Start", "BTC P&L", "BTC W/L",
               "ETH P&L", "ETH W/L", "Net P&L", "Tot W/L", "Day %"]
        rows = wide_rows

    cols = list(zip(*([hdr] + [list(r) for r in rows])))
    w = [max(len(str(c)) for c in col) for col in cols]

    def line(cells):
        return "│ " + " │ ".join(str(c).rjust(w[i]) for i, c in enumerate(cells)) + " │"

    def border(left, mid, right):
        return left + mid.join("─" * (wi + 2) for wi in w) + right

    top = border("┌", "┬", "┐")
    sep = border("├", "┼", "┤")
    bot = border("└", "┴", "┘")
    title = "VELA DAY BY DAY"
    if args.local:
        title += "  [local UTC+3]"

    print()
    print(" " + title)
    print(top)
    print(line(hdr))
    print(sep)
    for r in rows:
        print(line(r))
    print(bot)

    # summary
    if args.vertical:
        print(f"  Index:        {BASE_INDEX:.2f}  →  {final_idx:.2f}")
        print(f"  Total PnL:    {total_pct:+.2f}%")
        print(f"  Avg daily:    {avg_daily:+.3f}%/day")
    else:
        print(f"  Index:        {BASE_INDEX:.2f}  →  {final_idx:.2f}   ({n_days} days)")
        print(f"  Total PnL:    {total_pct:+.2f}%")
        print(f"  Avg daily (compounded):  {avg_daily:+.3f}%/day")
        print(f"  BTC total:    {fmt_pnl(total_btc * SCALE)}  ({tb_w}/{tb_l} W/L)")
        print(f"  ETH total:    {fmt_pnl(total_eth * SCALE)}  ({te_w}/{te_l} W/L)")
    print()
    print("  Parameters")
    print(f"    p_side gate:   BTC {C.P_SIDE_MIN_BY_ASSET['BTC']:.2f}   "
          f"ETH {C.P_SIDE_MIN_BY_ASSET['ETH']:.2f}")
    print(f"    win_px_floor:  {C.WIN_PX_FLOOR:.2f}     cap: {C.CAP:.2f}")
    if not args.vertical:
        print(f"    sizing:        {C.PORTFOLIO_FRACTION:.0%} of ledger / order (min 1 contract)")
        print(f"    strong-take:   ask ≥ {C.STRONG_TAKE_THRESH:.2f} "
              f"(<{C.STRONG_TAKE_SEC_HI:.0f}s, BTC 15m only)")
    else:
        print(f"    strong-take:   ask ≥ {C.STRONG_TAKE_THRESH:.2f}")
    print()


if __name__ == "__main__":
    main()
