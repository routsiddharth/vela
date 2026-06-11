"""Offline counterfactual: re-derive fills + PnL from the STORED data under any
price cap, without re-running live. Answers 'would a looser cap have traded?'.

Uses only the trades + windows already in paper.db. Per-window notional cap from
config; ignores the live cash constraint (this is a what-if on capture, not a
bankroll sim).  Run:  python -m livepaper.analyze_caps
"""
from __future__ import annotations
import math, sqlite3
from . import config as C


def fee(p):
    return max(0.01, math.ceil(0.07 * p * (1 - p) * 100) / 100)


def main() -> None:
    db = sqlite3.connect(f"file:{C.DB_PATH}?mode=ro", uri=True)
    rows_w = db.execute(
        "SELECT ticker, bet_side, result FROM windows "
        "WHERE gate_active=1 AND bet_side IS NOT NULL").fetchall()
    # won is computed from the settled result, NOT the stored `won` column (which
    # is NULL for windows that took no real fills).
    armed = [(tk, bet, (bet == "yes" and res == "yes") or (bet == "no" and res == "no"))
             for tk, bet, res in rows_w]
    print(f"\narmed (gate ON) windows: {len(armed)}")
    print(f"{'CAP':>5} {'windows_hit':>12} {'fills':>7} {'contracts':>10} "
          f"{'net $':>9} {'c/contract':>11} {'wins/losses':>12}")
    for cap in (0.97, 0.98, 0.99, 0.995):
        n_fills = n_ct = 0.0
        net = 0.0
        hit_windows = set()
        wins = losses = 0
        for ticker, bet, won in armed:
            rows = db.execute(
                "SELECT sec_to_close, yes_price, no_price, size FROM trades "
                "WHERE ticker=? AND sec_to_close BETWEEN ? AND ?",
                (ticker, C.SEC_LO, C.SEC_HI)).fetchall()
            wcost = 0.0
            for sec, yp, np_, sz in rows:
                px = yp if bet == "yes" else np_
                if px is None or not (0 < px <= cap) or sz is None:
                    continue
                room = (C.MAX_WINDOW_NOTIONAL - wcost) / px
                qty = min(sz * C.CAP_FRAC, room)
                if qty <= 1e-6:
                    continue
                wcost += qty * px
                n_fills += 1
                n_ct += qty
                pnl = (1 - px if won else -px) - fee(px)
                net += pnl * qty
                hit_windows.add(ticker)
                if won:
                    wins += qty
                else:
                    losses += qty
        cpc = (net / n_ct * 100) if n_ct else 0.0
        print(f"{cap:>5} {len(hit_windows):>12} {int(n_fills):>7} {n_ct:>10.1f} "
              f"{net:>+9.3f} {cpc:>+11.2f} {wins:>6.0f}/{losses:<5.0f}")
    print("\n(per-window notional cap ${:.0f}; cash constraint ignored — this is a "
          "capture what-if, not the live bankroll sim)".format(C.MAX_WINDOW_NOTIONAL))
    db.close()


if __name__ == "__main__":
    main()
