#!/usr/bin/env bash
# Install Daily-Stock LaunchAgents on macOS so backend + Moomoo sync + breakout
# daemon start automatically on login and stay running.
#
# Usage:
#   bash scripts/install_launchagents.sh                  # install all 3
#   bash scripts/install_launchagents.sh --skip breakout  # skip the breakout agent
#
# Re-running this script just overwrites + reloads the agents. Safe to repeat.

set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATES_DIR="${PROJECT_DIR}/scripts/launchagents"
TARGET_DIR="${HOME}/Library/LaunchAgents"
LOGS_DIR="${PROJECT_DIR}/logs"
PYTHON="${PYTHON:-$(command -v python3 || command -v python)}"

# Resolve Python's actual interpreter path (avoid version drift via a shim)
PYTHON="$($PYTHON -c 'import sys; print(sys.executable)')"

AGENTS=(
    "com.dailystock.uvicorn"
    "com.dailystock.moomoo-sync"
    "com.dailystock.breakout-live"
)

skip_filter=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip) skip_filter="$2"; shift 2 ;;
        *) echo "unknown arg: $1"; exit 2 ;;
    esac
done

echo "==> daily_stock_analysis @ ${PROJECT_DIR}"
echo "==> python              @ ${PYTHON}"
echo "==> target               ~/Library/LaunchAgents"
echo

if [[ ! -x "${PYTHON}" ]]; then
    echo "ERROR: python not found at ${PYTHON}" >&2
    exit 1
fi

# Sanity-check the project root looks right
if [[ ! -f "${PROJECT_DIR}/server.py" ]]; then
    echo "ERROR: server.py not found at ${PROJECT_DIR} — is this the right project?" >&2
    exit 1
fi

mkdir -p "${TARGET_DIR}" "${LOGS_DIR}"

for label in "${AGENTS[@]}"; do
    if [[ -n "${skip_filter}" && "${label}" == *"${skip_filter}"* ]]; then
        echo "[skip] ${label}"
        continue
    fi

    src="${TEMPLATES_DIR}/${label}.plist"
    dst="${TARGET_DIR}/${label}.plist"

    if [[ ! -f "${src}" ]]; then
        echo "ERROR: template missing: ${src}" >&2
        exit 1
    fi

    # 1. Substitute placeholders
    sed -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
        -e "s|__PYTHON__|${PYTHON}|g" \
        "${src}" > "${dst}"

    # 2. Validate plist syntax
    if ! plutil -lint "${dst}" >/dev/null 2>&1; then
        echo "ERROR: plist invalid: ${dst}" >&2
        plutil -lint "${dst}"
        exit 1
    fi

    # 3. Reload (unload first to pick up changes if previously installed)
    launchctl unload "${dst}" 2>/dev/null || true
    launchctl load "${dst}"

    echo "[loaded] ${label}"
done

echo
echo "==> done. Agents:"
launchctl list 2>/dev/null | awk '
    /com\.dailystock\./ {
        printf "  %-35s pid=%-8s last_exit=%s\n", $3, $1, $2
    }'
echo
echo "Logs:        ${LOGS_DIR}/"
echo "Backend URL: http://127.0.0.1:8000"
echo
echo "Status:      bash scripts/launchagent_status.sh"
echo "Uninstall:   bash scripts/uninstall_launchagents.sh"
