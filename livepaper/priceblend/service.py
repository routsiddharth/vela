"""PriceBlend service skeleton (MIGRATION_PLAN.md §1, §3, Phase 1).

PriceBlend owns the Binance feed and the de-bias state. Its input is just an
asset; its output is the raw Binance average bundle (§2.A) plus the de-bias
statistics the trading-side projection needs. It deliberately does NOT apply the
de-bias to the emitted price, and it knows NOTHING about Kalshi markets, strikes,
windows, or orders — only an asset -> symbol map and the settlement-truth needed
to self-calibrate.

In-process for now (Phase 1): `BinanceFeed` and the per-asset `Debias` are
injected, so the live `Engine` and the offline replay harness can both back a
PriceBlend with the same units. The REST avg60 fallback used during calibration
is injected too (`rest_avg60`), keeping this module free of httpx/Kalshi so it
runs fully offline in tests; production wiring passes `Discovery.binance_avg60`.
Under §6.A this becomes PriceBlend's own narrow settlement reader.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
from .. import config as C
from ..contract import RawAvgBundle, SettlementTruth


@dataclass(frozen=True)
class CalibrationResult:
    """What one settlement-truth produced for the de-bias tracker."""
    ticker: str
    asset: str
    close_ts: int
    binance_avg60: float | None   # raw Binance avg60 PriceBlend measured (None if unavailable)
    true_settle: float
    err: float | None             # binance_avg60 - true_settle (the de-bias sample); None if no avg60
    source: str                   # "local" | "rest" | "none"


class PriceBlend:
    def __init__(self, feed, debias: dict, asset_symbol: dict | None = None,
                 rest_avg60: Callable[[str, int], float | None] | None = None) -> None:
        self.feed = feed                                   # BinanceFeed (price_at/latest/recent_sigma)
        self.debias = debias                               # asset -> Debias
        self.asset_symbol = asset_symbol or dict(C.ASSET_SYMBOL)
        self.rest_avg60 = rest_avg60                       # fallback REST avg60(symbol, close_ts)

    # ---- §2.A producer ------------------------------------------------------
    def price(self, asset: str) -> RawAvgBundle | None:
        """Latest raw-average bundle for `asset`. None until the feed has a tick.

        `raw_avg` is the latest 1s Binance close (n_prices=1 at 1/sec cadence) and
        is NEVER de-biased; `delta` rides alongside for the trading side to apply
        once. `sigma_sec`/`resid_std` are the RAW stats (either may be None until
        warm) — the trading-side projection owns the warm-up fallbacks."""
        symbol = self.asset_symbol.get(asset)
        if symbol is None:
            return None
        latest = self.feed.latest(symbol)
        if latest is None:
            return None
        ts, raw_avg = latest
        return RawAvgBundle(
            asset=asset, ts=ts, symbol=symbol, raw_avg=raw_avg, n_prices=1,
            delta=self.debias[asset].delta(),
            sigma_sec=self.feed.recent_sigma(symbol, C.SIGMA_LOOKBACK),
            resid_std=self.debias[asset].resid_std(),
        )

    def bucket_at(self, asset: str, sec: int) -> float | None:
        """Raw_avg for an already-elapsed second of this asset's stream — what the
        trading-side projection averages over the window's locked seconds. In-process
        this reads PriceBlend's own feed buffer (the full per-second history); across
        a process boundary (Phase 2) the trading side buffers the emitted stream
        instead. Either way the engine never touches the raw feed directly."""
        symbol = self.asset_symbol.get(asset)
        if symbol is None:
            return None
        return self.feed.price_at(symbol, sec)

    # ---- §2.B consumer (self-calibration) -----------------------------------
    def calibrate(self, truth: SettlementTruth) -> CalibrationResult:
        """Record one settlement-truth into the de-bias tracker. Mirrors
        engine._settle (engine.py:274-280): measure the RAW Binance avg60 over
        [close_ts-60, close_ts) from the local buffer (REST fallback), err =
        avg60 - true_settle, then Debias.add. The de-bias is never applied before
        measuring this error."""
        b60 = self._local_avg60(truth.symbol, truth.close_ts)
        source = "local"
        if b60 is None and self.rest_avg60 is not None:
            b60 = self.rest_avg60(truth.symbol, truth.close_ts)
            source = "rest"
        if b60 is None:
            return CalibrationResult(truth.ticker, truth.asset, truth.close_ts,
                                     None, truth.true_settle, None, "none")
        err = b60 - truth.true_settle
        self.debias[truth.asset].add(truth.close_ts, err)
        return CalibrationResult(truth.ticker, truth.asset, truth.close_ts,
                                 b60, truth.true_settle, err, source)

    def _local_avg60(self, symbol: str, close_ts: int) -> float | None:
        """Mean of the local feed's 1s closes over [close_ts-60, close_ts);
        needs >= 30 samples (mirrors engine._local_avg60)."""
        ps = [self.feed.price_at(symbol, e)
              for e in range(close_ts - C.SETTLE_SECS, close_ts)]
        ps = [p for p in ps if p is not None]
        return sum(ps) / len(ps) if len(ps) >= 30 else None
