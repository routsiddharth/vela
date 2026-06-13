#!/usr/bin/env bash
# ETH-only livepaper bot -> livepaper/data_eth/  (gate 0.98)
set -euo pipefail
cd "$(dirname "$0")"
FW="/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13"
DATA="livepaper/data_eth"
mkdir -p "$DATA"
if [ -f "$DATA/lp.pid" ] && kill -0 "$(cat "$DATA/lp.pid")" 2>/dev/null; then
  echo "ETH bot already running (PID $(cat "$DATA/lp.pid")). Stop it first."; exit 1
fi
VELA_ASSET=ETH nohup "$FW" -m livepaper > "$DATA/console.out" 2>&1 &
echo $! > "$DATA/lp.pid"
echo "ETH bot started — PID $(cat "$DATA/lp.pid"), data -> $DATA/"
