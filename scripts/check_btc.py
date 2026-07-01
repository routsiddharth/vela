#!/usr/bin/env python3
"""check_btc.py — reconcile the LIVE BTC bot against the REAL Kalshi account.

Run from the repo root:
    python3 scripts/check_btc.py

The account is shared with your manual trades, so the bot's own report is muddy.
This matches the bot's orders to ACTUAL account fills by order_id and computes the
bot's REAL realized PnL, plus open exposure, resting orders, and halt status.
Read-only: it never places or cancels anything.
"""
import os, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from livepaper.trading.broker import LiveBroker

DATA = ROOT / "livepaper" / "data_btc"
DB, PIDF, KILL = DATA / "paper.db", DATA / "lp.pid", DATA / "KILL"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0); return True
    except Exception:
        return False


def main() -> None:
    b = LiveBroker(demo=False)

    # --- bot process / halt status ---
    pid = int(PIDF.read_text()) if PIDF.exists() else None
    running = bool(pid and _alive(pid))
    halt = "  [KILL file present -> trading halted]" if KILL.exists() else ""
    print(f"bot: {'RUNNING pid=' + str(pid) if running else 'NOT running'}{halt}")

    # --- account snapshot ---
    bal = b.balance_dollars()
    resting = b.resting_orders()
    print(f"real balance: ${bal:.2f}   |   resting orders now: {len(resting)}")
    for o in resting:
        print(f"   resting: {o.get('ticker')} {o.get('side')} "
              f"{o.get('remaining_count')}@{o.get('price') or o.get('yes_price') or o.get('no_price')}")

    # --- open BTC exposure (filled positions not yet settled) ---
    openpos = [p for p in b.positions() if p.get("position") and "BTC" in (p.get("ticker") or "")]
    if openpos:
        print("OPEN BTC POSITIONS (live exposure):")
        for p in openpos:
            print(f"   {p['ticker']}  pos={p['position']}  exposure={p.get('market_exposure')}")
    else:
        print("open BTC exposure: none")

    # --- reconcile the bot's fills -> REAL PnL ---
    if not DB.exists():
        print("\n(no data_btc/paper.db yet — bot hasn't written anything)")
        return
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    ours = {r[0] for r in c.execute(
        "SELECT order_id FROM orders WHERE action='place' AND order_id IS NOT NULL")}
    results = {r[0]: r[1] for r in c.execute("SELECT ticker, result FROM windows")}

    fills = [f for f in b.fills() if f.get("order_id") in ours]
    print(f"\n=== bot fills on the real account: {len(fills)} (recent) ===")
    if fills:
        print(f"{'window':<16}{'side':<5}{'ct':<5}{'fillpx':<8}{'result':<9}{'pnl$':>8}")
    realized, pending = 0.0, 0
    for f in sorted(fills, key=lambda x: x.get("ts", 0)):
        tk, side = f["ticker"], f["side"]
        ct = round(float(f["count_fp"]))
        px = float(f["no_price_dollars"] if side == "no" else f["yes_price_dollars"])
        fee = float(f.get("fee_cost") or 0)
        mk = tk.split("-", 1)[1] if "-" in tk else tk
        result = results.get(tk)
        if result is None:                       # window not settled yet
            print(f"{mk:<16}{side:<5}{ct:<5}{px:<8.2f}{'PENDING':<9}{'—':>8}")
            pending += 1
            continue
        won = result == side
        pnl = (ct * (1 - px) if won else -ct * px) - fee
        realized += pnl
        print(f"{mk:<16}{side:<5}{ct:<5}{px:<8.2f}{result:<9}{pnl:>+8.2f}")

    print(f"\nREAL realized PnL (settled bot fills): ${realized:+.2f}"
          + (f"   |   {pending} fill(s) pending settlement" if pending else ""))
    if realized <= -25:
        print("NOTE: realized <= -$25 — the bot's daily-loss halt should have fired.")


if __name__ == "__main__":
    main()
