#!/usr/bin/env bash

set -euo pipefail

syntax_check() {
  echo "==> backend-gate: Python syntax check"
  python -m py_compile main.py src/config.py src/auth.py src/analyzer.py src/notification.py
  python -m py_compile src/storage.py src/scheduler.py src/search_service.py
  python -m py_compile src/market_analyzer.py src/stock_analyzer.py
  python -m py_compile data_provider/*.py
  # Phase 0 v4 Mirror layer
  python -m py_compile src/options/*.py src/journal/*.py src/journal/brokers/*.py
  python -m py_compile src/regime/*.py src/breakout/*.py
  python -m py_compile src/agent/tools/get_regime_score_tool.py \
      src/agent/tools/get_option_chain_tool.py \
      src/agent/tools/check_breakout_tool.py \
      src/agent/tools/get_journal_snapshot_tool.py
  python -m py_compile api/v1/endpoints/journal.py \
      api/v1/endpoints/regime.py api/v1/endpoints/breakout.py
}

flake8_checks() {
  echo "==> backend-gate: flake8 critical checks"
  flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
}

deterministic_checks() {
  echo "==> backend-gate: local deterministic checks"
  ./test.sh code
  ./test.sh yfinance
}

offline_test_suite() {
  echo "==> backend-gate: offline test suite"
  python -m pytest -m "not network"
}

run_all() {
  syntax_check
  flake8_checks
  deterministic_checks
  offline_test_suite
  echo "==> backend-gate: all checks passed"
}

phase="${1:-all}"

case "$phase" in
  all)
    run_all
    ;;
  syntax)
    syntax_check
    ;;
  flake8)
    flake8_checks
    ;;
  deterministic)
    deterministic_checks
    ;;
  offline-tests)
    offline_test_suite
    ;;
  *)
    echo "Usage: $0 [all|syntax|flake8|deterministic|offline-tests]" >&2
    exit 2
    ;;
esac
