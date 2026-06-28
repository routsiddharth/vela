"""Kalshi orderbook/trade/lifecycle WebSocket for the Trading service.

Subscribes orderbook_delta + trade + lifecycle for a dynamic market set and signs
with the Kalshi trading key. certifi SSL + forever-reconnect, same as the Binance
feed. Relocated verbatim from the old feeds.py — behaviour unchanged.
"""
from __future__ import annotations
import asyncio, base64, json, os, ssl, time
import certifi
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from websockets.asyncio.client import connect
from dotenv import load_dotenv
from pathlib import Path
from .. import config as C

load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
_SSL = ssl.create_default_context(cafile=certifi.where())


def _load_key():
    raw = os.environ["KALSHI_API_SECRET"]
    pem = raw.replace("\\n", "\n").encode()
    return serialization.load_pem_private_key(pem, password=None)


class _Signer:
    def __init__(self) -> None:
        self.kid = os.environ["KALSHI_API_KEY"].strip()
        self.key = _load_key()

    def ws_headers(self) -> dict:
        ts = str(int(time.time() * 1000))
        sig = self.key.sign(f"{ts}GET{C.KALSHI_WS_PATH}".encode(),
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                        salt_length=padding.PSS.DIGEST_LENGTH),
                            hashes.SHA256())
        return {"KALSHI-ACCESS-KEY": self.kid,
                "KALSHI-ACCESS-TIMESTAMP": ts,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode()}


class KalshiWS:
    """Subscribes orderbook_delta + trade + lifecycle for a dynamic market set.

    `desired` is owned by the engine (updated by discovery). On change we
    (re)subscribe the new markets. Each market's orderbook gets its own
    subscription (one ticker per subscribe) so its snapshot/seq stream is clean.
    Callbacks: on_snapshot/on_delta/on_trade/on_lifecycle(ticker, msg).
    """
    def __init__(self, store, log, callbacks) -> None:
        self.store = store
        self.log = log
        self.cb = callbacks
        self.signer = _Signer()
        self.desired: set[str] = set()
        self._subscribed: set[str] = set()     # markets we've sent an orderbook sub for
        self._ws = None
        self._id = 0
        self._changed = asyncio.Event()

    def set_desired(self, markets: set[str]) -> None:
        if markets != self.desired:
            self.desired = set(markets)
            self._changed.set()

    def _next(self) -> int:
        self._id += 1
        return self._id

    async def _sub_market(self, ws, ticker: str) -> None:
        for chan in ("orderbook_delta", "trade", "market_lifecycle_v2"):
            await ws.send(json.dumps({"id": self._next(), "cmd": "subscribe",
                                      "params": {"channels": [chan],
                                                 "market_tickers": [ticker]}}))
        self._subscribed.add(ticker)

    async def run(self, stop: asyncio.Event) -> None:
        backoff = 1.0
        while not stop.is_set():
            try:
                async with connect(C.KALSHI_WS, additional_headers=self.signer.ws_headers(),
                                   ssl=_SSL, ping_interval=10, max_queue=2048) as ws:
                    self._ws = ws
                    self._subscribed.clear()
                    backoff = 1.0
                    self.store.event("kalshi_ws_up")
                    for tk in sorted(self.desired):
                        await self._sub_market(ws, tk)
                    reader = asyncio.create_task(self._read(ws, stop))
                    submgr = asyncio.create_task(self._submgr(ws, stop))
                    done, pending = await asyncio.wait(
                        {reader, submgr}, return_when=asyncio.FIRST_COMPLETED)
                    for t in pending:
                        t.cancel()
                    for t in done:
                        t.result()
            except Exception as e:
                self.store.event("kalshi_ws_down", str(e)[:200])
                self.log(f"kalshi ws down: {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _submgr(self, ws, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(self._changed.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
            self._changed.clear()
            for tk in sorted(self.desired - self._subscribed):
                await self._sub_market(ws, tk)

    async def _read(self, ws, stop: asyncio.Event) -> None:
        while not stop.is_set():
            raw = await asyncio.wait_for(ws.recv(), timeout=60)
            m = json.loads(raw)
            t = m.get("type")
            if t not in ("orderbook_snapshot", "orderbook_delta", "trade",
                         "market_lifecycle_v2", "market_lifecycle"):
                continue
            self.store.raw_kalshi(m)
            msg = m.get("msg") or {}
            tk = msg.get("market_ticker") or msg.get("market_id")
            if not tk:
                continue
            if t == "orderbook_snapshot":
                self.cb["snapshot"](tk, msg)
            elif t == "orderbook_delta":
                self.cb["delta"](tk, msg)
            elif t == "trade":
                self.cb["trade"](tk, msg)
            else:
                self.cb["lifecycle"](tk, msg)
