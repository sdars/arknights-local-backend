#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${ARK_PYTHON:-/root/Knight/.venv/bin/python}"
exec env ARK_PORT="${ARK_PORT:-8443}" "$PYTHON" run.py
