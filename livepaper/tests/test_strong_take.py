"""Offline test for the 'strong take' taker pathway (no network, no account).

Run:  python -m livepaper.tests.test_strong_take    (from repo root)

Drives LiveExecutor.consider_take against MockBroker: trigger threshold, time gate,
series gate, ONE-take-per-window, separation from the panic-fade book, fill routing
by order_id, WIN/LOSS settlement into the shared daily-loss halt, and the kill file.
Asserts hard; prints PASS/FAIL and exits non-zero on any failure.
"""
from __future__ import annotations
import sys, tempfile
from pathlib import Path

from livepaper import config as C
from livepaper.trading.broker import MockBroker
from livepaper.trading.live_exec import LiveExecutor

_checks = []
def check(name, cond):
    _checks.append((name, bool(cond)))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")


class FakeBook:
    def __init__(self, yes_ask=None, no_ask=None, yes_bid=None, no_bid=None):
        self._ya, self._na, self._yb, self._nb = yes_ask, no_ask, yes_bid, no_bid
    def yes_ask(self): return self._ya
    def no_ask(self): return self._na
    def best_yes_bid(self): return self._yb
    def best_no_bid(self): return self._nb


class FakeState:
    def __init__(self, ticker, series="KXBTC15M", yes_ask=None, no_ask=None, sec=40):
        self.ticker = ticker
        self.asset = "BTC"
        self.series = series
        self.bet_yes = True
        self.book = FakeBook(yes_ask=yes_ask, no_ask=no_ask)
        self.decision_margin = 0.0
        self.fills, self.total_qty, self.window_cost = [], 0, 0.0
        self._sec = sec
    def sec_to_close(self, now): return self._sec


class FakeStore:
    def __init__(self): self.orders, self.fills, self.events = [], [], []
    def order(self, *a, **k): self.orders.append((a, k))
    def fill(self, *a, **k): self.fills.append((a, k))
    def event(self, *a, **k): self.events.append((a, k))


def _exec(broker, store, tmp=None):
    C.SHARED_PORTFOLIO_DB = Path(tempfile.mkdtemp()) / "portfolio.db"
    return LiveExecutor(broker, store, lambda m: None, tmp or Path(tempfile.mkdtemp()))


def main() -> int:
    C.STRONG_TAKE = True   # enable the pathway for this test (env-gated in prod)

    print("== trigger: yes ask >= 0.95 -> one taker buy on yes ==")
    b, st = MockBroker(100.0), FakeStore()
    ex = _exec(b, st)
    s = FakeState("KXBTC15M-A", yes_ask=0.96, no_ask=0.05, sec=40)
    ex.attach({s.ticker: s})
    ex.consider_take(s, 40, 0.0)
    resting = b.resting_orders()
    target = C.PORTFOLIO_FRACTION * ex.portfolio.balance()
    expect_ct = max(1, round(target / 0.96))
    check("one taker order placed", len(resting) == 1)
    check("side = yes", resting and resting[0]["side"] == "yes")
    check("price = ask 0.96", resting and abs(resting[0]["price"] - 0.96) < 1e-9)
    check(f"count = {expect_ct} (10% risk balance / 0.96)",
          resting and resting[0]["count"] == expect_ct)
    check("tagged strong095 in store", any(k.get("detail") == "strong095"
                                           for _, k in st.orders))

    print("== one-take-per-window: second call does nothing ==")
    ex.consider_take(s, 39, 0.0)
    check("still only one order", len(b.resting_orders()) == 1)

    print("== separation: fill routes to strong book, NOT MarketState ==")
    oid = resting[0]["order_id"]
    b.fill_order(oid, expect_ct, fee=0.01)
    ex.poll_and_manage(now=0.0)
    check("strong book has the qty", ex.strong_fills[s.ticker]["qty"] == expect_ct)
    check("MarketState untouched (total_qty==0)", s.total_qty == 0)
    check("no double-count on re-poll",
          (ex.poll_and_manage(now=0.0), ex.strong_fills[s.ticker]["qty"])[1] == expect_ct)

    print("== settle WIN: payout folds into day_realized ==")
    before = ex.day_realized
    ex.settle_strong(s.ticker, "yes", 0.0)
    net_win = expect_ct * (1 - 0.96) - 0.01
    check("WIN net added to day_realized", abs(ex.day_realized - before - net_win) < 1e-9)
    check("idempotent settle (no re-add)",
          (ex.settle_strong(s.ticker, "yes", 0.0), abs(ex.day_realized - before - net_win) < 1e-9)[1])

    print("== settle LOSS: full stake lost ==")
    b2, st2 = MockBroker(100.0), FakeStore()
    ex2 = _exec(b2, st2)
    s2 = FakeState("KXBTC15M-B", no_ask=0.97, yes_ask=0.03, sec=30)
    ex2.attach({s2.ticker: s2})
    ex2.consider_take(s2, 30, 0.0)
    o2 = b2.resting_orders()[0]
    ct2 = o2["count"]
    b2.fill_order(o2["order_id"], ct2, fee=0.01)
    ex2.poll_and_manage(now=0.0)
    ex2.settle_strong(s2.ticker, "yes", 0.0)    # took NO, settled YES -> loss
    check("LOSS net = -cost-fee", abs(ex2.day_realized - (-ct2 * 0.97 - 0.01)) < 1e-9)

    print("== gates: below threshold / wrong series / outside time window ==")
    b3, st3 = MockBroker(100.0), FakeStore()
    ex3 = _exec(b3, st3)
    lo = FakeState("KXBTC15M-C", yes_ask=0.94, sec=40);  ex3.attach({lo.ticker: lo})
    ex3.consider_take(lo, 40, 0.0)
    check("ask 0.94 < 0.95 -> no order", len(b3.resting_orders()) == 0)
    early = FakeState("KXBTC15M-D", yes_ask=0.98, sec=50); ex3.states[early.ticker] = early
    ex3.consider_take(early, 50, 0.0)
    check("sec 50 (>=45) -> no order", len(b3.resting_orders()) == 0)
    hourly = FakeState("KXBTCD-E", series="KXBTCD", yes_ask=0.98, sec=30)
    ex3.states[hourly.ticker] = hourly
    ex3.consider_take(hourly, 30, 0.0)
    check("KXBTCD not in STRONG_SERIES -> no order", len(b3.resting_orders()) == 0)

    print("== max px: never pay above STRONG_MAX_PX ==")
    b4, st4 = MockBroker(100.0), FakeStore()
    ex4 = _exec(b4, st4)
    hi = FakeState("KXBTC15M-F", yes_ask=1.0, sec=20)   # ask pinned at 1.00
    ex4.attach({hi.ticker: hi})
    ex4.consider_take(hi, 20, 0.0)
    r4 = b4.resting_orders()
    check("price capped at STRONG_MAX_PX",
          r4 and abs(r4[0]["price"] - C.STRONG_MAX_PX) < 1e-9)

    print("== kill file halts the strong pathway too ==")
    tmp = Path(tempfile.mkdtemp())
    b5, st5 = MockBroker(100.0), FakeStore()
    ex5 = _exec(b5, st5, tmp)
    (tmp / C.LIVE_KILL_FILE).write_text("stop")
    ex5.poll_and_manage(now=0.0)
    k = FakeState("KXBTC15M-G", yes_ask=0.98, sec=30); ex5.states[k.ticker] = k
    ex5.consider_take(k, 30, 0.0)
    check("no strong order while killed", len(b5.resting_orders()) == 0)

    n_pass = sum(1 for _, ok in _checks if ok)
    n_tot = len(_checks)
    print(f"\n{'='*48}\n{n_pass}/{n_tot} checks passed"
          f"{'  GREEN' if n_pass == n_tot else '  FAILURES'}\n{'='*48}")
    return 0 if n_pass == n_tot else 1


if __name__ == "__main__":
    sys.exit(main())
