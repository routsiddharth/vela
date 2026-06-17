#!/usr/bin/env bash
# Vela cloud bootstrap — run ONCE on a fresh Ubuntu 22.04/24.04 box.
# Assumes the repo is already at ~/vela and ~/vela/.env is in place (scp'd, never committed).
# Sets up Python venv + deps + a systemd service for the LIVE BTC bot.
set -euo pipefail

REPO="${REPO:-$HOME/vela}"
USER_NAME="$(whoami)"
cd "$REPO"

echo "== installing system deps =="
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip git sqlite3

# require Python >= 3.11
PYV=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
echo "python3 = $PYV"
python3 - <<'PY'
import sys
assert sys.version_info[:2] >= (3, 11), "need Python >= 3.11"
print("python version OK")
PY

echo "== venv + deps =="
python3 -m venv venv
. venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

if [ ! -f .env ]; then
  echo "!! .env MISSING at $REPO/.env — scp it over before starting (KALSHI_API_KEY/SECRET). Aborting service install."
  exit 1
fi

echo "== installing systemd service vela-btc =="
sudo tee /etc/systemd/system/vela-btc.service >/dev/null <<UNIT
[Unit]
Description=Vela BTC live bot
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$REPO
Environment=VELA_ASSET=BTC
Environment=VELA_LIVE=1
Environment=VELA_STRONG_TAKE=1
ExecStart=$REPO/venv/bin/python -m livepaper
Restart=on-failure
RestartSec=5
User=$USER_NAME

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
echo
echo "== READY =="
echo "Start LIVE:   sudo systemctl enable --now vela-btc"
echo "Live logs:    journalctl -u vela-btc -f"
echo "KILL switch:  touch $REPO/livepaper/data_btc/KILL"
echo "Stop:         sudo systemctl stop vela-btc"
echo "Report:       VELA_ASSET=BTC $REPO/venv/bin/python -m livepaper.report"
