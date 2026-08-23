#!/usr/bin/env bash
# 开发模式收尾：停 host 进程，恢复 Docker 代码容器（postgres 一直在 Docker 中）
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
DEV_DIR="$ROOT/.dev"

echo "→ 停止 host 进程..."
for s in backend collector quant-engine vite; do
  if [ -f "$DEV_DIR/$s.pid" ]; then
    pid="$(cat "$DEV_DIR/$s.pid")"
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null && echo "  stopped $s (pid $pid)"
    fi
    rm -f "$DEV_DIR/$s.pid"
  fi
done

echo "→ 恢复 Docker 代码容器..."
docker compose -f deploy/docker-compose.yml up -d backend collector quant-engine frontend nginx >/dev/null 2>&1 || true
echo "✅ dev-down 完成（postgres 一直在 Docker 中）"
