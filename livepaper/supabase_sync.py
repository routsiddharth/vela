"""Best-effort live mirror of the shared portfolio ledger to Supabase (PostgREST).

The local SQLite ledger (``data_shared/portfolio.db``) stays the source of truth.
This module pushes a copy to Supabase so the state is visible/queryable in the
cloud. Two write paths:

  1. Live  — ``SupabaseMirror`` runs on a daemon thread and pushes each settlement /
     balance change as it happens. Every push is retried then swallowed on failure;
     trading NEVER blocks on the network, and a dropped push is repaired by (2).
  2. Reconcile — ``supabase_reconcile.py`` (cron, every 6h) makes the cloud tables
     match the local DB exactly, healing anything the live path dropped, plus resets.

Enabled only when ``VELA_SUPABASE_SYNC=1`` AND ``SUPABASE_URL`` + ``SUPABASE_SECRET_KEY``
are set. The live run scripts (run_btc.sh / run_eth.sh) set the flag; tests and the
manual reset one-liner do not, so they never touch the network.
"""
from __future__ import annotations

import os
import queue
import threading
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _env() -> tuple[str | None, str | None]:
    return os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SECRET_KEY")


def make_client(timeout: float = 30.0) -> tuple[httpx.Client, str]:
    """httpx client + PostgREST base url, authed with the service (secret) key."""
    url, key = _env()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SECRET_KEY not set in env/.env")
    base = url.rstrip("/") + "/rest/v1"
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json"}
    return httpx.Client(timeout=timeout, headers=headers), base


def upsert(client: httpx.Client, base: str, table: str, rows: list[dict],
           on_conflict: str | None = None, chunk: int = 500) -> None:
    """Idempotent batch upsert (merge-duplicates on the conflict target)."""
    if not rows:
        return
    params = {"on_conflict": on_conflict} if on_conflict else {}
    for i in range(0, len(rows), chunk):
        r = client.post(f"{base}/{table}", params=params,
                        headers={"Prefer": "resolution=merge-duplicates"},
                        json=rows[i:i + chunk])
        r.raise_for_status()


def mirror_enabled() -> bool:
    url, key = _env()
    return os.environ.get("VELA_SUPABASE_SYNC", "") == "1" and bool(url) and bool(key)


class SupabaseMirror:
    """Daemon-thread, fire-and-forget mirror. No-op unless ``mirror_enabled()``."""

    def __init__(self, log=None) -> None:
        self.log = log or (lambda m: None)
        self.enabled = mirror_enabled()
        if not self.enabled:
            return
        self.client, self.base = make_client(timeout=15.0)
        self.q: queue.Queue = queue.Queue(maxsize=10000)
        threading.Thread(target=self._worker, daemon=True).start()
        self.log("[supabase] live mirror enabled")

    # -- producer side (called from the trading loop) -------------------------
    def push_settlement(self, row: dict) -> None:
        self._enqueue(("settlement", row))

    def push_portfolio(self, row: dict) -> None:
        self._enqueue(("portfolio", row))

    def push_reset(self, portfolio_row: dict, event_row: dict) -> None:
        self._enqueue(("reset", (portfolio_row, event_row)))

    def _enqueue(self, op) -> None:
        if not self.enabled:
            return
        try:
            self.q.put_nowait(op)
        except queue.Full:
            self.log("[supabase] queue full, dropping (reconcile will repair)")

    def close(self) -> None:
        if not self.enabled:
            return
        end = time.time() + 5.0
        while self.q.unfinished_tasks and time.time() < end:
            time.sleep(0.1)
        try:
            self.client.close()
        except Exception:
            pass

    # -- consumer side (daemon thread) ----------------------------------------
    def _worker(self) -> None:
        while True:
            kind, payload = self.q.get()
            for attempt in range(5):
                try:
                    self._do(kind, payload)
                    break
                except Exception as e:
                    if attempt == 4:
                        self.log(f"[supabase] drop {kind} after retries: {str(e)[:120]}")
                    else:
                        time.sleep(min(2 ** attempt, 10))
            self.q.task_done()

    def _do(self, kind: str, payload) -> None:
        if kind == "settlement":
            upsert(self.client, self.base, "settlements", [payload], on_conflict="key")
        elif kind == "portfolio":
            upsert(self.client, self.base, "portfolio", [payload], on_conflict="id")
        elif kind == "reset":
            portfolio_row, event_row = payload
            # mirror a local reset: wipe cloud settlements, then set balance + audit
            r = self.client.delete(f"{self.base}/settlements", params={"ts_ms": "gte.0"})
            r.raise_for_status()
            upsert(self.client, self.base, "portfolio", [portfolio_row], on_conflict="id")
            upsert(self.client, self.base, "events", [event_row])
