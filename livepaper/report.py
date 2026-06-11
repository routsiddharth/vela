"""Quick PnL / activity report.  python -m livepaper.report   (safe while running)"""
from __future__ import annotations
import sqlite3
from . import config as C


def main() -> None:
    db = sqlite3.connect(f"file:{C.DB_PATH}?mode=ro", uri=True)
    q = db.execute

    def one(sql):
        r = q(sql).fetchone()
        return r[0] if r and r[0] is not None else 0

    print(f"\n=== livepaper report  ({C.DB_PATH}) ===")
    print(f"price rows     : {one('SELECT COUNT(*) FROM prices'):>10}")
    print(f"book snapshots : {one('SELECT COUNT(*) FROM book_snaps'):>10}")
    print(f"estimates      : {one('SELECT COUNT(*) FROM estimates'):>10}")
    print(f"trades seen    : {one('SELECT COUNT(*) FROM trades'):>10}")
    print(f"paper fills    : {one('SELECT COUNT(*) FROM fills'):>10}")
    print(f"debias samples : {one('SELECT COUNT(*) FROM debias'):>10}")

    print("\nper-asset settled windows:")
    for r in q("SELECT asset, COUNT(*), SUM(CASE WHEN n_fills>0 THEN 1 ELSE 0 END), "
               "SUM(CASE WHEN n_fills>0 AND won=1 THEN 1 ELSE 0 END), "
               "SUM(CASE WHEN won=1 THEN 1 ELSE 0 END), SUM(net_pnl) FROM windows "
               "GROUP BY asset").fetchall():
        a, n, traded, twin, mok, net = (r[0] or "?", r[1] or 0, r[2] or 0,
                                        r[3] or 0, r[4] or 0, r[5] or 0.0)
        print(f"  {a:<4} windows={n:<4} traded={traded:<3} traded_wins={twin}/{traded:<3} "
              f"model_correct={mok}/{n:<4} net=${net:+.3f}")

    wins = q("SELECT COUNT(*), SUM(net_pnl), SUM(total_qty), SUM(fees), "
             "SUM(CASE WHEN n_fills>0 AND won=1 THEN 1 ELSE 0 END), "
             "SUM(CASE WHEN n_fills>0 AND won=0 THEN 1 ELSE 0 END), "
             "SUM(CASE WHEN n_fills>0 THEN 1 ELSE 0 END), "
             "SUM(CASE WHEN won=1 THEN 1 ELSE 0 END) FROM windows").fetchone()
    n_win, net, qty, fees, twin, tloss, traded, mok = (
        wins[0] or 0, wins[1] or 0.0, wins[2] or 0.0, wins[3] or 0.0,
        wins[4] or 0, wins[5] or 0, wins[6] or 0, wins[7] or 0)
    realized = net  # locked-in PnL
    open_cost = one("SELECT COALESCE(SUM(cost),0) FROM fills") - \
        one("SELECT COALESCE(SUM(total_qty*avg_px),0) FROM windows WHERE n_fills>0")
    # LIVE balance computed from the ledger — NOT windows.balance_after (which is a
    # stale per-window snapshot taken when other positions were still open).
    cash = C.BANKROLL + realized - open_cost
    print(f"\nsettled windows: {n_win}   (traded: {traded})")
    if traded:
        print(f"  traded W/L   : {twin}/{tloss}   (win% {100*twin/traded:.1f})")
        print(f"  model_correct: {mok}/{n_win}   (lock-gate accuracy)")
        print(f"  contracts    : {qty:.1f}   fees ${fees:.3f}")
        print(f"  net realized : ${net:+.3f}   ({100*net/qty:+.2f} c/contract)" if qty else "")
    print(f"  cash balance : ${cash:.2f}   (= ${C.BANKROLL:.0f} + ${realized:+.2f} realized "
          f"− ${open_cost:.2f} in open positions)")
    equity = cash + open_cost  # cost basis of open positions still counts as value
    if open_cost > 1e-9:
        print(f"  equity       : ${equity:.2f}   (cash + ${open_cost:.2f} cost basis of open trades)")

    print("\nlast 10 settled windows:")
    rows = q("SELECT ticker, asset, result, bet_side, gate_active, decision_margin_hat, "
             "n_fills, total_qty, avg_px, net_pnl, won, balance_after FROM windows "
             "ORDER BY close_ts DESC LIMIT 10").fetchall()
    for r in rows:
        tk, asset, res, bet, gate, mg, nf, qty, px, net, won, bal = r
        tag = "WIN " if won == 1 else ("LOSS" if won == 0 else "----")
        print(f"  {tk[-13:]:<13} {asset or '?':<3} res={res or '?':<3} bet={bet or '-':<3} "
              f"gate={'ON ' if gate else 'off'} m={(mg or 0):+7.1f} "
              f"fills={nf or 0:<3} qty={(qty or 0):>6.1f} @{(px or 0):.3f} "
              f"{tag} net=${(net or 0):+.3f} bal=${(bal or 0):.2f}")

    print("\nrecent fills:")
    for r in q("SELECT ticker, sec_to_close, bet_side, price, qty, margin_hat FROM fills "
               "ORDER BY ts_ms DESC LIMIT 8").fetchall():
        print(f"  {r[0][-14:]:<14} sec={r[1]:>4.0f} {r[2]:<3} @{r[3]:.3f} "
              f"qty={r[4]:>6.1f} m=${r[5]:+.0f}")
    print()
    db.close()


if __name__ == "__main__":
    main()
