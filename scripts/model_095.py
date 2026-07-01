#!/usr/bin/env python3
"""Model the proposed rule on the live BTC tape (read-only):
   if sec_to_close < 45 and ANY side >= 0.95, TAKE that side (ignore p_side),
   enter once at first trigger, hold to settlement. Taker fees.
"""
import sqlite3, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backtest" / "strategy_search"))
from fees import order_fee, TAKER

DB = ROOT / "livepaper" / "data_btc" / "paper.db"
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

THRESH_BY_SERIES = {"KXBTC15M": 0.95, "KXBTCD": 0.97}   # per-series take threshold
SEC_HI, SEC_LO = 45.0, 1.0
POSITION_USD = 5.0     # bot's live sizing
ENTRY_MAX = 0.99       # can't profitably "take" at >=1.00; cap at 0.99

wins = {r[0]: (r[1], r[2]) for r in
        c.execute("SELECT ticker,series,result FROM windows WHERE result IS NOT NULL")
        for r in [r]}  # ticker -> (series, result)
windows = {r[0]: (r[1], r[2]) for r in
           c.execute("SELECT ticker,series,result FROM windows WHERE result IS NOT NULL")}


def first_trigger_from_trades(tk, thr):
    """First print <45s where a side >= thr. Returns (side, entry_price) or None."""
    rows = c.execute(
        "SELECT sec_to_close,yes_price,no_price FROM trades "
        "WHERE ticker=? AND sec_to_close>=? AND sec_to_close<? ORDER BY sec_to_close DESC",
        (tk, SEC_LO, SEC_HI))
    for sec, yp, npx in rows:
        if thr <= yp <= ENTRY_MAX:
            return ("yes", yp)
        if thr <= npx <= ENTRY_MAX:
            return ("no", npx)
    return None


def first_trigger_from_book(tk, thr):
    """First book snap <45s where a side's ASK >= thr (what you'd pay to take)."""
    rows = c.execute(
        "SELECT sec_to_close,yes_ask,no_ask FROM book_snaps "
        "WHERE ticker=? AND sec_to_close>=? AND sec_to_close<? ORDER BY sec_to_close DESC",
        (tk, SEC_LO, SEC_HI))
    for sec, ya, na in rows:
        if ya is not None and thr <= ya <= ENTRY_MAX:
            return ("yes", ya)
        if na is not None and thr <= na <= ENTRY_MAX:
            return ("no", na)
    return None


def run(name, trigger_fn):
    agg = {}  # series -> stats
    losers = []
    for tk, (series, result) in windows.items():
        thr = THRESH_BY_SERIES.get(series)
        if thr is None:
            continue
        t = trigger_fn(tk, thr)
        s = agg.setdefault(series, dict(n=0, fired=0, win=0, gross=0.0, fee=0.0,
                                        net=0.0, ct=0.0))
        s["n"] += 1
        if t is None:
            continue
        side, entry = t
        ct = max(1, round(POSITION_USD / entry))
        fee = order_fee(ct, entry, TAKER)
        won = (result == side)
        gross = ct * (1 - entry) if won else -ct * entry
        net = gross - fee
        s["fired"] += 1
        s["win"] += int(won)
        s["gross"] += gross
        s["fee"] += fee
        s["net"] += net
        s["ct"] += ct
        if not won:
            losers.append((tk, side, entry, result, net))

    print(f"\n========== {name} ==========")
    print(f"{'series':<10}{'wins':>6}{'fired':>7}{'winrate':>9}{'net$':>9}"
          f"{'gross¢/ct':>11}{'fee¢/ct':>9}{'net¢/ct':>9}")
    tot = dict(fired=0, win=0, gross=0.0, fee=0.0, net=0.0, ct=0.0)
    for series, s in sorted(agg.items()):
        if s["fired"] == 0:
            print(f"{series:<10}{s['n']:>6}{0:>7}{'—':>9}{'—':>9}")
            continue
        wr = s["win"] / s["fired"]
        for k in ("fired", "win", "gross", "fee", "net", "ct"):
            tot[k] += s[k]
        print(f"{series:<10}{s['n']:>6}{s['fired']:>7}{wr:>8.1%}{s['net']:>+9.2f}"
              f"{100*s['gross']/s['ct']:>+11.2f}{100*s['fee']/s['ct']:>9.2f}"
              f"{100*s['net']/s['ct']:>+9.2f}")
    if tot["fired"]:
        wr = tot["win"] / tot["fired"]
        print(f"{'TOTAL':<10}{'':>6}{tot['fired']:>7}{wr:>8.1%}{tot['net']:>+9.2f}"
              f"{100*tot['gross']/tot['ct']:>+11.2f}{100*tot['fee']/tot['ct']:>9.2f}"
              f"{100*tot['net']/tot['ct']:>+9.2f}")
        print(f"  -> {tot['fired']} windows fired, {tot['fired']-tot['win']} losses, "
              f"total net ${tot['net']:+.2f} on ${POSITION_USD:.0f}/window taker sizing")
    if losers:
        print(f"  losing windows ({len(losers)}):")
        for tk, side, entry, result, net in sorted(losers, key=lambda x: x[4]):
            print(f"     {tk:<26} took {side}@{entry:.2f} -> settled {result}  {net:+.2f}")


run("TAKE AT ASK (realistic taker)", first_trigger_from_book)
run("TAKE AT PRINT (optimistic)", first_trigger_from_trades)
