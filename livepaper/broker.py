"""Kalshi order brokers for LIVE trading.

Two implementations behind one interface so the executor is identical in test and
prod:

  LiveBroker  — signed REST against Kalshi (prod or demo). Places/cancels REAL
                limit orders, reads REAL balance/positions/fills.
  MockBroker  — in-memory, deterministic. Lets the full order lifecycle
                (place -> fill -> cancel -> reconcile) be tested offline with no
                network and no account.

Prices: this codebase works in DOLLARS (0.01..0.99). Kalshi's API works in integer
CENTS (1..99). Conversion happens ONLY at this boundary (px_cents below); everything
above this layer stays in dollars.

Kalshi REST auth reuses the repo's proven RSA-PSS scheme (sign ts+METHOD+path; the
body is NOT signed). Order placement is `post_only` so we can only ever be the
MAKER — an order that would cross the book is rejected rather than paying taker fees.
"""
from __future__ import annotations
import base64, os, time, uuid
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

PROD_BASE = "https://api.elections.kalshi.com/trade-api/v2"
DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"

# v2 create-order endpoint. The legacy POST /portfolio/orders (action+side(yes|no)+
# {yes,no}_price in cents) was deprecated 2026-06 -> HTTP 410 deprecated_v1_order_endpoint.
# v2 quotes EVERYTHING from the YES leg: side is bid|ask, price is a fixed-point DOLLAR
# string always in YES terms, count is a string. buy YES == bid at yes_price; buy NO ==
# ask at (1 - no_price) (selling YES is economically buying NO).
ORDERS_PATH_V2 = "/portfolio/events/orders"


def px_cents(price_dollars: float) -> int:
    """Dollars (0.01..0.99) -> Kalshi integer cents (1..99), clamped."""
    return max(1, min(99, round(price_dollars * 100)))


def _yes_price_dollars(price_dollars: float) -> str:
    """Clamp to a valid 1c..99c tick and format as a fixed-point dollar string."""
    p = max(0.01, min(0.99, round(price_dollars, 2)))
    return f"{p:.2f}"


def _yes_terms(side: str, price_dollars: float) -> tuple[str, str]:
    """Map our (side, side-price) onto the v2 YES-quoted book.
    side='yes' -> ('bid', yes_price); side='no' -> ('ask', 1 - no_price)."""
    if side == "yes":
        return "bid", _yes_price_dollars(price_dollars)
    return "ask", _yes_price_dollars(1.0 - price_dollars)


def new_client_order_id() -> str:
    return f"vela-{uuid.uuid4()}"


class BrokerError(RuntimeError):
    pass


class LiveBroker:
    """Signed REST client that can place/cancel orders and read the account.

    base: PROD_BASE or DEMO_BASE. Pass demo=True to force the demo cluster.
    """
    def __init__(self, base: str | None = None, demo: bool = False) -> None:
        self.base = (DEMO_BASE if demo else (base or PROD_BASE)).rstrip("/")
        self.key_id = os.environ["KALSHI_API_KEY"].strip()
        raw = os.environ["KALSHI_API_SECRET"]
        if "-----BEGIN" not in raw:
            raise BrokerError("KALSHI_API_SECRET is not a PEM private key")
        self.key = serialization.load_pem_private_key(
            raw.replace("\\n", "\n").encode(), password=None)
        self.c = httpx.Client(timeout=15.0)

    # ---- signing / transport ----
    def _headers(self, method: str, path: str) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        sig = self.key.sign(f"{ts}{method.upper()}{path}".encode(),
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                        salt_length=padding.PSS.DIGEST_LENGTH),
                            hashes.SHA256())
        return {"KALSHI-ACCESS-KEY": self.key_id,
                "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
                "Content-Type": "application/json"}

    def _req(self, method: str, path: str, *, params=None, body=None) -> dict:
        from urllib.parse import urlsplit
        url = f"{self.base}{path}"
        sign_path = urlsplit(url).path
        last = None
        for attempt in range(5):
            h = self._headers(method, sign_path)
            r = self.c.request(method, url, params=params, json=body, headers=h)
            if r.status_code == 429 or r.status_code >= 500:
                last = r
                time.sleep(0.4 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise BrokerError(f"{method} {path} -> {r.status_code} {r.text[:300]}")
            return r.json() if r.text else {}
        raise BrokerError(f"{method} {path} exhausted retries; last={getattr(last,'status_code',None)}")

    # ---- account ----
    def balance_dollars(self) -> float:
        return self._req("GET", "/portfolio/balance").get("balance", 0) / 100.0

    def positions(self, ticker: str | None = None) -> list[dict]:
        p = {"ticker": ticker} if ticker else None
        return self._req("GET", "/portfolio/positions", params=p).get("market_positions", [])

    def resting_orders(self, ticker: str | None = None) -> list[dict]:
        p = {"status": "resting"}
        if ticker:
            p["ticker"] = ticker
        return self._req("GET", "/portfolio/orders", params=p).get("orders", [])

    def fills(self, ticker: str | None = None, min_ts: int | None = None) -> list[dict]:
        p: dict[str, Any] = {}
        if ticker:
            p["ticker"] = ticker
        if min_ts:
            p["min_ts"] = min_ts
        return self._req("GET", "/portfolio/fills", params=p).get("fills", [])

    # ---- order actions (v2 create endpoint; post_only -> maker only) ----
    def place_limit_buy(self, ticker: str, side: str, price_dollars: float,
                        count: int, client_order_id: str) -> dict:
        """Rest a MAKER limit buy. side='yes'|'no'. Returns the order dict."""
        v2_side, yes_px = _yes_terms(side, price_dollars)
        body = {
            "ticker": ticker,
            "client_order_id": client_order_id,
            "side": v2_side,                          # bid=buy YES, ask=buy NO
            "count": str(int(count)),
            "price": yes_px,                          # fixed-point dollars, YES terms
            "time_in_force": "good_till_canceled",
            "post_only": True,                        # reject if it would cross -> maker only
            "self_trade_prevention_type": "maker",
        }
        r = self._req("POST", ORDERS_PATH_V2, body=body)
        return r.get("order", r)                      # v2 returns order fields at top level

    def place_taker_buy(self, ticker: str, side: str, price_dollars: float,
                        count: int, client_order_id: str) -> dict:
        """Cross the spread: an immediate-or-cancel limit buy that TAKES liquidity up
        to price_dollars and pays the taker fee. side='yes'|'no'. Used by the 'strong
        take' pathway; the panic-fade still uses the post_only place_limit_buy above."""
        v2_side, yes_px = _yes_terms(side, price_dollars)
        body = {
            "ticker": ticker,
            "client_order_id": client_order_id,
            "side": v2_side,
            "count": str(int(count)),
            "price": yes_px,                          # limit price; IOC -> may cross then cancel
            "time_in_force": "immediate_or_cancel",
            "self_trade_prevention_type": "taker_at_cross",
        }
        r = self._req("POST", ORDERS_PATH_V2, body=body)
        return r.get("order", r)

    def cancel(self, order_id: str) -> dict:
        return self._req("DELETE", f"/portfolio/orders/{order_id}")

    def cancel_all(self, ticker: str | None = None) -> int:
        n = 0
        for o in self.resting_orders(ticker):
            oid = o.get("order_id")
            if oid:
                try:
                    self.cancel(oid); n += 1
                except BrokerError:
                    pass
        return n


class MockBroker:
    """Deterministic in-memory broker for offline lifecycle tests.

    Same interface as LiveBroker. Tests drive fills explicitly via fill_order().
    No randomness, no network. Tracks balance, resting orders, and fills.
    """
    def __init__(self, start_balance: float = 100.0) -> None:
        self._bal = start_balance
        self._orders: dict[str, dict] = {}     # order_id -> order
        self._fills: list[dict] = []
        self._seq = 0

    def _oid(self) -> str:
        self._seq += 1
        return f"mock-{self._seq}"

    def balance_dollars(self) -> float:
        return self._bal

    def positions(self, ticker=None):
        return []

    def resting_orders(self, ticker=None):
        return [o for o in self._orders.values()
                if o["status"] == "resting" and (ticker is None or o["ticker"] == ticker)]

    def fills(self, ticker=None, min_ts=None):
        return [f for f in self._fills if ticker is None or f["ticker"] == ticker]

    def place_limit_buy(self, ticker, side, price_dollars, count, client_order_id):
        oid = self._oid()
        o = {"order_id": oid, "client_order_id": client_order_id, "ticker": ticker,
             "side": side, "action": "buy", "price": price_dollars,
             "count": int(count), "remaining_count": int(count), "status": "resting"}
        self._orders[oid] = o
        return dict(o)

    # taker buy: same bookkeeping as a resting order here (tests drive fills
    # explicitly via fill_order); the real broker differs only in post_only.
    def place_taker_buy(self, ticker, side, price_dollars, count, client_order_id):
        return self.place_limit_buy(ticker, side, price_dollars, count, client_order_id)

    def cancel(self, order_id):
        o = self._orders.get(order_id)
        if o and o["status"] == "resting":
            o["status"] = "canceled"
        return {"order_id": order_id, "status": "canceled"}

    def cancel_all(self, ticker=None):
        n = 0
        for o in list(self._orders.values()):
            if o["status"] == "resting" and (ticker is None or o["ticker"] == ticker):
                o["status"] = "canceled"; n += 1
        return n

    # ---- test hook: simulate a (partial) maker fill on a resting order ----
    # Emits the SAME field names the REAL Kalshi /portfolio/fills returns
    # (count_fp, {yes,no}_price_dollars, fee_cost, trade_id, ticker) so tests
    # exercise the real parser. (A prior mismatch here hid a live fill-parse bug.)
    def fill_order(self, order_id: str, count: int, fee: float = 0.0) -> dict:
        o = self._orders[order_id]
        count = min(count, o["remaining_count"])
        o["remaining_count"] -= count
        if o["remaining_count"] <= 0:
            o["status"] = "executed"
        cost = count * o["price"]
        self._bal -= (cost + fee)
        side = o["side"]
        f = {"trade_id": f"t{len(self._fills)+1}", "fill_id": f"t{len(self._fills)+1}",
             "order_id": order_id, "ticker": o["ticker"], "market_ticker": o["ticker"],
             "side": side, "count_fp": f"{count:.2f}", "fee_cost": f"{fee:.6f}",
             f"{side}_price_dollars": f"{o['price']:.4f}",
             f"{'yes' if side=='no' else 'no'}_price_dollars": f"{1.0 - o['price']:.4f}"}
        self._fills.append(f)
        return f

    def credit(self, amount: float) -> None:
        """Settlement payout into the account (test helper)."""
        self._bal += amount
