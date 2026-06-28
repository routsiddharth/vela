"""Binance 1s spot feed for the PriceBlend service.

Owns the raw per-second closes + recent_sigma. Uses a certifi SSL context (the
local cert chain has a self-signed root, so the system default store fails the
handshake). Reconnects with backoff forever; a 6-12h unattended run must survive
drops. Relocated verbatim from the old feeds.py — behaviour unchanged.
"""
from __future__ import annotations
import asyncio, json, ssl, time
import certifi
from websockets.asyncio.client import connect
from .. import config as C

_SSL = ssl.create_default_context(cafile=certifi.where())


class BinanceFeed:
    """Last EST_BUFFER_SECS of 1s closes for MANY symbols, via one combined stream.
    prices[symbol][epoch_sec] -> close; last[symbol] -> (sec, close)."""
    def __init__(self, store, log, symbols: list[str]) -> None:
        self.store = store
        self.log = log
        self.symbols = [s.lower() for s in symbols]
        self.prices: dict[str, dict[int, float]] = {s.upper(): {} for s in symbols}
        self.last: dict[str, tuple[int, float]] = {}

    def price_at(self, symbol: str, sec: int) -> float | None:
        return self.prices.get(symbol, {}).get(sec)

    def latest(self, symbol: str) -> tuple[int, float] | None:
        return self.last.get(symbol)

    def recent_sigma(self, symbol: str, lookback: int) -> float | None:
        """Per-second price-step std (USD) over the last `lookback` 1s closes —
        the diffusion sigma for the settlement variance. None until warmed up."""
        buf = self.prices.get(symbol)
        if not buf or len(buf) < 12:
            return None
        secs = sorted(buf)[-(lookback + 1):]
        diffs = [buf[secs[i]] - buf[secs[i - 1]] for i in range(1, len(secs))]
        if len(diffs) < 8:
            return None
        import statistics
        return statistics.pstdev(diffs)

    def url(self) -> str:
        return C.BINANCE_WS_BASE + "/".join(f"{s}@kline_1s" for s in self.symbols)

    async def run(self, stop: asyncio.Event) -> None:
        backoff = 1.0
        while not stop.is_set():
            try:
                async with connect(self.url(), ssl=_SSL, ping_interval=15,
                                   max_queue=1024) as ws:
                    backoff = 1.0
                    self.store.event("binance_ws_up")
                    while not stop.is_set():
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        m = json.loads(raw)
                        data = m.get("data") or m         # combined stream wraps in .data
                        k = data.get("k") or {}
                        close = k.get("c")
                        sym = (k.get("s") or "").upper()
                        if close is None or not sym:
                            continue
                        sec = int(k["T"]) // 1000           # the second that just closed
                        price = float(close)
                        buf = self.prices.setdefault(sym, {})
                        buf[sec] = price
                        self.last[sym] = (sec, price)
                        cutoff = sec - C.EST_BUFFER_SECS
                        if len(buf) > C.EST_BUFFER_SECS + 60:
                            for s in [s for s in buf if s < cutoff]:
                                del buf[s]
                        self.store.raw_binance({"sym": sym, "sec": sec, "c": price, "x": k.get("x")})
            except Exception as e:
                self.store.event("binance_ws_down", str(e)[:200])
                self.log(f"binance ws down: {e}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
