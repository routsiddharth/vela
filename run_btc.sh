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
VELA_ASSET=BTC VELA_LIVE=1 VELA_STRONG_TAKE=1 VELA_SUPABASE_SYNC=1 "$FW" -c '
import os, subprocess, sys
fw, data = sys.argv[1], sys.argv[2]
out = open(os.path.join(data, "console.out"), "ab", buffering=0)
p = subprocess.Popen([fw, "-m", "livepaper"], stdin=subprocess.DEVNULL,
                     stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
with open(os.path.join(data, "lp.pid"), "w") as f:
    f.write(f"{p.pid}\n")
' "$FW" "$DATA"
echo "BTC bot started — PID $(cat "$DATA/lp.pid"), data -> $DATA/"
