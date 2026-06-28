"""Trading-side settlement projection (MIGRATION_PLAN.md §2 reconstruction).

Given a RawAvgBundle (the latest raw-average bucket + de-bias stats), the buffered
raw-average buckets for the window's already-elapsed seconds, and the window's own
`strike`/`close_ts`/decision `now`, reproduce today's `engine._estimate` +
`p_side` EXACTLY:

    start    = close_ts - 60;  n_elapsed = clamp(int(now) - start, 0, 60)
    locked   = buffered raw_avg for [start, start+n_elapsed)
    lmean    = mean(locked)                              (== spot when nothing locked)
    shat     = (lmean*n_elapsed + raw_avg*n_rem) / 60    (raw Binance estimate)
    mhat     = shat - delta                              (de-bias applied ONCE, here)
    margin   = mhat - strike
    sd_S     = sqrt(sigma_sec^2 * remaining_var_factor(n_rem) + resid_std^2)
    p_side   = norm_cdf(|margin| / sd_S)

The de-bias is applied here and only here (the §2 parity nuance): PriceBlend ships
the raw average plus `delta`, never an already-de-biased price, so a mid-window
`delta` update can't mix deltas across the averaged buckets.

This is a behaviour-preserving re-expression of engine.py:228-249 + the p_side
line at engine.py:199. The Phase-0 golden-master test (tests/test_priceblend_
parity.py) asserts it reproduces the recorded live `estimates` rows bit-for-bit;
the `_norm_cdf` / `_remaining_var_factor` copies below are validated against the
engine's originals there.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Callable
from .. import config as C
from ..contract import RawAvgBundle


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _remaining_var_factor(n_rem: int) -> float:
    """sum_{i,j in 1..n_rem} min(i,j) / 60^2 — Var(S)/sigma_sec^2 for the n_rem
    unlocked settlement samples sharing one Brownian path. Copy of
    engine._remaining_var_factor; equivalence is asserted by the parity test."""
    if n_rem <= 1:
        return 0.0
    s = 0
    for i in range(1, n_rem + 1):
        s += i * (i + 1) // 2 + (n_rem - i) * i   # sum_j min(i,j)
    return s / (60.0 ** 2)


@dataclass(frozen=True)
class Projection:
    """The window projection at one decision/tick instant."""
    mhat: float
    margin: float
    n_lock: int
    lmean: float
    shat: float
    sd_S: float
    p_side: float
    bet_yes: bool          # margin > 0
    n_elapsed: int
    n_rem: int
    sigma_sec_used: float  # post-fallback diffusion std actually used
    resid_std_used: float  # post-fallback de-bias tracking std actually used


def project(latest: RawAvgBundle, locked_at: Callable[[int], float | None],
            strike: float, close_ts: int, now: float) -> Projection:
    """Reproduce engine._estimate + p_side from the §2 inputs.

    `latest`     — the most-recent RawAvgBundle (its raw_avg is today's `spot`).
    `locked_at`  — locked_at(sec) -> buffered raw_avg for that second, or None
                   (mirrors feed.price_at over the window's elapsed seconds).
    `strike`, `close_ts`, `now` — owned by the trading side (from discovery + clock).
    """
    spot = latest.raw_avg
    delta = latest.delta

    start = close_ts - C.SETTLE_SECS
    n_elapsed = min(C.SETTLE_SECS, max(0, int(now) - start))
    locked = [locked_at(e) for e in range(start, start + n_elapsed)]
    locked = [p for p in locked if p is not None]
    n_lock = len(locked)
    lmean = sum(locked) / n_lock if n_lock else spot
    shat = (lmean * n_elapsed + spot * (C.SETTLE_SECS - n_elapsed)) / C.SETTLE_SECS
    mhat = shat - delta
    margin = mhat - strike

    n_rem = max(0, C.SETTLE_SECS - n_elapsed)
    sigma_sec = latest.sigma_sec
    if sigma_sec is None:
        sigma_sec = C.SIGMA_FALLBACK_BPS / 1e4 * spot
    proxy_sd = latest.resid_std
    if proxy_sd is None:
        proxy_sd = C.PROXY_SD_FALLBACK_BPS / 1e4 * abs(mhat)
    diff_var = sigma_sec ** 2 * _remaining_var_factor(n_rem)
    sd_S = math.sqrt(diff_var + proxy_sd ** 2) or 1e-6
    p_side = _norm_cdf(abs(margin) / sd_S)

    return Projection(mhat=mhat, margin=margin, n_lock=n_lock, lmean=lmean,
                      shat=shat, sd_S=sd_S, p_side=p_side, bet_yes=margin > 0,
                      n_elapsed=n_elapsed, n_rem=n_rem,
                      sigma_sec_used=sigma_sec, resid_std_used=proxy_sd)
