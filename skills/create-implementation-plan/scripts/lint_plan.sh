#!/usr/bin/env bash
# 実装計画書の提出前セルフリント（本体は lint_plan.py）
# usage: lint_plan.sh <plan.md> [plan2.md ...]
set -euo pipefail
exec python3 "$(dirname "$0")/lint_plan.py" "$@"
