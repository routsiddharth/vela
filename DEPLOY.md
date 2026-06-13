# Vela — live BTC bot: hosting & go-live runbook

This is the **real-money** path for the BTC bot. It is **off by default**; live
trading only happens when you launch with `VELA_LIVE=1`. ETH stays paper.

> **What live mode does:** at 45s to close, on a gate-ON BTC window, it rests **one
> post-only (maker-only) limit buy** on the favored side for **$5** at the favored
> side's best bid (clamped to `[0.45, 0.99]`), folds real fills into the window,
> cancels any unfilled remainder at 2s to close, and reconciles your **real Kalshi
> balance** at settlement. Guards: kill-switch file, daily-loss halt, open-notional
> cap, cancel-all on shutdown.

---

## 0. Prerequisites (do these once)

1. **Funded Kalshi account** with **API trading enabled** and an **RSA API key**
   (key id + PEM private key). The market-data key in `.env` may be read-only —
   confirm it can trade (the demo step below will tell you).
2. `.env` at repo root with:
   ```
   KALSHI_API_KEY=<key id>
   KALSHI_API_SECRET=<PEM private key, \n-escaped or real newlines>
   ```
3. Python **3.13** with: `websockets httpx certifi cryptography python-dotenv`.

---

## 1. Do you need a server? — Yes.

Not for speed (offers persist ~60s; this is not a latency race). For **uptime and
orphaned-order safety**: BTC windows run 24/7, and a laptop that sleeps can leave a
real resting order unmanaged. Run it on a small always-on Linux VPS in **US-East**
(near Kalshi): DigitalOcean / AWS Lightsail / Fly.io, ~$6–12/mo, 1 vCPU / 1GB is
plenty. Your Mac is for development only.

### VPS setup (Ubuntu 24.04, US-East)
```bash
sudo apt update && sudo apt install -y python3.13 python3.13-venv git
git clone <your repo> vela && cd vela
python3.13 -m venv venv && . venv/bin/activate
pip install websockets httpx certifi cryptography python-dotenv
# put your .env at the repo root (scp it; do NOT commit it)
```

### Run it as a service (survives reboots/SSH drops) — `/etc/systemd/system/vela-btc.service`
```ini
[Unit]
Description=Vela BTC live bot
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/home/ubuntu/vela
Environment=VELA_ASSET=BTC
Environment=VELA_LIVE=1
ExecStart=/home/ubuntu/vela/venv/bin/python -m livepaper
Restart=on-failure
RestartSec=5
# auto-restart is safe: startup_reconcile() cancels any stray orders first

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload && sudo systemctl enable --now vela-btc
journalctl -u vela-btc -f          # live logs
```

---

## 2. Validate on Kalshi DEMO first (real API, fake money)

**Do this before risking a cent.** It exercises the actual order/cancel/fill loop
against Kalshi with no financial risk.

```bash
# from repo root, demo cluster + live orders ON, BTC only
VELA_ASSET=BTC VELA_LIVE=1 VELA_LIVE_DEMO=1 \
  /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 -m livepaper
```
Watch `livepaper/data_btc/run.log` for: `[live] startup balance ...`, then on the
first gate-ON BTC window `[live] REST buy ...`, then either `[live] FILL ...` or
`[live] CANCEL unfilled ...` at close. Confirm in the Kalshi demo UI that orders
appear and cancel. Let it run a few windows. **If you see auth/permission errors,
your API key can't trade — fix that before going live.**

Verify what happened any time:
```bash
VELA_ASSET=BTC python -m livepaper.report          # PnL/balance (reads data_btc/)
sqlite3 livepaper/data_btc/paper.db "SELECT * FROM orders ORDER BY ts_ms DESC LIMIT 10;"
```

---

## 3. Go live (real money, $5/window)

Only after demo looks right:
```bash
# drop VELA_LIVE_DEMO -> prod cluster, real money
VELA_ASSET=BTC VELA_LIVE=1 ./run_btc.sh     # or the systemd service above
```
Start watching closely. Keep the kill switch one command away (below).

---

## 4. Operating it

| action | command |
|---|---|
| **KILL NOW** (cancel all, halt) | `touch livepaper/data_btc/KILL` |
| resume after kill | `rm livepaper/data_btc/KILL` then restart the bot |
| stop cleanly (cancels resting orders) | `kill -INT $(cat livepaper/data_btc/lp.pid)` |
| PnL / balance | `VELA_ASSET=BTC python -m livepaper.report` |
| live decisions/fills | `tail -f livepaper/data_btc/run.log` |
| order audit trail | `sqlite3 livepaper/data_btc/paper.db "SELECT * FROM orders;"` |

The bot **auto-halts** if daily realized PnL hits `-$25` (cancels all, stops
trading). It cancels all resting orders on clean shutdown and on the kill file.

---

## 5. Risk knobs (`livepaper/config.py`, LIVE block)

| knob | default | meaning |
|---|---|---|
| `POSITION_USD` | `5.0` | notional per window |
| `LIVE_MAX_DAILY_LOSS` | `25.0` | halt + cancel-all at this day loss |
| `LIVE_MAX_OPEN_NOTIONAL` | `25.0` | cap on total resting+open exposure |
| `LIVE_CANCEL_BEFORE_CLOSE` | `2` | cancel unfilled remainder at this sec-to-close |
| `LIVE_REST_FLOOR / CAP` | `0.45 / 0.99` | never rest a buy outside this band |
| `LIVE_JOIN_BEST_BID` | `True` | rest at favored best bid (the one execution knob to tune) |

**The one real unknown is the maker fill rate** — whether your resting bid actually
catches the panic sells in real time. That is exactly what this live test measures.
Expect P&L ≈ breakeven, not the paper headline. `$5/window` caps each window's
downside to ~$5 while you learn it.
