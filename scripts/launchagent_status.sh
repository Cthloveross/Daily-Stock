#!/usr/bin/env bash
# Quick status check for the Daily-Stock LaunchAgents.

set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
LOGS_DIR="${PROJECT_DIR}/logs"

AGENTS=(
    "com.dailystock.uvicorn"
    "com.dailystock.moomoo-sync"
    "com.dailystock.breakout-live"
)

printf "%-30s  %-10s  %-15s  %s\n" "Label" "PID" "Last exit code" "Last log line"
printf "%-30s  %-10s  %-15s  %s\n" "------------------------------" "----------" "---------------" "-------------"

for label in "${AGENTS[@]}"; do
    line=$(launchctl list 2>/dev/null | awk -v lbl="$label" '$3 == lbl {print $1, $2}') || true
    if [[ -z "$line" ]]; then
        printf "%-30s  %-10s  %-15s  %s\n" "$label" "—" "(not loaded)" ""
        continue
    fi
    pid=$(echo "$line" | awk '{print $1}')
    exitcode=$(echo "$line" | awk '{print $2}')

    case "$label" in
        com.dailystock.uvicorn)        log="${LOGS_DIR}/launchagent.uvicorn.err.log" ;;
        com.dailystock.moomoo-sync)    log="${LOGS_DIR}/launchagent.moomoo-sync.err.log" ;;
        com.dailystock.breakout-live)  log="${LOGS_DIR}/launchagent.breakout-live.err.log" ;;
    esac
    last_line=""
    if [[ -f "$log" ]]; then
        last_line=$(tail -n 1 "$log" 2>/dev/null | head -c 80)
    fi
    printf "%-30s  %-10s  %-15s  %s\n" "$label" "$pid" "$exitcode" "$last_line"
done

echo
echo "Backend:     curl -sI http://127.0.0.1:8000/health 2>/dev/null | head -1"
curl -sI http://127.0.0.1:8000/health 2>/dev/null | head -1 || echo "  (backend not reachable)"
echo
echo "Tail logs:   tail -f logs/launchagent.*.{out,err}.log"
echo "Reload all:  bash scripts/install_launchagents.sh"
echo "Stop all:    bash scripts/uninstall_launchagents.sh"
