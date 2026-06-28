"""The PriceBlend <-> Trading wire contract (MIGRATION_PLAN.md §2).

This is the ENTIRE coupling between the two future services. It is transport-
agnostic on purpose: the same two dataclasses describe an in-process call (Phase
1), an in-process queue (Phase 2), and a cross-machine message (Phase 3-4). Keep
the field set here as the single source of truth for the wire; both sides import
it so a schema change can't drift between producer and consumer.

A. RawAvgBundle  — PriceBlend -> Trading, ~1/sec per asset. The raw Binance
   average for one emitted bucket plus the de-bias statistics the trading-side
   projection needs. It is NEVER de-biased: `delta` is shipped alongside so the
   trading side applies it exactly once, window-aligned (the §2 parity nuance).

B. SettlementTruth — Trading/Kalshi -> PriceBlend, per settled window. Feeds the
   de-bias calibration. PriceBlend computes its own raw avg60 and records the
   error; the de-bias is never applied before measuring it.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class RawAvgBundle:
    """§2.A — one emitted price bucket + de-bias stats. `sigma_sec`/`resid_std`
    are the RAW statistics (either may be None until warm); the trading side
    owns the warm-up fallbacks so a cold price box and a cold monolith decide
    identically."""
    asset: str             # "BTC" / "ETH"
    ts: int                # epoch second of the latest close in this bucket (staleness clock)
    symbol: str            # Binance symbol, e.g. "BTCUSDT"
    raw_avg: float         # raw average of Binance closes in this bucket (USD); never de-biased
    n_prices: int          # samples in the bucket (usually 1 at 1/sec cadence)
    delta: float           # raw Binance minus RTI bias (USD); Trading subtracts this ONCE
    sigma_sec: float | None  # per-second diffusion std (USD) = feed.recent_sigma; None until warm
    resid_std: float | None  # de-bias tracking std (USD) = Debias.resid_std; None until warm

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RawAvgBundle":
        return cls(
            asset=d["asset"], ts=int(d["ts"]), symbol=d["symbol"],
            raw_avg=float(d["raw_avg"]), n_prices=int(d["n_prices"]),
            delta=float(d["delta"]),
            sigma_sec=None if d.get("sigma_sec") is None else float(d["sigma_sec"]),
            resid_std=None if d.get("resid_std") is None else float(d["resid_std"]),
        )


@dataclass(frozen=True)
class SettlementTruth:
    """§2.B — a settled window's true RTI settle, into PriceBlend for de-bias
    calibration. `series`/`ticker` identify the calibration window; PriceBlend
    looks up nothing tradeable from it (this is NOT market discovery)."""
    ticker: str
    series: str
    asset: str
    symbol: str
    close_ts: int
    true_settle: float

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SettlementTruth":
        return cls(
            ticker=d["ticker"], series=d["series"], asset=d["asset"],
            symbol=d["symbol"], close_ts=int(d["close_ts"]),
            true_settle=float(d["true_settle"]),
        )
