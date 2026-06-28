"""SQLite store + raw JSONL dump. One file, queryable live with `sqlite3`.

All writes happen on the single asyncio event-loop thread (every feed/callback
runs there), so no locking is needed. WAL mode lets you `sqlite3 paper.db` and
run reports while the trader is still writing.

Tables:
  btc_prices  — one row/sec: the live Binance 1s close
  book_snaps  — one row/sec/market: top-of-book + depth + full book JSON
  estimates   — one row/sec/market: the causal mhat / margin / gate state
  trades      — every Kalshi print on a tracked market (the panic flow)
  fills       — every paper fill we took (entry price, qty, fee, why)
  windows     — one row per settled market: realized PnL + running balance
  debias      — per settled window: binance_avg60 - true_settle (the bias sample)
  events      — lifecycle / reconnect / decision log
"""
from __future__ import annotations
import json, sqlite3, threading, time
from pathlib import Path
from . import config as C

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices(
  ts_ms INTEGER, symbol TEXT, epoch_sec INTEGER, price REAL);
CREATE TABLE IF NOT EXISTS book_snaps(
  ts_ms INTEGER, ticker TEXT, sec_to_close REAL,
  best_yes_bid REAL, yes_bid_sz REAL, best_no_bid REAL, no_bid_sz REAL,
  yes_ask REAL, no_ask REAL, depth_yes REAL, depth_no REAL, book_json TEXT);
CREATE TABLE IF NOT EXISTS estimates(
  ts_ms INTEGER, ticker TEXT, asset TEXT, sec_to_close REAL, spot REAL, n_lock INTEGER,
  locked_mean REAL, s_hat_binance REAL, delta REAL, mhat REAL, strike REAL,
  margin_hat REAL, thr_abs REAL, bet_side TEXT, gate_active INTEGER, decided INTEGER);
CREATE TABLE IF NOT EXISTS est_oracle(
  ts_ms INTEGER, ticker TEXT, asset TEXT, sec_to_close REAL, spot_sec INTEGER,
  sigma_sec REAL, resid_std REAL, sd_S REAL, p_side REAL);
CREATE TABLE IF NOT EXISTS shadow_diff(
  ts_ms INTEGER, ticker TEXT, asset TEXT, sec_to_close REAL,
  field TEXT, old REAL, new REAL, diff REAL);
CREATE TABLE IF NOT EXISTS trades(
  ts_ms INTEGER, ticker TEXT, sec_to_close REAL, yes_price REAL, no_price REAL,
  size REAL, taker_side TEXT);
CREATE TABLE IF NOT EXISTS fills(
  ts_ms INTEGER, ticker TEXT, sec_to_close REAL, bet_side TEXT, price REAL,
  qty REAL, fee REAL, cost REAL, margin_hat REAL, reason TEXT);
CREATE TABLE IF NOT EXISTS windows(
  ticker TEXT PRIMARY KEY, asset TEXT, series TEXT, close_ts INTEGER, strike REAL,
  true_settle REAL, result TEXT, decision_margin_hat REAL, bet_side TEXT,
  gate_active INTEGER, n_fills INTEGER, total_qty REAL, avg_px REAL, gross_pnl REAL,
  fees REAL, net_pnl REAL, won INTEGER, balance_after REAL);
CREATE TABLE IF NOT EXISTS debias(
  ticker TEXT PRIMARY KEY, asset TEXT, close_ts INTEGER, binance_avg60 REAL,
  true_settle REAL, err REAL);
CREATE TABLE IF NOT EXISTS orders(
  ts_ms INTEGER, ticker TEXT, action TEXT, client_order_id TEXT, order_id TEXT,
  side TEXT, price REAL, count INTEGER, status TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS events(ts_ms INTEGER, kind TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_book ON book_snaps(ticker, ts_ms);
CREATE INDEX IF NOT EXISTS ix_est ON estimates(ticker, ts_ms);
CREATE INDEX IF NOT EXISTS ix_oracle ON est_oracle(ticker, ts_ms);
CREATE INDEX IF NOT EXISTS ix_shadow ON shadow_diff(ticker, ts_ms);
CREATE INDEX IF NOT EXISTS ix_tr ON trades(ticker, ts_ms);
CREATE INDEX IF NOT EXISTS ix_px ON prices(symbol, epoch_sec);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


class Store:
    """Thread-safe (one global lock): tick runs on the event loop, while
    discovery/settlement/bootstrap write from `to_thread` workers."""
    def __init__(self) -> None:
        C.DATA.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(C.DB_PATH, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(_SCHEMA)
        self.db.commit()
        self._raw_k = open(C.RAW_KALSHI, "a") if C.RAW_DUMP else None
        self._raw_b = open(C.RAW_BINANCE, "a") if C.RAW_DUMP else None
        self._since_commit = 0

    def _w(self, sql: str, params: tuple, commit: bool = False) -> None:
        with self._lock:
            self.db.execute(sql, params)
            if commit:
                self.db.commit()
                self._since_commit = 0
            else:
                self._since_commit += 1
                if self._since_commit >= 50:
                    self.db.commit()
                    self._since_commit = 0

    # -- raw firehose ---------------------------------------------------------
    def raw_kalshi(self, obj: dict) -> None:
        if self._raw_k is not None:
            with self._lock:
                self._raw_k.write(json.dumps(obj, separators=(",", ":")) + "\n")

    def raw_binance(self, obj: dict) -> None:
        if self._raw_b is not None:
            with self._lock:
                self._raw_b.write(json.dumps(obj, separators=(",", ":")) + "\n")

    # -- inserts --------------------------------------------------------------
    def price(self, symbol: str, epoch_sec: int, price: float) -> None:
        self._w("INSERT INTO prices VALUES(?,?,?,?)", (now_ms(), symbol, epoch_sec, price))

    def book(self, t: str, sec: float, byb, ybs, bnb, nbs, ya, na, dy, dn, book) -> None:
        self._w("INSERT INTO book_snaps VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (now_ms(), t, sec, byb, ybs, bnb, nbs, ya, na, dy, dn,
                 json.dumps(book, separators=(",", ":"))))

    def estimate(self, t, asset, sec, spot, n_lock, lmean, shat, delta, mhat, strike,
                 margin, thr_abs, bet, gate, decided) -> None:
        self._w("INSERT INTO estimates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now_ms(), t, asset, sec, spot, n_lock, lmean, shat, delta, mhat,
                 strike, margin, thr_abs, bet, int(gate), int(decided)))

    def oracle(self, t, asset, sec, spot_sec, sigma_sec, resid_std, sd_S, p_side) -> None:
        """Phase-0 golden-master row: the per-tick (sigma_sec, resid_std, sd_S,
        p_side) + raw-average second behind each `estimate`. Additive — lets later
        migration phases be checked against the exact recorded live oracle without
        re-deriving sd_S from thr_abs/Z_GATE. sigma_sec/resid_std are RAW (NULL
        until warm); sd_S/p_side are the post-fallback values actually used."""
        self._w("INSERT INTO est_oracle VALUES(?,?,?,?,?,?,?,?,?)",
                (now_ms(), t, asset, sec, spot_sec, sigma_sec, resid_std, sd_S, p_side))

    def shadow(self, t, asset, sec, field, old, new, diff) -> None:
        """Phase-1 shadow divergence: the new PriceBlend+projection path produced
        `new` where the live _estimate path produced `old`, off by `diff`. Written
        ONLY when nonzero, so an empty/quiet table == perfect parity. Trading is
        unaffected; this is pure observation (MIGRATION_PLAN.md Phase 1)."""
        self._w("INSERT INTO shadow_diff VALUES(?,?,?,?,?,?,?,?)",
                (now_ms(), t, asset, sec, field, old, new, diff), commit=True)

    def trade(self, t, sec, yp, np_, sz, taker) -> None:
        self._w("INSERT INTO trades VALUES(?,?,?,?,?,?,?)",
                (now_ms(), t, sec, yp, np_, sz, taker))

    def fill(self, t, sec, bet, price, qty, fee, cost, margin, reason) -> None:
        self._w("INSERT INTO fills VALUES(?,?,?,?,?,?,?,?,?,?)",
                (now_ms(), t, sec, bet, price, qty, fee, cost, margin, reason), commit=True)

    def window(self, row: tuple) -> None:
        self._w("INSERT OR REPLACE INTO windows VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                row, commit=True)

    def debias_row(self, ticker, asset, close_ts, b60, settle, err) -> None:
        self._w("INSERT OR REPLACE INTO debias VALUES(?,?,?,?,?,?)",
                (ticker, asset, close_ts, b60, settle, err), commit=True)

    def order(self, ticker, action, client_order_id, order_id, side, price,
              count, status, detail="") -> None:
        self._w("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?)",
                (now_ms(), ticker, action, client_order_id, order_id, side, price,
                 count, status, detail), commit=True)

    def event(self, kind: str, detail: str = "") -> None:
        self._w("INSERT INTO events VALUES(?,?,?)", (now_ms(), kind, detail), commit=True)

    def flush(self) -> None:
        with self._lock:
            self.db.commit()
            self._since_commit = 0
            if self._raw_k:
                self._raw_k.flush()
            if self._raw_b:
                self._raw_b.flush()

    def close(self) -> None:
        self.flush()
        with self._lock:
            self.db.close()
