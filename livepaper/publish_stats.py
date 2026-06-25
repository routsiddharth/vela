"""Compute public stats and PUT them to Cloudflare KV.

Runs on a timer on the Mac (launchd, see deploy/com.vela.stats.plist). Computes the
stats payload from the read-only ledger and pushes it to a Cloudflare KV key; a
Worker route then serves it to the website. The Mac only ever pushes OUT — no
inbound port, and the trading box / Kalshi keys are never exposed.

Required env (put in a file the launchd job sources, NOT committed):
    CF_ACCOUNT_ID     Cloudflare account id
    CF_KV_NAMESPACE   KV namespace id
    CF_KV_KEY         key to write (e.g. "vela-stats")
    CF_API_TOKEN      API token with "Workers KV Storage:Edit" on this account

    python -m livepaper.publish_stats          # compute + push
    python -m livepaper.publish_stats --dry     # compute + print, no push
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx

from . import config as C
from .stats_export import compute

_KV_URL = (
    "https://api.cloudflare.com/client/v4/accounts/{acct}"
    "/storage/kv/namespaces/{ns}/values/{key}"
)


def _require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.stderr.write(f"missing env {name}\n")
        raise SystemExit(2)
    return v


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Push public stats to Cloudflare KV.")
    ap.add_argument("--dry", action="store_true", help="compute + print, do not push")
    args = ap.parse_args(argv)

    payload = compute(C.SHARED_PORTFOLIO_DB, updated_ms=int(time.time() * 1000))
    body = json.dumps(payload, separators=(",", ":"))

    if args.dry:
        sys.stdout.write(body + "\n")
        return 0

    url = _KV_URL.format(
        acct=_require("CF_ACCOUNT_ID"),
        ns=_require("CF_KV_NAMESPACE"),
        key=_require("CF_KV_KEY"),
    )
    r = httpx.put(
        url,
        headers={
            "Authorization": f"Bearer {_require('CF_API_TOKEN')}",
            "Content-Type": "application/json",
        },
        content=body,
        timeout=15.0,
    )
    if r.status_code >= 300:
        sys.stderr.write(f"KV PUT failed {r.status_code}: {r.text}\n")
        return 1
    sys.stderr.write(
        f"pushed stats: net={payload['summary']['total_net']} "
        f"({payload['summary']['return_pct']}%) at {payload['updated_ms']}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
