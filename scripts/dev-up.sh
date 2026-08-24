#!/usr/bin/env bash
# 开发模式一键启动：host 直跑全部代码组件（不打包镜像），Docker 仅保留 postgres
#
# 组件：
#   backend      Go，host 编译运行（go build → .dev/backend-bin），端口 8080
#   collector    Python，host 系统 python3 运行（python -m app.tasks 调度器）
#   quant-engine Python，host 系统 python3 运行（python -m app.tasks 调度器）
#   frontend-v2  vite dev server，端口 5173（/api 代理到 127.0.0.1:8080）
#
# 用法：
#   ./scripts/dev-up.sh                # 全组件启动
#   ./scripts/dev-up.sh --no-pipeline  # 只起 backend + 前端（跳过采集/引擎调度）
#   ./scripts/dev-down.sh              # 停止 host 进程并恢复 Docker 容器
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
DEV_DIR="$ROOT/.dev"
mkdir -p "$DEV_DIR"

# 1. 加载开发环境变量（DB_HOST=127.0.0.1 等）
set -a
# shellcheck disable=SC1091
source "$ROOT/scripts/dev.env"
set +a

# 2. 停掉 Docker 代码容器（保留 postgres），避免端口冲突 / 逻辑打架
echo "→ 停止 Docker 代码容器（保留 postgres）..."
docker compose -f deploy/docker-compose.yml stop backend collector quant-engine frontend nginx >/dev/null 2>&1 || true

_pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1" 2>/dev/null)" 2>/dev/null; }

# 3. backend（Go 编译 + host 运行，CWD=backend 使 config.yaml/../docs 正确解析）
if _pid_alive "$DEV_DIR/backend.pid"; then
  echo "→ backend 已在运行（pid $(cat "$DEV_DIR/backend.pid")）"
else
  echo "→ 编译并启动 backend（Go）..."
  ( cd "$ROOT/backend" && GOPROXY=https://goproxy.cn,direct go build -o "$ROOT/.dev/backend-bin" ./cmd/server )
  ( cd "$ROOT/backend" && nohup "$ROOT/.dev/backend-bin" > "$ROOT/.dev/backend.log" 2>&1 & echo $! > "$ROOT/.dev/backend.pid" )
  sleep 2
fi

# 4. collector（Python 调度器）
if _pid_alive "$DEV_DIR/collector.pid"; then
  echo "→ collector 已在运行（pid $(cat "$DEV_DIR/collector.pid")）"
elif [ "${1:-}" != "--no-pipeline" ]; then
  echo "→ 启动 collector（Python 调度器）..."
  ( cd "$ROOT/collector" && nohup python3 -m app.tasks > "$ROOT/.dev/collector.log" 2>&1 & echo $! > "$ROOT/.dev/collector.pid" )
else
  echo "→ 跳过 collector（--no-pipeline）"
fi

# 5. quant-engine（Python 调度器）
if _pid_alive "$DEV_DIR/quant-engine.pid"; then
  echo "→ quant-engine 已在运行（pid $(cat "$DEV_DIR/quant-engine.pid")）"
elif [ "${1:-}" != "--no-pipeline" ]; then
  echo "→ 启动 quant-engine（Python 调度器）..."
  ( cd "$ROOT/quant-engine" && nohup python3 -m app.tasks > "$ROOT/.dev/quant-engine.log" 2>&1 & echo $! > "$ROOT/.dev/quant-engine.pid" )
else
  echo "→ 跳过 quant-engine（--no-pipeline）"
fi

# 6. frontend-v2（vite dev server）
if _pid_alive "$DEV_DIR/vite.pid"; then
  echo "→ frontend-v2 dev 已在运行（pid $(cat "$DEV_DIR/vite.pid")）"
else
  echo "→ 启动 frontend-v2（vite dev）..."
  ( cd "$ROOT/frontend" && nohup npm run dev > "$ROOT/.dev/vite.log" 2>&1 & echo $! > "$ROOT/.dev/vite.pid" )
fi

# 7. 健康检查 + 摘要
sleep 3
echo
echo "================ 开发环境摘要 ================"
if _pid_alive "$DEV_DIR/backend.pid"; then
  h="$(curl -s --max-time 3 http://127.0.0.1:8080/api/v1/health 2>/dev/null || echo 'health 请求失败')"
  echo "✅ backend     http://127.0.0.1:8080  $h"
else
  echo "❌ backend     未启动（见 .dev/backend.log）"
fi
if _pid_alive "$DEV_DIR/vite.pid"; then
  echo "✅ frontend-v2 http://localhost:5173  （dev 模式，HMR）"
else
  echo "❌ frontend-v2 未启动（见 .dev/vite.log）"
fi
for s in collector quant-engine; do
  if _pid_alive "$DEV_DIR/$s.pid"; then
    echo "✅ $s   pid $(cat "$DEV_DIR/$s.pid")（调度器后台运行，日志 .dev/$s.log）"
  else
    echo "⚠️ $s  未运行（--no-pipeline 或启动失败，见 .dev/$s.log）"
  fi
done
echo "============================================"
echo "提示：dev 机器重启后 docker 的 unless-stopped 可能复活代码容器占 8080，"
echo "      再跑一次 dev-up.sh 即可（脚本会先停容器再起 host 进程）。"
