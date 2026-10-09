#!/usr/bin/env bash
#
# dev.sh — 开发模式：前端热更新 + 后端自动重载
#
#     web/dev.sh
#
# 起两个进程：
#   后端 uvicorn --reload   → http://127.0.0.1:8787
#   前端 vite dev server    → http://127.0.0.1:5173   ← 浏览器开这个
#
# Vite 会把 /api 转发到后端，所以前端页面和后端接口同源，不存在跨域问题。
# Ctrl-C 一起停掉。

set -euo pipefail

WEB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$WEB_DIR/.." && pwd)"
FRONTEND_DIR="$WEB_DIR/frontend"
BACKEND_DIR="$WEB_DIR/backend"

BACKEND_PORT=8787

info() { printf '\033[36m▸\033[0m %s\n' "$*"; }
die()  { printf '\033[31m❌\033[0m %s\n' "$*" >&2; exit 1; }

PY="$PROJECT_ROOT/.venv/bin/python"
[ -x "$PY" ] || die "没找到虚拟环境，先跑一次 web/deploy.sh"

"$PY" -c 'import fastapi, uvicorn' >/dev/null 2>&1 \
    || die "后端依赖缺失，先跑一次 web/deploy.sh"

command -v npm >/dev/null 2>&1 || die "未找到 npm"

[ -d "$FRONTEND_DIR/node_modules" ] || {
    info "安装前端依赖（首次会比较慢）"
    (cd "$FRONTEND_DIR" && npm install)
}

BACKEND_PID=""
cleanup() {
    if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        kill "$BACKEND_PID" 2>/dev/null || true
        wait "$BACKEND_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

info "后端 http://127.0.0.1:$BACKEND_PORT"
cd "$PROJECT_ROOT"
"$PY" -m uvicorn app:app --app-dir "$BACKEND_DIR" \
    --host 127.0.0.1 --port "$BACKEND_PORT" --reload &
BACKEND_PID=$!

sleep 2

info "前端 http://127.0.0.1:5173  ← 浏览器开这个"
cd "$FRONTEND_DIR"
npm run dev
