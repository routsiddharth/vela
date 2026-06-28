"""LiveExecutor — turns the paper engine's decisions into REAL Kalshi maker orders.

Lifecycle per window (BTC only, when VELA_LIVE=1):
  decision (gate ON, ~45s)  -> rest ONE post-only limit buy on the favored side,
                               10% of the shared live risk ledger, price =
                               favored best bid clamped to [floor, cap]
                               (be the maker a panic seller hits)
  each tick                 -> poll real fills, fold them into the window state so
                               the engine's normal settlement/accounting works
  sec_to_close <= 2         -> cancel any unfilled remainder
  settle                    -> realized PnL from real fills; update the shared
                               risk ledger

Safety: post_only (maker-only, never crosses), shared-ledger sizing, a kill-switch
file, a daily-loss halt, an open-notional cap, startup reconciliation, and
cancel-all on shutdown. Nothing here runs unless config.LIVE is true.
"""
from __future__ import annotations
import math
from pathlib import Path

from .. import config as C
from .broker import new_client_order_id
from .portfolio import SharedPortfolio


def _maker_fee(qty: int, p: float) -> float:
    return math.ceil(C.MAKER_FEE_RATE * qty * p * (1.0 - p) * 100) / 100.0


def _taker_fee(qty: int, p: float) -> float:
    return math.ceil(C.STRONG_TAKER_FEE_RATE * qty * p * (1.0 - p) * 100) / 100.0


class LiveExecutor:
    def __init__(self, broker, store, log, data_dir: Path) -> None:
        self.b = broker
        self.store = store
        self.log = log
        self.kill_path = Path(data_dir) / C.LIVE_KILL_FILE
        self.states: dict = {}          # ticker -> MarketState (set via attach)
        self.orders: dict = {}          # ticker -> panic-fade order record
        # ---- 'strong take' pathway (separate book; see config.STRONG_TAKE) -----
        self.strong_orders: dict = {}   # ticker -> taker order record
        self.strong_fills: dict = {}    # ticker -> {side,qty,cost,fee,settled} (own book)
        self._seen_trades: set = set()  # dedup fills across polls (shared)
        self.halted = False
        self.day_realized = 0.0         # fade + strong combined (drives the shared halt)
        self.balance = None
        self.real_balance = None
        self.portfolio = SharedPortfolio(C.SHARED_PORTFOLIO_DB, C.BANKROLL, log=log)

    def attach(self, states: dict) -> None:
        self.states = states

    # ---- startup / shutdown ----
    def startup_reconcile(self) -> None:
        try:
            self.b.cancel_all()
            self.real_balance = self.b.balance_dollars()
            self.balance = self.portfolio.balance()
            self.log(f"[live] startup real_balance=${self.real_balance:.2f} "
                     f"risk_balance=${self.balance:.2f} "
                     f"(demo={C.LIVE_DEMO}, size={C.PORTFOLIO_FRACTION:.0%}/trade)")
        except Exception as e:
            self.log(f"[live] startup reconcile error: {e}")
            self.store.event("live_startup_err", str(e)[:200])

    def shutdown(self) -> None:
        try:
            self.b.cancel_all()
        except Exception:
            pass
        self.portfolio.close()

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
        """Combined open exposure across BOTH pathways (shared cap)."""
        return sum(r["count"] * r["price"]
                   for book in (self.orders, self.strong_orders)
                   for r in book.values() if not r.get("done"))

    def _risk_balance(self) -> float:
        self.balance = self.portfolio.balance()
        return max(0.0, self.balance)

    def _target_notional(self) -> float:
        return C.PORTFOLIO_FRACTION * self._risk_balance()

    def _max_open_notional(self) -> float:
        return max(C.LIVE_MAX_OPEN_NOTIONAL,
                   C.LIVE_MAX_OPEN_FRACTION * self._risk_balance())

    def _count_for_price(self, px: float, target_notional: float) -> int:
        if px <= 0 or target_notional <= 0:
            return 0
        return max(1, round(target_notional / px))

    # ---- ALT pathway: taker-take a near-certain favorite (config.STRONG_TAKE) --
    def consider_take(self, s, sec: float, now: float) -> None:
        """Independent of the panic-fade. Once per window, on STRONG_SERIES only:
        if a side's ask >= threshold within the time window, cross and buy it."""
        if not C.STRONG_TAKE or self.killed():
            return
        if s.series not in C.STRONG_SERIES or s.ticker in self.strong_orders:
            return
        if not (C.STRONG_TAKE_SEC_LO <= sec < C.STRONG_TAKE_SEC_HI):
            return
        ya, na = s.book.yes_ask(), s.book.no_ask()
        side = px = None
        if ya is not None and ya >= C.STRONG_TAKE_THRESH:
            side, px = "yes", min(C.STRONG_MAX_PX, ya)
        elif na is not None and na >= C.STRONG_TAKE_THRESH:
            side, px = "no", min(C.STRONG_MAX_PX, na)
        if side is None:
            return
        target = self._target_notional()
        count = self._count_for_price(px, target)
        if count <= 0:
            self.store.event("strong_skip", f"{s.ticker} zero risk balance")
            return
        order_notional = count * px
        if self._open_notional() + order_notional > self._max_open_notional():
            self.store.event("strong_skip",
                             f"{s.ticker} open-notional cap target={target:.2f}")
            return
        coid = new_client_order_id()
        try:
            o = self.b.place_taker_buy(s.ticker, side, px, count, coid)
        except Exception as e:
            self.store.event("strong_order_err", f"{s.ticker} take: {e}")
            return
        oid = o.get("order_id")
        self.strong_orders[s.ticker] = {"coid": coid, "oid": oid, "side": side,
                                        "price": px, "count": count, "filled": 0,
                                        "canceled": False, "done": False}
        self.store.order(s.ticker, "place", coid, oid, side, px, count,
                         o.get("status", "taker"), detail="strong095")

    # ---- per-window: place the resting bid at decision ----
    def on_decision(self, s, sec: float) -> None:
        if self.killed() or s.ticker in self.orders:
            return
        side = "yes" if s.bet_yes else "no"
        bid = s.book.best_yes_bid() if side == "yes" else s.book.best_no_bid()
        if bid is None:
            self.store.event("live_skip", f"{s.ticker} no {side} bid to join")
            return
        px = min(C.LIVE_REST_CAP, max(C.LIVE_REST_FLOOR, bid))
        target = self._target_notional()
        count = self._count_for_price(px, target)
        if count <= 0:
            self.store.event("live_skip", f"{s.ticker} zero risk balance")
            return
        order_notional = count * px
        if self._open_notional() + order_notional > self._max_open_notional():
            self.store.event("live_skip",
                             f"{s.ticker} open-notional cap target={target:.2f}")
            return
        coid = new_client_order_id()
        try:
            o = self.b.place_limit_buy(s.ticker, side, px, count, coid)
        except Exception as e:
            self.store.event("live_order_err", f"{s.ticker} place: {e}")
            return
        oid = o.get("order_id")
        self.orders[s.ticker] = {"coid": coid, "oid": oid, "side": side, "price": px,
                                 "count": count, "filled": 0, "canceled": False,
                                 "done": False}
        self.store.order(s.ticker, "place", coid, oid, side, px, count,
                         o.get("status", "resting"))

    # ---- once per tick: kill check, poll fills, cancel near close ----
    def poll_and_manage(self, now: float) -> None:
        if self.kill_path.exists():
            self._halt("kill file present")
            return
        if not self.orders and not self.strong_orders:
            return
        # route fills by order_id: both pathways may have an order on the SAME
        # ticker, so ticker alone is ambiguous. oid -> ("fade"|"strong", tk, rec).
        oid_map = {}
        for tk, r in self.orders.items():
            if r.get("oid"):
                oid_map[r["oid"]] = ("fade", tk, r)
        for tk, r in self.strong_orders.items():
            if r.get("oid"):
                oid_map[r["oid"]] = ("strong", tk, r)
        # 1) poll real fills, fold into the matching book
        try:
            fills = self.b.fills()
        except Exception as e:
            self.store.event("live_fill_poll_err", str(e)[:200])
            fills = []
        for f in fills:
            # Kalshi fill fields: fill_id/trade_id, order_id, market_ticker/ticker,
            # count_fp (str), {yes,no}_price_dollars (str), fee_cost (str).
            tid = f.get("trade_id") or f.get("fill_id")
            if not tid or tid in self._seen_trades:
                continue
            match = oid_map.get(f.get("order_id"))
            if match is None:                      # fallback: legacy ticker match (fade only)
                tk = f.get("ticker") or f.get("market_ticker")
                rec = self.orders.get(tk)
                if rec is None:
                    continue
                match = ("fade", tk, rec)
            kind, tk, rec = match
            self._seen_trades.add(tid)
            cnt = round(float(f.get("count_fp") or f.get("count") or 0))
            if cnt <= 0:
                continue
            side = rec["side"]
            px = f.get(f"{side}_price_dollars")
            px = float(px) if px is not None else float(f.get("price", rec["price"]))
            fee = f.get("fee_cost")
            s = self.states.get(tk)
            rec["filled"] += cnt
            if kind == "strong":
                fee = float(fee) if fee is not None else _taker_fee(cnt, px)
                bk = self.strong_fills.setdefault(
                    tk, {"side": side, "qty": 0, "cost": 0.0, "fee": 0.0, "settled": False})
                bk["qty"] += cnt
                bk["cost"] += cnt * px
                bk["fee"] += fee
                sec = s.sec_to_close(now) if s is not None else 0.0
                self.store.fill(tk, sec, side, px, cnt, fee, cnt * px, None,
                                "strong095 taker")
                self.log(f"[strong] FILL {cnt}@{px:.2f} {tk} ({rec['filled']}/{rec['count']})")
            else:
                fee = float(fee) if fee is not None else _maker_fee(cnt, px)
                if s is not None:
                    s.fills.append((px, cnt, fee))
                    s.total_qty += cnt
                    s.window_cost += cnt * px
                    self.store.fill(tk, s.sec_to_close(now), side, px, cnt, fee,
                                    cnt * px, s.decision_margin, "live maker fill")
                self.log(f"[live] FILL {cnt}@{px:.2f} {tk} ({rec['filled']}/{rec['count']})")
        # 2) cancel any unfilled remainder near close (BOTH pathways)
        for book in (self.orders, self.strong_orders):
            for tk, rec in book.items():
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

    # ---- settlement hooks ----
    def note_settle(self, ticker: str, net: float) -> float:
        """Called by engine._settle. Updates day PnL + reconciles real balance."""
        self.day_realized += net
        if self.orders.get(ticker):
            self.orders[ticker]["done"] = True
        s = self.states.get(ticker)
        self.balance = self.portfolio.apply_settlement(
            f"fade:{ticker}", ticker, "fade", getattr(s, "asset", None), net)
        if self.day_realized <= -C.LIVE_MAX_DAILY_LOSS:
            self._halt(f"daily loss {self.day_realized:+.2f} <= -{C.LIVE_MAX_DAILY_LOSS}")
        try:
            self.real_balance = self.b.balance_dollars()
        except Exception:
            pass
        return self.balance if self.balance is not None else 0.0

    def settle_strong(self, ticker: str, result: str, now: float) -> None:
        """Settle the strong (taker) book for one window from the REAL result.
        Folds its realized PnL into the shared daily-loss halt, logs + stores it.
        No-op if the strong pathway never took this window."""
        rec = self.strong_orders.get(ticker)
        if rec:
            rec["done"] = True
        bk = self.strong_fills.get(ticker)
        if not bk or bk["qty"] <= 0 or bk.get("settled"):
            return
        bk["settled"] = True
        side, qty, cost, fee = bk["side"], bk["qty"], bk["cost"], bk["fee"]
        won = (result == side)
        net = (qty * 1.0 if won else 0.0) - cost - fee
        avg = cost / qty if qty else 0.0
        self.day_realized += net
        self.store.event("strong_settle",
            f"{ticker} {result} {'WIN' if won else 'LOSS'} side={side} qty={qty} "
            f"avg={avg:.3f} fee={fee:.3f} net={net:+.3f}")
        self.log(f"[strong] SETTLED {ticker} {result.upper()} "
                 f"{'WIN' if won else 'LOSS'} side={side} qty={qty} avg={avg:.2f} "
                 f"net=${net:+.3f}")
        if self.day_realized <= -C.LIVE_MAX_DAILY_LOSS:
            self._halt(f"daily loss {self.day_realized:+.2f} <= -{C.LIVE_MAX_DAILY_LOSS}")
        s = self.states.get(ticker)
        self.balance = self.portfolio.apply_settlement(
            f"strong:{ticker}", ticker, "strong", getattr(s, "asset", None), net)
        try:
            self.real_balance = self.b.balance_dollars()
        except Exception:
            pass
