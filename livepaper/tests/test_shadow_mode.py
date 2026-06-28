"""Phase-1 shadow-mode wiring test (MIGRATION_PLAN.md Phase 1).

Drives a REAL engine.tick() with VELA_SHADOW on, over a ReplayFeed, and asserts
the new PriceBlend+projection path diverges from the live _estimate path by zero
in-process. A negative control proves the comparison actually fires (so a green
run means "no divergence", not "check skipped"). Trading is never touched.
"""
from __future__ import annotations
import sqlite3
import pytest

from livepaper import config as C
from livepaper import engine as eng
from livepaper.engine import Engine
from livepaper.market import MarketState, Debias
from livepaper.replay import ReplayFeed


class _FakeStore:
    """Captures shadow rows; everything else the tick touches is a no-op."""
    def __init__(self):
        self.shadow_rows = []
        self.n_est = 0
    def price(self, *a): pass
    def book(self, *a): pass
    def estimate(self, *a): self.n_est += 1
    def oracle(self, *a): pass
    def shadow(self, t, asset, sec, field, old, new, diff):
        self.shadow_rows.append((field, old, new, diff))
    def event(self, *a, **k): pass


def _recent_btc_window():
    db = C.ROOT / "data_btc" / "paper.db"
    if not db.exists():
        return None
    con = sqlite3.connect(str(db))
    row = con.execute("SELECT ticker, close_ts, strike FROM windows "
                      "WHERE asset='BTC' ORDER BY close_ts DESC LIMIT 1").fetchone()
    con.close()
    return (db, *row) if row else None


def _engine_with(feed, debias, store):
    return Engine(store, feed, disc=None, debias=debias, market_meta={},
                  log=lambda *a, **k: None)


def test_shadow_zero_divergence_in_tick(monkeypatch):
    info = _recent_btc_window()
    if info is None:
        pytest.skip("no recorded BTC data")
    db, ticker, close_ts, strike = info
    symbol = "BTCUSDT"
    feed = ReplayFeed().load(db, symbol)

    now = float(close_ts - 30)                  # 30s to close => in-window, decides
    cursor = feed.latest_sec_at(symbol, now)
    assert cursor is not None
    feed.set_cursor(symbol, cursor)

    # reconstruct a plausible Debias so resid_std()/delta() are warm
    con = sqlite3.connect(str(db))
    samples = con.execute("SELECT close_ts, err FROM debias WHERE asset='BTC' "
                          "AND close_ts < ? ORDER BY close_ts DESC LIMIT ?",
                          (now, C.DEBIAS_LOOKBACK)).fetchall()
    con.close()
    db_obj = Debias("BTC", symbol)
    db_obj.samples = sorted((int(c), float(e)) for c, e in samples)
    debias = {"BTC": db_obj}

    store = _FakeStore()
    monkeypatch.setattr(C, "SHADOW", True)
    monkeypatch.setattr(eng.time, "time", lambda: now)
    engine = _engine_with(feed, debias, store)
    engine.states[ticker] = MarketState(ticker, close_ts, float(strike),
                                         "BTC", symbol, "KXBTC15M")

    engine.tick()

    assert store.n_est >= 1, "tick did not process the market"
    assert store.shadow_rows == [], f"shadow divergence: {store.shadow_rows}"


def test_shadow_negative_control(monkeypatch):
    """A deliberately wrong `old` mhat must produce a shadow_diff row — proves the
    comparison isn't silently skipping."""
    info = _recent_btc_window()
    if info is None:
        pytest.skip("no recorded BTC data")
    db, ticker, close_ts, strike = info
    symbol = "BTCUSDT"
    feed = ReplayFeed().load(db, symbol)
    now = float(close_ts - 30)
    feed.set_cursor(symbol, feed.latest_sec_at(symbol, now))
    debias = {"BTC": Debias("BTC", symbol)}
    store = _FakeStore()
    engine = _engine_with(feed, debias, store)
    s = MarketState(ticker, close_ts, float(strike), "BTC", symbol, "KXBTC15M")

    # real values from the live path, then perturb mhat by +1.0
    delta = debias["BTC"].delta()
    mhat, margin, n_lock, lmean, shat, sd_S = engine._estimate(s, now, feed.latest(symbol)[1], delta)
    p_side = eng._norm_cdf(abs(margin) / sd_S)
    engine._shadow_check(s, -30.0, now, mhat=mhat + 1.0, margin=margin, sd_S=sd_S,
                         p_side=p_side, n_lock=n_lock, lmean=lmean, shat=shat)

    fields = [r[0] for r in store.shadow_rows]
    assert "mhat" in fields, store.shadow_rows
    mrow = next(r for r in store.shadow_rows if r[0] == "mhat")
    assert abs(mrow[3] - (-1.0)) < 1e-9          # diff = new - old = mhat - (mhat+1) = -1
