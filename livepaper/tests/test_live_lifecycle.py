"""Offline lifecycle + risk-guard test for the LIVE executor (no network, no account).

Run:  python -m livepaper.tests.test_live_lifecycle    (from repo root)

Drives LiveExecutor against MockBroker through the full path a real window takes:
place -> partial fill -> cancel unfilled near close -> settle/reconcile, plus every
risk guard (kill file, daily-loss halt, open-notional cap). Asserts hard; prints a
PASS/FAIL summary and exits non-zero on any failure.
"""
from __future__ import annotations
import sys, tempfile
from pathlib import Path

from livepaper import config as C
from livepaper.broker import MockBroker, px_cents
from livepaper.live_exec import LiveExecutor

_checks = []
def check(name, cond):
    _checks.append((name, bool(cond)))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


class FakeBook:
    def __init__(self, yes_bid=None, no_bid=None):
        self._y, self._n = yes_bid, no_bid
    def best_yes_bid(self): return self._y
    def best_no_bid(self): return self._n


class FakeState:
    def __init__(self, ticker, bet_yes, yes_bid=None, no_bid=None, sec=45):
        self.ticker = ticker
        self.asset = "BTC"
        self.bet_yes = bet_yes
        self.book = FakeBook(yes_bid, no_bid)
        self.decision_margin = 12.3
        self.fills, self.total_qty, self.window_cost = [], 0, 0.0
        self._sec = sec
    def sec_to_close(self, now): return self._sec


class FakeStore:
    def __init__(self): self.orders, self.fills, self.events = [], [], []
    def order(self, *a): self.orders.append(a)
    def fill(self, *a): self.fills.append(a)
    def event(self, *a): self.events.append(a)


def main() -> int:
    C.SHARED_PORTFOLIO_DB = Path(tempfile.mkdtemp()) / "portfolio.db"

    print("== unit: px_cents conversion ==")
    check("0.59 -> 59c", px_cents(0.59) == 59)
    check("0.005 clamps to 1c", px_cents(0.005) == 1)
    check("1.5 clamps to 99c", px_cents(1.5) == 99)

    print("== lifecycle: place -> partial fill -> cancel -> settle ==")
    b = MockBroker(start_balance=100.0)
    st = FakeStore()
    state = FakeState("KXBTC15M-X", bet_yes=False, no_bid=0.59, sec=45)
    ex = LiveExecutor(b, st, lambda m: None, Path(tempfile.mkdtemp()))
    ex.attach({state.ticker: state})
    ex.startup_reconcile()
    check("startup uses shared risk balance", ex.balance == C.BANKROLL)
    check("startup still reads real broker balance", ex.real_balance == 100.0)

    ex.on_decision(state, 45)
    resting = b.resting_orders()
    target = C.PORTFOLIO_FRACTION * C.BANKROLL
    expect_ct = max(1, round(target / 0.59))   # round(5/0.59)=8
    check("one resting order placed", len(resting) == 1)
    check("side = no (bet_yes False)", resting and resting[0]["side"] == "no")
    check(f"count = {expect_ct} (10% risk balance / 0.59)",
          resting and resting[0]["count"] == expect_ct)
    check("price clamped within [floor,cap]",
          resting and C.LIVE_REST_FLOOR <= resting[0]["price"] <= C.LIVE_REST_CAP)
    oid = resting[0]["order_id"]

    # partial fill of 3 contracts
    b.fill_order(oid, 3, fee=0.01)
    ex.poll_and_manage(now=0.0)
    check("3 contracts folded into window state", state.total_qty == 3)
    check("window_cost = 3 * 0.59", abs(state.window_cost - 3 * 0.59) < 1e-9)
    check("fill recorded to store", len(st.fills) == 1)

    # re-poll must NOT double-count the same trade
    ex.poll_and_manage(now=0.0)
    check("no double-count on re-poll", state.total_qty == 3)

    # advance to close -> unfilled remainder cancelled
    state._sec = C.LIVE_CANCEL_BEFORE_CLOSE
    ex.poll_and_manage(now=0.0)
    check("unfilled remainder cancelled", len(b.resting_orders()) == 0)

    # settle a WIN: payout = qty*$1, update the shared risk balance
    payout = state.total_qty * 1.0
    b.credit(payout)
    net = payout - state.window_cost - 0.01
    bal_after = ex.note_settle(state.ticker, net)
    expected_bal = C.BANKROLL + net
    check("risk balance updated from live PnL", abs(bal_after - expected_bal) < 1e-9)
    check("real broker balance still refreshed separately",
          abs(ex.real_balance - (100.0 - 3 * 0.59 - 0.01 + payout)) < 1e-9)
    check("day_realized updated", abs(ex.day_realized - net) < 1e-9)

    print("== guard: open-notional cap blocks new orders ==")
    b2, st2 = MockBroker(100.0), FakeStore()
    ex2 = LiveExecutor(b2, st2, lambda m: None, Path(tempfile.mkdtemp()))
    states2 = {}
    placed = 0
    for i in range(20):
        s = FakeState(f"MKT{i}", bet_yes=True, yes_bid=0.50)
        states2[s.ticker] = s
        ex2.attach(states2)
        ex2.on_decision(s, 45)
        placed = len(b2.resting_orders())
    cap_notional = max(C.LIVE_MAX_OPEN_NOTIONAL,
                       C.LIVE_MAX_OPEN_FRACTION * ex2.portfolio.balance())
    per_order = round((C.PORTFOLIO_FRACTION * ex2.portfolio.balance()) / 0.50) * 0.50
    cap_ct = int(cap_notional // per_order)
    check(f"stopped placing at open-notional cap (~{cap_ct} orders, got {placed})",
          placed <= cap_ct + 1 and placed < 20)

    print("== guard: kill file halts + cancels all ==")
    tmp = Path(tempfile.mkdtemp())
    b3, st3 = MockBroker(100.0), FakeStore()
    ex3 = LiveExecutor(b3, st3, lambda m: None, tmp)
    s3 = FakeState("KILLME", bet_yes=True, yes_bid=0.60)
    ex3.attach({s3.ticker: s3})
    ex3.on_decision(s3, 45)
    check("order resting before kill", len(b3.resting_orders()) == 1)
    (tmp / C.LIVE_KILL_FILE).write_text("stop")
    ex3.poll_and_manage(now=0.0)
    check("kill file -> halted", ex3.halted)
    check("kill file -> all orders cancelled", len(b3.resting_orders()) == 0)
    s4 = FakeState("AFTERKILL", bet_yes=True, yes_bid=0.60)
    ex3.states[s4.ticker] = s4
    ex3.on_decision(s4, 45)
    check("no new orders while halted", len(b3.resting_orders()) == 0)

    print("== guard: daily-loss halt ==")
    b5, st5 = MockBroker(100.0), FakeStore()
    ex5 = LiveExecutor(b5, st5, lambda m: None, Path(tempfile.mkdtemp()))
    ex5.note_settle("L1", -(C.LIVE_MAX_DAILY_LOSS + 1))
    check("daily loss beyond limit -> halted", ex5.halted)

    n_pass = sum(1 for _, ok in _checks if ok)
    n_tot = len(_checks)
    print(f"\n{'='*48}\n{n_pass}/{n_tot} checks passed"
          f"{'  ✅ ALL GREEN' if n_pass == n_tot else '  ❌ FAILURES'}\n{'='*48}")
    return 0 if n_pass == n_tot else 1


if __name__ == "__main__":
    sys.exit(main())
