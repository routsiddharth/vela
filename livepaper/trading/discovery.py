"""Kalshi REST discovery for the Trading service.

`active()` finds currently-tradeable floor-strike markets (strikes, near-spot
bands) — the only piece the trading side needs to pick markets. `settled()` /
`recent_settled()` / `binance_avg60()` are the read-only settlement lookups the
PriceBlend de-bias calibration consumes (injected into PriceBlend, see §6); they
live here for now because they share this REST client. Relocated verbatim from
the old market.py.
"""
from __future__ import annotations
import time
from datetime import datetime, timezone
import httpx
from .. import config as C


def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


class Discovery:
    """Thin REST layer over Kalshi for market discovery + settlement + bootstrap."""
    def __init__(self, kalshi) -> None:
        self.k = kalshi
        self.http = httpx.Client(timeout=20.0)

    def active(self, series: str) -> list[dict]:
        """Currently-tradeable markets in `series` (status active/open).

        Only floor-strike `greater[_or_equal]` markets are admitted (YES iff
        settle >= floor): that's the single-margin model the edge was validated on.
        Two-sided range / cap-only markets have no floor_strike and are skipped."""
        out = []
        for status in ("open", "active"):
            try:
                r = self.k.get("/markets", {"series_ticker": series, "status": status, "limit": 100})
            except Exception:
                continue
            for m in r.get("markets", []):
                strike = m.get("floor_strike")
                if strike is None or not m.get("close_time"):
                    continue
                if m.get("strike_type") not in ("greater", "greater_or_equal"):
                    continue
                out.append({"ticker": m["ticker"], "close_ts": _epoch(m["close_time"]),
                            "strike": float(strike)})
        return list({d["ticker"]: d for d in out}.values())

    def settled(self, ticker: str) -> dict | None:
        try:
            r = self.k.get(f"/markets/{ticker}")
        except Exception:
            return None
        m = r.get("market") or r
        ev, res = m.get("expiration_value"), m.get("result")
        if not ev or not res:
            return None
        try:
            return {"true_settle": float(ev), "result": res, "close_ts": _epoch(m["close_time"])}
        except (TypeError, ValueError):
            return None

    def recent_settled(self, series: str, n: int) -> list[dict]:
        """Most-recent settled windows in `series` (for the de-bias bootstrap)."""
        out, cursor = [], None
        while len(out) < n:
            p = {"series_ticker": series, "status": "settled", "limit": 100}
            if cursor:
                p["cursor"] = cursor
            try:
                r = self.k.get("/markets", p)
            except Exception:
                break
            ms = r.get("markets", [])
            for m in ms:
                ev = m.get("expiration_value")
                if ev and m.get("close_time"):
                    try:
                        out.append({"ticker": m["ticker"], "close_ts": _epoch(m["close_time"]),
                                    "true_settle": float(ev)})
                    except (TypeError, ValueError):
                        pass
            cursor = r.get("cursor")
            if not cursor or not ms:
                break
        out.sort(key=lambda d: d["close_ts"])
        return out[-n:]

    def binance_avg60(self, symbol: str, close_ts: int) -> float | None:
        """Mean of Binance 1s closes for `symbol` over the 60s ending at close_ts."""
        end = close_ts * 1000
        start = end - C.SETTLE_SECS * 1000
        for attempt in range(4):
            try:
                r = self.http.get(f"{C.BINANCE_REST}/api/v3/klines",
                                  params={"symbol": symbol, "interval": "1s",
                                          "startTime": start, "endTime": end, "limit": 70})
                if r.status_code in (429, 418):
                    time.sleep(1.0 * (attempt + 1)); continue
                r.raise_for_status()
                cs = [float(c[4]) for c in r.json()
                      if start <= int(c[0]) < end]
                return sum(cs) / len(cs) if cs else None
            except Exception:
                time.sleep(0.5 * (attempt + 1))
        return None
