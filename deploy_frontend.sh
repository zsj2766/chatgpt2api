#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

echo "=========================================="
echo "  chatgpt2api 前端部署脚本 (Debian)"
echo "=========================================="
echo ""

# ── 1. 检查/安装 Node.js ──
echo "[1/4] 检查 Node.js..."
if ! command -v node &>/dev/null; then
    echo "  缺少 Node.js，正在安装..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
    sudo apt-get install -y -qq nodejs
fi
echo "  完成"

# ── 2. 安装前端依赖 ──
echo "[2/4] 安装前端依赖..."
cd "$ROOT/web"
npm install --silent 2>/dev/null
echo "  完成"

# ── 3. 释放占用端口 ──
echo "[3/4] 检查端口 3000..."
PID=$(lsof -ti:3000 2>/dev/null || true)
if [ -n "$PID" ]; then
    echo "  端口被 PID $PID 占用，正在终止..."
    kill -9 $PID 2>/dev/null || true
    sleep 1
fi
echo "  完成"

# ── 4. 构建并后台启动前端 ──
echo "[4/4] 构建并启动前端服务..."
npm run build
nohup npm run start > "$LOG_DIR/frontend.log" 2>&1 &
echo $! > "$LOG_DIR/frontend.pid"
echo "  PID: $(cat "$LOG_DIR/frontend.pid")"
echo "  日志: $LOG_DIR/frontend.log"
echo "  前端地址: http://localhost:3000"
echo ""
echo "  停止: kill \$(cat $LOG_DIR/frontend.pid)"
echo "  查看日志: tail -f $LOG_DIR/frontend.log"
echo "=========================================="
