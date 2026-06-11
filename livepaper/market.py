"""Order book, per-market state, REST discovery, and the causal de-bias tracker."""
from __future__ import annotations
import statistics, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import httpx
from . import config as C


def _epoch(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


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


class Debias:
    """Per-asset causal Binance->RTI bias: trailing median of (binance_avg60 -
    true_settle). One instance per asset (BTC, ETH); both BTC series share it."""
    def __init__(self, asset: str, symbol: str) -> None:
        self.asset = asset
        self.symbol = symbol
        self.samples: list[tuple[int, float]] = []   # (close_ts, err)

    def add(self, close_ts: int, err: float) -> None:
        self.samples.append((close_ts, err))
        self.samples.sort()

    def delta(self) -> float:
        if not self.samples:
            return 0.0
        errs = [e for _, e in self.samples[-C.DEBIAS_LOOKBACK:]]
        return statistics.median(errs)

    def resid_std(self) -> float | None:
        """Causal std of the trailing de-bias tracking error (binance_avg60 -
        true_settle). This is the dominant `proxy_sd` term in sd_S. Returns None
        until there are enough samples (caller falls back to a price-relative prior)."""
        errs = [e for _, e in self.samples[-C.DEBIAS_LOOKBACK:]]
        if len(errs) < 10:
            return None
        return statistics.pstdev(errs)

    def bootstrap(self, disc: Discovery, store, log, series: str) -> None:
        windows = disc.recent_settled(series, C.DEBIAS_BOOTSTRAP)
        log(f"debias[{self.asset}] bootstrap: {len(windows)} windows from {series}; "
            f"fetching {self.symbol} avg60...")
        with ThreadPoolExecutor(max_workers=12) as ex:
            avgs = list(ex.map(lambda w: disc.binance_avg60(self.symbol, w["close_ts"]), windows))
        n = 0
        for w, a in zip(windows, avgs):
            if a is None:
                continue
            err = a - w["true_settle"]
            self.add(w["close_ts"], err)
            store.debias_row(w["ticker"], self.asset, w["close_ts"], a, w["true_settle"], err)
            n += 1
        log(f"debias[{self.asset}] done: {n} samples, delta=${self.delta():.2f}")
