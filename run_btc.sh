#!/usr/bin/env bash
# BTC-only livepaper bot -> livepaper/data_btc/  (gate 0.84)
set -euo pipefail
cd "$(dirname "$0")"
FW="/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13"
DATA="livepaper/data_btc"
mkdir -p "$DATA"
if [ -f "$DATA/lp.pid" ] && kill -0 "$(cat "$DATA/lp.pid")" 2>/dev/null; then
  echo "BTC bot already running (PID $(cat "$DATA/lp.pid")). Stop it first."; exit 1
fi
VELA_ASSET=BTC nohup "$FW" -m livepaper > "$DATA/console.out" 2>&1 &
echo $! > "$DATA/lp.pid"
echo "BTC bot started — PID $(cat "$DATA/lp.pid"), data -> $DATA/"
