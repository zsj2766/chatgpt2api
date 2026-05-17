#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

echo "=========================================="
echo "  chatgpt2api 后端部署脚本 (Debian)"
echo "=========================================="
echo ""

# ── 1. 检查/安装系统依赖 ──
echo "[1/4] 检查系统依赖..."
for cmd in python3 pip3; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "  缺少 $cmd，正在安装..."
        sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-pip
        break
    fi
done
echo "  完成"

# ── 2. 安装 Python 依赖 ──
echo "[2/4] 安装 Python 依赖..."
pip3 install uvicorn fastapi pydantic requests aiohttp tiktoken pybase64 pillow httpx python-multipart gitpython curl-cffi sqlalchemy pyyaml --quiet
echo "  完成"

# ── 3. 释放占用端口 ──
echo "[3/4] 检查端口 8000..."
PID=$(lsof -ti:8000 2>/dev/null || true)
if [ -n "$PID" ]; then
    echo "  端口被 PID $PID 占用，正在终止..."
    kill -9 $PID 2>/dev/null || true
    sleep 1
fi
echo "  完成"

# ── 4. 后台启动后端 ──
echo "[4/4] 启动后端服务..."
cd "$ROOT"
nohup python3 main.py > "$LOG_DIR/backend.log" 2>&1 &
echo $! > "$LOG_DIR/backend.pid"
echo "  PID: $(cat "$LOG_DIR/backend.pid")"
echo "  日志: $LOG_DIR/backend.log"
echo "  后端地址: http://localhost:8000"
echo ""
echo "  停止: kill \$(cat $LOG_DIR/backend.pid)"
echo "  查看日志: tail -f $LOG_DIR/backend.log"
echo "=========================================="
