"""Shared live risk ledger for split BTC/ETH livepaper processes.

This is intentionally separate from the Kalshi cash balance. The real account may
hold more cash than we want this strategy to risk, so live sizing reads this
ledger and settlement PnL updates it exactly once per pathway/window.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from ..supabase_sync import SupabaseMirror


_SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio(
  id INTEGER PRIMARY KEY CHECK (id = 1),
  balance REAL NOT NULL,
  updated_ts_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settlements(
  key TEXT PRIMARY KEY,
  ticker TEXT NOT NULL,
  kind TEXT NOT NULL,
  asset TEXT,
  net REAL NOT NULL,
  ts_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS events(
  ts_ms INTEGER NOT NULL,
  kind TEXT NOT NULL,
  detail TEXT NOT NULL
);
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


class SharedPortfolio:
    def __init__(self, path: Path, default_balance: float, log=None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=30.0, isolation_level=None,
                                   check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(_SCHEMA)
        self.db.execute(
            "INSERT OR IGNORE INTO portfolio(id,balance,updated_ts_ms) VALUES(1,?,?)",
            (float(default_balance), _now_ms()),
        )
        # Best-effort live cloud mirror (no-op unless VELA_SUPABASE_SYNC=1 + keys set).
        self.mirror = SupabaseMirror(log)

    def balance(self) -> float:
        row = self.db.execute("SELECT balance FROM portfolio WHERE id=1").fetchone()
        return float(row[0]) if row else 0.0

    def reset(self, balance: float, reason: str = "manual reset") -> float:
        ts = _now_ms()
        detail = f"{reason}: balance={float(balance):.2f}"
        with self.db:
            self.db.execute(
                "UPDATE portfolio SET balance=?, updated_ts_ms=? WHERE id=1",
                (float(balance), ts),
            )
            self.db.execute("DELETE FROM settlements")
            self.db.execute(
                "INSERT INTO events(ts_ms,kind,detail) VALUES(?,?,?)",
                (ts, "reset", detail),
            )
        self.mirror.push_reset(
            {"id": 1, "balance": float(balance), "updated_ts_ms": ts},
            {"ts_ms": ts, "kind": "reset", "detail": detail},
        )
        return float(balance)

    def apply_settlement(self, key: str, ticker: str, kind: str,
                         asset: str | None, net: float) -> float:
        """Apply realized PnL once. Replays after restart return current balance."""
        ts = _now_ms()
        with self.db:
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO settlements(key,ticker,kind,asset,net,ts_ms) "
                "VALUES(?,?,?,?,?,?)",
                (key, ticker, kind, asset, float(net), ts),
            ).rowcount
            if inserted:
                self.db.execute(
                    "UPDATE portfolio SET balance=balance+?, updated_ts_ms=? WHERE id=1",
                    (float(net), ts),
                )
                self.db.execute(
                    "INSERT INTO events(ts_ms,kind,detail) VALUES(?,?,?)",
                    (ts, "settlement", f"{key} net={float(net):+.4f}"),
                )
        bal = self.balance()
        if inserted:  # only mirror the once-per-window real PnL, not idempotent replays
            self.mirror.push_settlement({
                "key": key, "ticker": ticker, "kind": kind,
                "asset": asset, "net": float(net), "ts_ms": ts,
            })
            self.mirror.push_portfolio({"id": 1, "balance": bal, "updated_ts_ms": ts})
        return bal

    def close(self) -> None:
        self.mirror.close()
        self.db.close()
