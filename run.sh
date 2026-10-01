#!/usr/bin/env bash
# Minecraft Java 命令生成器 —— macOS / Linux 启动脚本
set -euo pipefail

cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    PY="python"
fi

exec "$PY" main.py "$@"
