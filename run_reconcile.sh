#!/usr/bin/env bash
# Reconcile the Supabase mirror against the local shared portfolio.db.
# Local SQLite is the source of truth; this heals anything the live mirror dropped.
# Intended for cron, every 6h:  0 */6 * * * /Users/siddharthrout/Desktop/Projects/vela/run_reconcile.sh
set -euo pipefail
cd "$(dirname "$0")"
FW="/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13"
LOG="livepaper/data_shared/reconcile.log"
mkdir -p livepaper/data_shared
{
  printf '%s ' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  "$FW" -m livepaper.supabase_reconcile
} >> "$LOG" 2>&1
