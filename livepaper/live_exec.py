"""LiveExecutor — turns the paper engine's decisions into REAL Kalshi maker orders.

Lifecycle per window (BTC only, when VELA_LIVE=1):
  decision (gate ON, ~45s)  -> rest ONE post-only limit buy on the favored side,
                               $5 notional, price = favored best bid clamped to
                               [floor, cap]  (be the maker a panic seller hits)
  each tick                 -> poll real fills, fold them into the window state so
                               the engine's normal settlement/accounting works
  sec_to_close <= 2         -> cancel any unfilled remainder
  settle                    -> realized PnL from real fills; reconcile cash from the
                               real account balance

Safety: post_only (maker-only, never crosses), fixed $5/window, a kill-switch file,
a daily-loss halt, an open-notional cap, startup reconciliation, and cancel-all on
shutdown. Nothing here runs unless config.LIVE is true.
"""
from __future__ import annotations
import math
from pathlib import Path

from . import config as C
from .broker import new_client_order_id


def _maker_fee(qty: int, p: float) -> float:
    return math.ceil(C.MAKER_FEE_RATE * qty * p * (1.0 - p) * 100) / 100.0


class LiveExecutor:
    def __init__(self, broker, store, log, data_dir: Path) -> None:
        self.b = broker
        self.store = store
        self.log = log
        self.kill_path = Path(data_dir) / C.LIVE_KILL_FILE
        self.states: dict = {}          # ticker -> MarketState (set via attach)
        self.orders: dict = {}          # ticker -> order record
        self._seen_trades: set = set()  # dedup fills across polls
        self.halted = False
        self.day_realized = 0.0
        self.balance = None

    def attach(self, states: dict) -> None:
        self.states = states

    # ---- startup / shutdown ----
    def startup_reconcile(self) -> None:
        try:
            n = self.b.cancel_all()
            if n:
                self.log(f"[live] startup: canceled {n} stray resting order(s)")
            self.balance = self.b.balance_dollars()
            self.log(f"[live] startup balance ${self.balance:.2f}  "
                     f"(demo={C.LIVE_DEMO}, $/window={C.POSITION_USD})")
        except Exception as e:
            self.log(f"[live] startup reconcile error: {e}")
            self.store.event("live_startup_err", str(e)[:200])

    def shutdown(self) -> None:
        try:
            n = self.b.cancel_all()
            self.log(f"[live] shutdown: canceled {n} resting order(s)")
        except Exception as e:
            self.log(f"[live] shutdown cancel error: {e}")

    # ---- kill / halt ----
    def killed(self) -> bool:
        return self.halted or self.kill_path.exists()

    def _halt(self, why: str) -> None:
        if self.halted:
            return
        self.halted = True
        self.store.event("live_halt", why)
        self.log(f"[live] *** HALTED: {why} *** cancelling all orders")
        try:
            self.b.cancel_all()
        except Exception:
            pass

    def _open_notional(self) -> float:
        return sum(r["count"] * r["price"] for r in self.orders.values()
                   if not r.get("done"))

    # ---- per-window: place the resting bid at decision ----
    def on_decision(self, s, sec: float) -> None:
        if self.killed() or s.ticker in self.orders:
            return
        if self._open_notional() + C.POSITION_USD > C.LIVE_MAX_OPEN_NOTIONAL:
            self.store.event("live_skip", f"{s.ticker} open-notional cap")
            return
        side = "yes" if s.bet_yes else "no"
        bid = s.book.best_yes_bid() if side == "yes" else s.book.best_no_bid()
        if bid is None:
            self.store.event("live_skip", f"{s.ticker} no {side} bid to join")
            return
        px = min(C.LIVE_REST_CAP, max(C.LIVE_REST_FLOOR, bid))
        count = max(1, round(C.POSITION_USD / px))
        coid = new_client_order_id()
        try:
            o = self.b.place_limit_buy(s.ticker, side, px, count, coid)
        except Exception as e:
            self.store.event("live_order_err", f"{s.ticker} place: {e}")
            self.log(f"[live] place FAILED {s.ticker}: {e}")
            return
        oid = o.get("order_id")
        self.orders[s.ticker] = {"coid": coid, "oid": oid, "side": side, "price": px,
                                 "count": count, "filled": 0, "canceled": False,
                                 "done": False}
        self.store.order(s.ticker, "place", coid, oid, side, px, count,
                         o.get("status", "resting"))
        self.log(f"[live] REST buy {side} {count}@{px:.2f} {s.ticker} oid={oid}")

    # ---- once per tick: kill check, poll fills, cancel near close ----
    def poll_and_manage(self, now: float) -> None:
        if self.kill_path.exists():
            self._halt("kill file present")
            return
        if not self.orders:
            return
        # 1) poll real fills, fold into the matching window state
        try:
            fills = self.b.fills()
        except Exception as e:
            self.store.event("live_fill_poll_err", str(e)[:200])
            fills = []
        for f in fills:
            tid = f.get("trade_id")
            tk = f.get("ticker")
            rec = self.orders.get(tk)
            if not tid or tid in self._seen_trades or rec is None:
                continue
            self._seen_trades.add(tid)
            cnt = int(f.get("count", 0))
            if cnt <= 0:
                continue
            px = f.get("price", rec["price"])
            fee = f.get("fee")
            fee = _maker_fee(cnt, px) if fee is None else fee
            s = self.states.get(tk)
            if s is not None:
                s.fills.append((px, cnt, fee))
                s.total_qty += cnt
                s.window_cost += cnt * px
                self.store.fill(tk, s.sec_to_close(now), rec["side"], px, cnt, fee,
                                cnt * px, s.decision_margin, "live maker fill")
            rec["filled"] += cnt
            self.log(f"[live] FILL {cnt}@{px:.2f} {tk} ({rec['filled']}/{rec['count']})")
        # 2) cancel any unfilled remainder near close
        for tk, rec in self.orders.items():
            s = self.states.get(tk)
            if s is None or rec["canceled"] or rec["oid"] is None:
                continue
            if s.sec_to_close(now) <= C.LIVE_CANCEL_BEFORE_CLOSE \
                    and rec["filled"] < rec["count"]:
                try:
                    self.b.cancel(rec["oid"])
                except Exception:
                    pass
                rec["canceled"] = True
                self.store.order(tk, "cancel", rec["coid"], rec["oid"], rec["side"],
                                 rec["price"], rec["count"] - rec["filled"], "canceled")
                self.log(f"[live] CANCEL unfilled {rec['count']-rec['filled']} {tk}")

    # ---- settlement hooks ----
    def note_settle(self, ticker: str, net: float) -> float:
        """Called by engine._settle. Updates day PnL + reconciles real balance."""
        self.day_realized += net
        if self.orders.get(ticker):
            self.orders[ticker]["done"] = True
        if self.day_realized <= -C.LIVE_MAX_DAILY_LOSS:
            self._halt(f"daily loss {self.day_realized:+.2f} <= -{C.LIVE_MAX_DAILY_LOSS}")
        try:
            self.balance = self.b.balance_dollars()
        except Exception:
            pass
        return self.balance if self.balance is not None else 0.0
