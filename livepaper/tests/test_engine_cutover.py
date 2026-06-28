"""Engine-level behaviour preservation after the PriceBlend cutover.

A real engine.tick() (paper mode) over a ReplayFeed must log exactly the decision
the FROZEN pre-migration _estimate would have produced — mhat/margin/sd_S/p_side,
the bet side, and the gate. This is the engine-level companion to the projection
golden master: it proves the cutover (decide off PriceBlend+projection, no
_estimate) changed where the numbers come from, not the numbers.
"""
from __future__ import annotations
import sqlite3
import pytest

from livepaper import config as C
from livepaper.trading import engine as eng
from livepaper.trading import Engine, MarketState
from livepaper.priceblend import Debias
from livepaper.replay import ReplayFeed, _legacy_estimate, _legacy_norm_cdf


class _CapStore:
    """Captures the rows the tick writes; everything else is a no-op."""
    def __init__(self):
        self.est = None
        self.orc = None
        self.decision = None
    def price(self, *a): pass
    def book(self, *a): pass
    def estimate(self, t, asset, sec, spot, n_lock, lmean, shat, delta, mhat,
                 strike, margin, thr_abs, bet, gate, decided):
        self.est = dict(mhat=mhat, margin=margin, n_lock=n_lock, lmean=lmean,
                        shat=shat, delta=delta, bet=bet, gate=gate, decided=decided)
    def oracle(self, t, asset, sec, spot_sec, sigma_sec, resid_std, sd_S, p_side):
        self.orc = dict(sd_S=sd_S, p_side=p_side, sigma_sec=sigma_sec, resid_std=resid_std)
    def event(self, kind, detail=""):
        if kind == "decision":
            self.decision = detail


def test_engine_tick_matches_frozen_estimate(monkeypatch):
    db = C.ROOT / "data_btc" / "paper.db"
    if not db.exists():
        pytest.skip("no recorded BTC data")
    con = sqlite3.connect(str(db))
    row = con.execute("SELECT ticker, close_ts, strike FROM windows WHERE asset='BTC' "
                      "ORDER BY close_ts DESC LIMIT 1").fetchone()
    asset, symbol = "BTC", "BTCUSDT"
    now = float(row[1] - 30)                                # 30s to close => decides
    samples = con.execute("SELECT close_ts, err FROM debias WHERE asset='BTC' "
                          "AND close_ts < ? ORDER BY close_ts DESC LIMIT ?",
                          (now, C.DEBIAS_LOOKBACK)).fetchall()
    con.close()
    ticker, close_ts, strike = row[0], int(row[1]), float(row[2])

    feed = ReplayFeed().load(db, symbol)
    feed.set_cursor(symbol, feed.latest_sec_at(symbol, now))
    spot = feed.latest(symbol)[1]
    db_obj = Debias("BTC", symbol)
    db_obj.samples = sorted((int(c), float(e)) for c, e in samples)

    # frozen pre-migration oracle for this exact (feed, debias, now, strike)
    delta = db_obj.delta()
    mhat_o, margin_o, nlock_o, lmean_o, shat_o, sdS_o = _legacy_estimate(
        feed, db_obj, symbol, close_ts, strike, now, spot, delta)
    pside_o = _legacy_norm_cdf(abs(margin_o) / sdS_o)
    gate_o = pside_o >= C.P_SIDE_MIN_BY_ASSET.get("BTC", C.P_SIDE_MIN)

    # run the REAL cutover engine
    store = _CapStore()
    monkeypatch.setattr(eng.time, "time", lambda: now)
    engine = Engine(store, feed, disc=None, debias={"BTC": db_obj},
                    market_meta={}, log=lambda *a, **k: None)
    engine.states[ticker] = MarketState(ticker, close_ts, strike, "BTC", symbol, "KXBTC15M")
    engine.tick()

    assert store.est is not None and store.orc is not None
    assert store.est["mhat"] == mhat_o
    assert store.est["margin"] == margin_o
    assert store.est["n_lock"] == nlock_o
    assert store.est["lmean"] == lmean_o
    assert store.est["shat"] == shat_o
    assert store.est["delta"] == delta
    assert store.orc["sd_S"] == sdS_o
    assert store.orc["p_side"] == pside_o
    # decision: bet side + gate match the frozen oracle
    assert store.est["decided"] is True
    assert store.est["bet"] == ("yes" if margin_o > 0 else "no")
    assert store.est["gate"] == gate_o
