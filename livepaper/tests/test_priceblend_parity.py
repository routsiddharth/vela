"""Phase-0 golden-master gate (MIGRATION_PLAN.md §5 Phase 0).

The new extraction path (PriceBlend.price -> RawAvgBundle -> projection.project)
must reproduce the CURRENT engine exactly. Three layers:

  1. primitive equivalence — projection's _norm_cdf / _remaining_var_factor are
     bit-identical to engine's (guards the copies in projection.py).
  2. projection logic — a hand-checked closed-form case (no DB needed).
  3. replay golden master — every recorded `estimate` row reproduced bit-for-bit
     off the real recorded data (skipped if no paper.db is present).
"""
from __future__ import annotations
import math
from pathlib import Path
import pytest

from livepaper import config as C
from livepaper import projection as proj
from livepaper.contract import RawAvgBundle, SettlementTruth
from livepaper.priceblend import PriceBlend
from livepaper.market import Debias
from livepaper.replay import (run_parity, ReplayFeed, _legacy_norm_cdf,
                              _legacy_remaining_var_factor, _legacy_local_avg60)


# --- 1. primitive equivalence (projection's copies == frozen pre-migration) --
def test_remaining_var_factor_matches_legacy():
    for n in range(0, C.SETTLE_SECS + 1):
        assert proj._remaining_var_factor(n) == _legacy_remaining_var_factor(n), n


def test_norm_cdf_matches_legacy():
    for x in (-6.0, -2.5, -1.0, -0.3, 0.0, 0.3, 1.0, 2.5, 6.0, 18.6):
        assert proj._norm_cdf(x) == _legacy_norm_cdf(x), x


# --- 2. projection logic (closed form, no DB) --------------------------------
def test_projection_nothing_locked():
    """Decision well before the window opens: nothing locked, so shat == spot and
    mhat == spot - delta; sd_S uses the raw stats unchanged."""
    close_ts = 1_000_000
    now = close_ts - 200.0                      # 200s out => int(now)-start < 0 => n_elapsed 0
    bundle = RawAvgBundle(asset="BTC", ts=int(now), symbol="BTCUSDT",
                          raw_avg=60_000.0, n_prices=1, delta=85.0,
                          sigma_sec=8.0, resid_std=12.0)
    p = proj.project(bundle, lambda e: None, strike=59_900.0, close_ts=close_ts, now=now)
    assert p.n_lock == 0 and p.n_elapsed == 0 and p.n_rem == C.SETTLE_SECS
    assert p.lmean == 60_000.0
    assert p.shat == 60_000.0
    assert p.mhat == 60_000.0 - 85.0
    assert p.margin == (60_000.0 - 85.0) - 59_900.0
    assert p.bet_yes is True
    exp_sd = math.sqrt(8.0 ** 2 * proj._remaining_var_factor(C.SETTLE_SECS) + 12.0 ** 2)
    assert p.sd_S == exp_sd
    assert p.p_side == proj._norm_cdf(abs(p.margin) / exp_sd)


def test_projection_fallbacks_match_config():
    """None sigma/resid -> the config priors the engine uses (spot-/mhat-relative)."""
    close_ts = 1_000_000
    now = close_ts - 200.0
    bundle = RawAvgBundle(asset="BTC", ts=int(now), symbol="BTCUSDT",
                          raw_avg=60_000.0, n_prices=1, delta=0.0,
                          sigma_sec=None, resid_std=None)
    p = proj.project(bundle, lambda e: None, strike=50_000.0, close_ts=close_ts, now=now)
    assert p.sigma_sec_used == C.SIGMA_FALLBACK_BPS / 1e4 * 60_000.0
    assert p.resid_std_used == C.PROXY_SD_FALLBACK_BPS / 1e4 * abs(p.mhat)


def test_contract_roundtrips():
    b = RawAvgBundle("BTC", 1, "BTCUSDT", 60000.0, 1, 85.0, 8.0, None)
    assert RawAvgBundle.from_dict(b.to_dict()) == b
    t = SettlementTruth("KX-1", "KXBTC15M", "BTC", "BTCUSDT", 1, 60000.0)
    assert SettlementTruth.from_dict(t.to_dict()) == t


def test_calibrate_records_debias_sample():
    """PriceBlend.calibrate computes err = raw_avg60 - true_settle off the local
    buffer and feeds Debias — synthetic, fully checkable by hand."""
    close_ts = 1_000_000
    feed = ReplayFeed()
    # 60 locked closes all at 60_100 over [close_ts-60, close_ts)
    feed.prices["BTCUSDT"] = {s: 60_100.0 for s in range(close_ts - 60, close_ts)}
    feed.secs["BTCUSDT"] = sorted(feed.prices["BTCUSDT"])
    debias = {"BTC": Debias("BTC", "BTCUSDT")}
    pb = PriceBlend(feed, debias, asset_symbol={"BTC": "BTCUSDT"})
    truth = SettlementTruth("KX-1", "KXBTC15M", "BTC", "BTCUSDT", close_ts, 60_000.0)
    res = pb.calibrate(truth)
    assert res.source == "local"
    assert res.binance_avg60 == 60_100.0
    assert res.err == 100.0                      # 60_100 - 60_000
    assert debias["BTC"].samples == [(close_ts, 100.0)]


# --- 3. replay golden master against recorded data ---------------------------


# --- 3. replay golden master against recorded data ---------------------------
def _db_for(asset: str) -> Path:
    return C.ROOT / f"data_{asset.lower()}" / "paper.db"


@pytest.mark.parametrize("asset", ["BTC", "ETH"])
def test_replay_golden_master(asset):
    db = _db_for(asset)
    if not db.exists():
        pytest.skip(f"no recorded data at {db}")
    rep = run_parity(db, asset, n=8000)
    if rep.resolved == 0:
        pytest.skip(f"no resolvable recorded ticks for {asset}")
    print("\n" + rep.summary())
    # PRIMARY GATE: the new path reproduces the REAL engine bit-for-bit on the
    # same feed (behaviour-preserving refactor). Gaps in the recorded prices
    # cancel because both sides read the same feed, so this must be exactly 0.
    assert rep.p_nlock == 0, rep.summary()
    assert rep.p_mhat < 1e-9, rep.summary()
    assert rep.p_margin < 1e-9, rep.summary()
    assert rep.p_shat < 1e-9, rep.summary()
    assert rep.p_lmean < 1e-9, rep.summary()
    assert rep.p_sdS < 1e-9, rep.summary()
    assert rep.p_pside < 1e-12, rep.summary()
    assert rep.primary_ok(), rep.summary()
    # DIAGNOSTIC (not a refactor gate): where the LOCKED window is fully recorded,
    # the replay must reproduce the live LOG's mhat exactly — confirms the harness
    # is faithful, not just internally consistent. (sd_S vs log stays nonzero: the
    # 121s sigma lookback is essentially never fully recorded under the lossy
    # per-tick price write — that fidelity gap is what the diagnostic exists to
    # surface; sd_S itself is already proven bit-exact by the PRIMARY gate.)
    if rep.full_cov:
        assert rep.d_mhat_fullcov < 1e-6, rep.summary()


@pytest.mark.parametrize("asset", ["BTC", "ETH"])
def test_calibrate_avg60_matches_legacy(asset):
    """PriceBlend._local_avg60 (calibration path) reproduces the FROZEN
    pre-migration avg60 bit-for-bit on the same feed, over recorded windows."""
    import sqlite3
    db = _db_for(asset)
    if not db.exists():
        pytest.skip(f"no recorded data at {db}")
    symbol = C.ASSET_SYMBOL.get(asset, {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}[asset])
    feed = ReplayFeed().load(db, symbol)
    con = sqlite3.connect(str(db))
    wins = con.execute("SELECT close_ts FROM windows WHERE asset=? ORDER BY close_ts DESC "
                       "LIMIT 40", (asset,)).fetchall()
    con.close()
    if not wins:
        pytest.skip(f"no settled windows for {asset}")
    checked = 0
    for (close_ts,) in wins:
        pb = PriceBlend(feed, {asset: Debias(asset, symbol)}, asset_symbol={asset: symbol})
        got = pb._local_avg60(symbol, int(close_ts))
        want = _legacy_local_avg60(feed, symbol, int(close_ts))
        assert got == want, (asset, close_ts, got, want)   # both None or both equal
        checked += 1
    assert checked > 0
