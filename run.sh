#!/usr/bin/env bash
# Copyright (c) 2026 Alex Wang
# veFaaS native/v1 image runtime 启动脚本。
set -euo pipefail

# veFaaS 通过环境变量下发监听端口；兼容常见命名，缺省 8000。
PORT="${_FAAS_RUNTIME_PORT:-${PORT:-8000}}"

echo "[run.sh] starting uvicorn on 0.0.0.0:${PORT}"
echo "[run.sh] ffmpeg: $(ffmpeg -version 2>/dev/null | head -1 || echo 'NOT FOUND')"
echo "[run.sh] ffprobe: $(ffprobe -version 2>/dev/null | head -1 || echo 'NOT FOUND')"

exec python -m uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --ws-ping-interval 30 \
  --ws-ping-timeout 3600 \
  --timeout-keep-alive 75
