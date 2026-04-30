#!/usr/bin/env bash
# Stop and remove Daily-Stock LaunchAgents.
# Idempotent — fine to run on a clean system.

set -euo pipefail

TARGET_DIR="${HOME}/Library/LaunchAgents"
AGENTS=(
    "com.dailystock.uvicorn"
    "com.dailystock.moomoo-sync"
    "com.dailystock.breakout-live"
)

for label in "${AGENTS[@]}"; do
    plist="${TARGET_DIR}/${label}.plist"
    if [[ -f "${plist}" ]]; then
        launchctl unload "${plist}" 2>/dev/null || true
        rm -f "${plist}"
        echo "[removed] ${label}"
    else
        echo "[skip]    ${label} (not installed)"
    fi
done

echo
echo "==> done. Logs in logs/ are kept for forensics; delete manually if you want."
