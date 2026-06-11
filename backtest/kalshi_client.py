"""Minimal signed Kalshi REST client for the BTC backtest.

Reuses the repo's confirmed RSA-PSS signing scheme (see
ingest/src/simplex_ingest/kalshi/auth.py). Self-contained so the backtest
subagents can import it without the full ingest package.
"""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import load_dotenv

_ENV = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV)

# prod host for BTC markets (these markets live on the elections/prod cluster)
PROD = "https://api.elections.kalshi.com/trade-api/v2"


def _load_key():
    raw = os.environ["KALSHI_API_SECRET"]
    # may be inline PEM with literal \n or real newlines
    if "-----BEGIN" not in raw:
        raise SystemExit("KALSHI_API_SECRET does not look like a PEM")
    pem = raw.replace("\\n", "\n").encode()
    return serialization.load_pem_private_key(pem, password=None)


class Kalshi:
    def __init__(self, base: str = PROD):
        self.base = base.rstrip("/")
        self.key_id = os.environ["KALSHI_API_KEY"].strip()
        self.key = _load_key()
        self.c = httpx.Client(timeout=30.0)

    def _headers(self, method: str, path: str) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        msg = f"{ts}{method.upper()}{path}".encode()
        sig = self.key.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        }

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        from urllib.parse import urlsplit
        url = f"{self.base}{path}"
        sign_path = urlsplit(url).path
        for attempt in range(6):
            h = self._headers("GET", sign_path)
            r = self.c.get(url, params=params, headers=h)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(0.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        r.raise_for_status()
        return r.json()


if __name__ == "__main__":
    k = Kalshi()
    # 1) exchange status (sanity / auth check)
    try:
        st = k.get("/exchange/status")
        print("exchange status:", st)
    except Exception as e:
        print("status err:", e)
    # 2) the BTC 15-min series meta
    try:
        s = k.get("/series/KXBTC15M")
        print("series keys:", list(s.get("series", s).keys()) if isinstance(s, dict) else type(s))
        print("series:", s)
    except Exception as e:
        print("series err:", e)
