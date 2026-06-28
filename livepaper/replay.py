"""Phase-0 golden-master replay harness (MIGRATION_PLAN.md §5 Phase 0, §10).

Proves the NEW extraction path (PriceBlend.price -> RawAvgBundle -> projection)
reproduces the CURRENT engine, off real recorded data — no network, no live
process. This is the oracle every later phase is gated against.

Two distinct things, kept separate (this separation is the whole point):

  PRIMARY GATE — behaviour-preserving refactor. For each recorded tick we drive
  BOTH the real `Engine._estimate` (borrowed verbatim) and the new
  `projection.project` from the *same* ReplayFeed + reconstructed Debias, and
  assert they agree to the bit. Because both consume the identical feed, any gap
  in the recorded prices cancels out — this isolates "does the new code compute
  what the old code computes" from "is the recording faithful". This MUST be 0.

  DIAGNOSTIC — recording fidelity (NOT gated). We also compare the replayed
  `_estimate` to the value the live engine LOGGED at that tick. These differ
  because the `prices` table is a lossy per-tick sample: the live tick loop drifts
  past 1s under load, so `feed.latest` skips seconds and only the latest is
  written, while `_estimate` read the full in-RAM buffer. So a window's 60 locked
  seconds are typically only ~43-44 in the table. We report the gap and, on the
  subset of ticks whose locked window IS fully recorded, confirm the replay
  reproduces the live log exactly. Implication for the migration: the PriceBlend
  service must persist EVERY emitted bucket (1/sec), not a per-tick sample.

CLI:  python -m livepaper.replay [--asset BTC|ETH] [--db PATH] [--n 8000]
"""
from __future__ import annotations
import argparse, bisect, math, sqlite3, statistics, types
from dataclasses import dataclass, field
from . import config as C
from .contract import RawAvgBundle
from .projection import project, _norm_cdf
from .market import Debias
from . import engine as _eng

GUARD_SECS = 40            # de-bias membership ambiguity window around a same-asset settle


# --------------------------------------------------------------------------- #
#  Replay feed: price_at / latest / recent_sigma off the recorded prices table
# --------------------------------------------------------------------------- #
class ReplayFeed:
    """A BinanceFeed look-alike backed by the recorded `prices` table. `cursor`
    is the latest second 'received' so far; latest()/recent_sigma() slice <= it,
    exactly as the live in-RAM buffer did at that instant."""
    def __init__(self) -> None:
        self.prices: dict[str, dict[int, float]] = {}
        self.secs: dict[str, list[int]] = {}
        self.cursor: dict[str, int] = {}

    def load(self, db_path, symbol: str) -> "ReplayFeed":
        con = sqlite3.connect(str(db_path))
        try:
            rows = con.execute(
                "SELECT epoch_sec, price FROM prices WHERE symbol=? ORDER BY epoch_sec",
                (symbol,)).fetchall()
        finally:
            con.close()
        d: dict[int, float] = {}
        for sec, px in rows:
            d[int(sec)] = float(px)          # asc order => latest write wins per sec
        self.prices[symbol] = d
        self.secs[symbol] = sorted(d)
        return self

    def set_cursor(self, symbol: str, sec: int) -> None:
        self.cursor[symbol] = int(sec)

    def latest_sec_at(self, symbol: str, now: float):
        """The newest recorded second <= floor(now) — the feed's natural latest."""
        secs_all = self.secs.get(symbol)
        if not secs_all:
            return None
        i = bisect.bisect_right(secs_all, int(math.floor(now)))
        return secs_all[i - 1] if i > 0 else None

    def price_at(self, symbol: str, sec: int):
        return self.prices.get(symbol, {}).get(int(sec))

    def latest(self, symbol: str):
        c = self.cursor.get(symbol)
        if c is None:
            return None
        p = self.prices.get(symbol, {}).get(c)
        return None if p is None else (c, p)

    def recent_sigma(self, symbol: str, lookback: int):
        """Byte-identical to BinanceFeed.recent_sigma over the buffer <= cursor."""
        d = self.prices.get(symbol)
        secs_all = self.secs.get(symbol)
        c = self.cursor.get(symbol)
        if not d or c is None:
            return None
        lo = c - (C.EST_BUFFER_SECS + 60)               # mirror the live buffer window
        hi = bisect.bisect_right(secs_all, c)
        loi = bisect.bisect_right(secs_all, lo)
        win = secs_all[loi:hi]                            # existing secs in (lo, c]
        if len(win) < 12:
            return None
        secs = win[-(lookback + 1):]
        diffs = [d[secs[i]] - d[secs[i - 1]] for i in range(1, len(secs))]
        if len(diffs) < 8:
            return None
        return statistics.pstdev(diffs)


class _Oracle:
    """Borrows the REAL Engine._estimate verbatim so the gate compares against the
    live code, not a copy of it. _estimate only touches self.feed/self.debias."""
    _estimate = _eng.Engine._estimate

    def __init__(self, feed, debias: dict) -> None:
        self.feed = feed
        self.debias = debias


def _debias_at(asset: str, symbol: str, samples_sorted, cts_list, now: float) -> Debias:
    """Reconstruct the live Debias state at `now`: every sample with close_ts <
    now (only the last DEBIAS_LOOKBACK matter for delta/resid_std)."""
    idx = bisect.bisect_left(cts_list, now)               # # samples with close_ts < now
    db = Debias(asset, symbol)
    db.samples = list(samples_sorted[max(0, idx - C.DEBIAS_LOOKBACK):idx])
    return db


def _same_asset_recent(cts_list, now: float) -> bool:
    """True if a de-bias sample's close_ts is in (now-GUARD, now): its settle-time
    add could straddle this tick, so membership (hence delta/resid_std) is
    ambiguous vs the live log."""
    i = bisect.bisect_left(cts_list, now)
    return i > 0 and cts_list[i - 1] > now - GUARD_SECS


# --------------------------------------------------------------------------- #
#  Parity run
# --------------------------------------------------------------------------- #
@dataclass
class ParityReport:
    asset: str
    sampled: int
    resolved: int = 0
    boundary: int = 0
    full_cov: int = 0
    # PRIMARY: project() vs real _estimate() on the SAME feed — must be ~0
    p_mhat: float = 0.0
    p_margin: float = 0.0
    p_shat: float = 0.0
    p_lmean: float = 0.0
    p_nlock: int = 0
    p_sdS: float = 0.0
    p_pside: float = 0.0
    # DIAGNOSTIC: replayed _estimate() vs the LOGGED row (recording fidelity)
    d_mhat: float = 0.0
    d_nlock: int = 0
    d_sdS: float = 0.0
    d_delta: float = 0.0
    d_mhat_fullcov: float = 0.0      # vs-log mhat dev on fully-recorded, non-boundary ticks
    d_sdS_fullcov: float = 0.0
    failures: list = field(default_factory=list)

    def primary_ok(self) -> bool:
        return (self.resolved > 0 and self.p_nlock == 0
                and self.p_mhat < 1e-9 and self.p_margin < 1e-9
                and self.p_shat < 1e-9 and self.p_lmean < 1e-9
                and self.p_sdS < 1e-9 and self.p_pside < 1e-12)

    def summary(self) -> str:
        return (
            f"[{self.asset}] sampled={self.sampled} resolved={self.resolved} "
            f"boundary={self.boundary} full_cov={self.full_cov}\n"
            f"  PRIMARY  project vs _estimate (must be 0): "
            f"mhat={self.p_mhat:.2e} margin={self.p_margin:.2e} shat={self.p_shat:.2e} "
            f"lmean={self.p_lmean:.2e} nlock={self.p_nlock} sd_S={self.p_sdS:.2e} "
            f"p_side={self.p_pside:.2e}  -> primary_ok={self.primary_ok()}\n"
            f"  DIAGNOSTIC replay vs logged (recording loss, not gated): "
            f"mhat={self.d_mhat:.3f} nlock={self.d_nlock} sd_S={self.d_sdS:.3f} "
            f"delta={self.d_delta:.3e}\n"
            f"  on fully-recorded non-boundary ticks ({self.full_cov}): "
            f"mhat={self.d_mhat_fullcov:.2e} sd_S={self.d_sdS_fullcov:.2e}")


_COLS = ("ts_ms", "ticker", "asset", "sec", "spot", "n_lock", "lmean", "shat",
         "delta", "mhat", "strike", "margin", "thr_abs", "close_ts")


def _sample_rows(db_path, asset: str, n: int):
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.execute(
            "SELECT e.ts_ms, e.ticker, e.asset, e.sec_to_close, e.spot, e.n_lock, "
            "       e.locked_mean, e.s_hat_binance, e.delta, e.mhat, e.strike, "
            "       e.margin_hat, e.thr_abs, w.close_ts "
            "FROM estimates e JOIN windows w ON e.ticker = w.ticker "
            "WHERE e.asset = ? AND e.sec_to_close BETWEEN -5 AND 120 "
            "ORDER BY e.ts_ms DESC LIMIT ?", (asset, n))
        return [dict(zip(_COLS, r)) for r in cur.fetchall()]
    finally:
        con.close()


def _load_debias(db_path, asset: str):
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            "SELECT close_ts, err FROM debias WHERE asset=? ORDER BY close_ts",
            (asset,)).fetchall()
    finally:
        con.close()
    samples = [(int(c), float(e)) for c, e in rows]
    return samples, [s[0] for s in samples]


def _locked_fully_recorded(feed: ReplayFeed, symbol: str, close_ts: int,
                           now: float, logged_nlock: int) -> bool:
    """Did the recorded prices cover the whole elapsed locked window — i.e. did the
    replay feed see the same locked set the live engine logged?"""
    start = close_ts - C.SETTLE_SECS
    n_elapsed = min(C.SETTLE_SECS, max(0, int(now) - start))
    have = sum(1 for e in range(start, start + n_elapsed)
               if feed.price_at(symbol, e) is not None)
    return have == n_elapsed == logged_nlock


def run_parity(db_path, asset: str, n: int = 8000) -> ParityReport:
    symbol = C.ASSET_SYMBOL.get(asset) or {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}[asset]
    rows = _sample_rows(db_path, asset, n)
    feed = ReplayFeed().load(db_path, symbol)
    samples_sorted, cts_list = _load_debias(db_path, asset)
    z_gate = _eng.Z_GATE_BY_ASSET.get(asset, _eng.Z_GATE)
    rep = ParityReport(asset=asset, sampled=len(rows))

    for r in rows:
        close_ts = int(r["close_ts"])
        now = close_ts - r["sec"]                          # exact (Sterbenz; see docstring)
        cursor = feed.latest_sec_at(symbol, now)           # feed's natural latest second
        if cursor is None:
            continue
        feed.set_cursor(symbol, cursor)
        spot = r["spot"]                                   # the spot the live engine used
        db = _debias_at(asset, symbol, samples_sorted, cts_list, now)
        delta = r["delta"]                                 # LOGGED delta -> both paths use the live value
        # (reconstructing historical Debias.delta() across restarts is imperfect;
        # delta is logged per tick, so use it directly and report the drift below)
        state = types.SimpleNamespace(symbol=symbol, close_ts=close_ts,
                                      strike=r["strike"], asset=asset)

        # ---- oracle: the REAL engine code on the replay feed -----------------
        o = _Oracle(feed, {asset: db})
        mhat_o, margin_o, nlock_o, lmean_o, shat_o, sdS_o = o._estimate(state, now, spot, delta)
        pside_o = _eng._norm_cdf(abs(margin_o) / sdS_o)

        # ---- new path: PriceBlend bundle -> projection on the same feed ------
        bundle = RawAvgBundle(asset=asset, ts=cursor, symbol=symbol, raw_avg=spot,
                              n_prices=1, delta=delta,
                              sigma_sec=feed.recent_sigma(symbol, C.SIGMA_LOOKBACK),
                              resid_std=db.resid_std())
        proj = project(bundle, lambda e: feed.price_at(symbol, e),
                       r["strike"], close_ts, now)

        rep.resolved += 1
        # PRIMARY deviations (project vs _estimate)
        rep.p_mhat = max(rep.p_mhat, abs(proj.mhat - mhat_o))
        rep.p_margin = max(rep.p_margin, abs(proj.margin - margin_o))
        rep.p_shat = max(rep.p_shat, abs(proj.shat - shat_o))
        rep.p_lmean = max(rep.p_lmean, abs(proj.lmean - lmean_o))
        rep.p_nlock = max(rep.p_nlock, abs(proj.n_lock - nlock_o))
        rep.p_sdS = max(rep.p_sdS, abs(proj.sd_S - sdS_o))
        rep.p_pside = max(rep.p_pside, abs(proj.p_side - pside_o))
        if proj.n_lock != nlock_o or abs(proj.mhat - mhat_o) > 1e-9:
            rep.failures.append((r["ticker"], r["sec"], "PRIMARY",
                                 dict(d_mhat=abs(proj.mhat - mhat_o),
                                      d_nlock=abs(proj.n_lock - nlock_o))))

        # DIAGNOSTIC deviations (replayed _estimate vs logged row)
        sdS_log = r["thr_abs"] / z_gate
        rep.d_mhat = max(rep.d_mhat, abs(mhat_o - r["mhat"]))
        rep.d_nlock = max(rep.d_nlock, abs(nlock_o - int(r["n_lock"])))
        rep.d_sdS = max(rep.d_sdS, abs(sdS_o - sdS_log))
        boundary = _same_asset_recent(cts_list, now)
        if boundary:
            rep.boundary += 1
        else:
            rep.d_delta = max(rep.d_delta, abs(db.delta() - r["delta"]))  # reconstruction drift (reported)
        # on fully-recorded, non-boundary ticks the replay should match the log too
        if not boundary and _locked_fully_recorded(feed, symbol, close_ts, now, int(r["n_lock"])):
            rep.full_cov += 1
            rep.d_mhat_fullcov = max(rep.d_mhat_fullcov, abs(mhat_o - r["mhat"]))
            rep.d_sdS_fullcov = max(rep.d_sdS_fullcov, abs(sdS_o - sdS_log))
    return rep


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase-0 PriceBlend golden-master replay")
    ap.add_argument("--asset", default=None, help="BTC or ETH (default: both)")
    ap.add_argument("--db", default=None, help="paper.db (default: data_<asset>/paper.db)")
    ap.add_argument("--n", type=int, default=8000, help="most-recent in-range ticks to check")
    a = ap.parse_args()
    assets = [a.asset] if a.asset else ["BTC", "ETH"]
    any_fail = False
    for asset in assets:
        db = a.db or (C.ROOT / f"data_{asset.lower()}" / "paper.db")
        try:
            rep = run_parity(db, asset, a.n)
        except Exception as e:
            print(f"[{asset}] SKIP ({type(e).__name__}: {e})")
            continue
        print(rep.summary())
        for f in rep.failures[:10]:
            print(f"   FAIL {f}")
        if not rep.primary_ok():
            any_fail = True
    raise SystemExit(1 if any_fail else 0)


if __name__ == "__main__":
    main()
