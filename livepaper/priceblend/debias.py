"""Causal Binance->RTI de-bias tracker for the PriceBlend service.

Per-asset trailing median of (binance_avg60 - true_settle); ships `delta` +
`resid_std` in the price bundle. The bootstrap pulls historical settled windows
through an injected calibration source (Discovery, duck-typed: it only needs
`recent_settled` + `binance_avg60`), so PriceBlend stays independent of the
trading-side market discovery. Relocated verbatim from the old market.py.
"""
from __future__ import annotations
import statistics
from concurrent.futures import ThreadPoolExecutor
from .. import config as C


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

    def bootstrap(self, disc, store, log, series: str) -> None:
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
