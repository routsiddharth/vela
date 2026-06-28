"""Order book + per-market position state for the Trading service.

OrderBook is a bids-only book (yes_ask = 1 - best_no_bid); MarketState carries one
window's decision + paper accounting. Relocated verbatim from the old market.py.
"""
from __future__ import annotations


class OrderBook:
    """Bids-only book. `yes`/`no` map price$ -> size. yes_ask = 1 - best_no_bid."""
    def __init__(self) -> None:
        self.yes: dict[float, float] = {}
        self.no: dict[float, float] = {}

    def snapshot(self, msg: dict) -> None:
        self.yes, self.no = {}, {}
        for p, s in _levels(msg.get("yes_dollars_fp") or msg.get("yes")):
            self.yes[p] = s
        for p, s in _levels(msg.get("no_dollars_fp") or msg.get("no")):
            self.no[p] = s

    def delta(self, msg: dict) -> None:
        side = self.yes if msg.get("side") == "yes" else self.no
        p = _f(msg.get("price_dollars") or msg.get("price"))
        d = _f(msg.get("delta_fp") if msg.get("delta_fp") is not None else msg.get("delta"))
        if p is None or d is None:
            return
        side[p] = side.get(p, 0.0) + d
        if side[p] <= 1e-9:
            side.pop(p, None)

    def best_yes_bid(self):
        return max(self.yes) if self.yes else None

    def best_no_bid(self):
        return max(self.no) if self.no else None

    def yes_ask(self):
        b = self.best_no_bid()
        return None if b is None else round(1.0 - b, 4)

    def no_ask(self):
        b = self.best_yes_bid()
        return None if b is None else round(1.0 - b, 4)

    def winning_ask(self, bet_yes: bool):
        """(price_to_buy_winning_side, size_available)."""
        if bet_yes:
            b = self.best_no_bid()
            return (None, 0.0) if b is None else (round(1.0 - b, 4), self.no.get(b, 0.0))
        b = self.best_yes_bid()
        return (None, 0.0) if b is None else (round(1.0 - b, 4), self.yes.get(b, 0.0))

    def compact(self, depth: int = 6) -> dict:
        ys = sorted(self.yes.items(), key=lambda x: -x[0])[:depth]
        ns = sorted(self.no.items(), key=lambda x: -x[0])[:depth]
        return {"yes": ys, "no": ns}


def _f(v):
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _levels(raw):
    out = []
    for pair in raw or []:
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            p, s = _f(pair[0]), _f(pair[1])
            if p is not None and s is not None:
                out.append((p, s))
    return out


class MarketState:
    def __init__(self, ticker, close_ts, strike, asset, symbol, series) -> None:
        self.ticker = ticker
        self.close_ts = close_ts
        self.strike = strike
        self.asset = asset           # "BTC" / "ETH" -> picks the feed + de-bias
        self.symbol = symbol         # Binance symbol, e.g. "BTCUSDT"
        self.series = series         # Kalshi series, e.g. "KXBTC15M"
        self.book = OrderBook()
        self.have_book = False
        # decision (locked once at sec_to_close <= TAU_DECISION)
        self.decided = False
        self.bet_yes: bool | None = None
        self.decision_margin: float | None = None
        self.sd_S: float = 0.0           # model settlement-estimate std at decision
        self.p_side: float = 0.0         # model P(our side wins) at decision
        self.gate_active = False
        # paper accounting
        self.window_cost = 0.0
        self.total_qty = 0.0
        self.budget_ct: int | None = None   # target contracts (10% of portfolio), set at 1st fill
        self.fills: list[tuple] = []     # (price, qty, fee)
        self.settled = False

    def sec_to_close(self, now: float) -> float:
        return self.close_ts - now
