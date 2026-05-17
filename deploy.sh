#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$ROOT/logs"

# ── stop 子命令 ──
if [ "${1:-}" = "--stop" ]; then
    echo "正在停止所有服务..."
    mkdir -p "$LOG_DIR"
    for SVC in backend frontend; do
        PID_FILE="$LOG_DIR/$SVC.pid"
        if [ -f "$PID_FILE" ]; then
            PID=$(cat "$PID_FILE")
            kill "$PID" 2>/dev/null && echo "  $SVC (PID $PID) 已停止" || echo "  $SVC 未在运行"
            rm -f "$PID_FILE"
        else
            echo "  $SVC 无 PID 文件，跳过"
        fi
    done
    exit 0
fi

mkdir -p "$LOG_DIR"

echo "=========================================="
echo "  chatgpt2api 一键部署脚本 (Debian)"
echo "=========================================="
echo ""

# ── 1. 检查/安装系统依赖 ──
echo "[1/6] 检查系统依赖..."
NEED_INSTALL=0
for cmd in python3 pip3 node npm; do
    if ! command -v "$cmd" &>/dev/null; then
        NEED_INSTALL=1
        break
    fi
done
if [ "$NEED_INSTALL" -eq 1 ]; then
    echo "  正在安装系统依赖..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-pip
    if ! command -v node &>/dev/null; then
        curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
        sudo apt-get install -y -qq nodejs
    fi
fi
echo "  完成"

# ── 2. 安装 Python 依赖 ──
echo "[2/6] 安装 Python 依赖..."
pip3 install uvicorn fastapi pydantic requests aiohttp tiktoken pybase64 pillow httpx python-multipart gitpython curl-cffi sqlalchemy pyyaml --quiet
echo "  完成"

# ── 3. 安装前端依赖 ──
echo "[3/6] 安装前端依赖..."
cd "$ROOT/web"
npm install --silent 2>/dev/null
echo "  完成"

# ── 4. 释放占用端口 ──
echo "[4/6] 检查端口 8000 和 3000..."
for PORT in 8000 3000; do
    PID=$(lsof -ti:$PORT 2>/dev/null || true)
    if [ -n "$PID" ]; then
        echo "  端口 $PORT 被 PID $PID 占用，正在终止..."
        kill -9 $PID 2>/dev/null || true
    fi
done
sleep 1
echo "  完成"

# ── 5. 构建前端 ──
echo "[5/6] 构建前端..."
npm run build
echo "  完成"

# ── 6. 后台启动所有服务 ──
echo "[6/6] 启动服务..."
cd "$ROOT"
nohup python3 main.py > "$LOG_DIR/backend.log" 2>&1 &
echo $! > "$LOG_DIR/backend.pid"

cd "$ROOT/web"
nohup npm run start > "$LOG_DIR/frontend.log" 2>&1 &
echo $! > "$LOG_DIR/frontend.pid"

sleep 2
echo ""
echo "  后端 PID: $(cat "$LOG_DIR/backend.pid")  →  http://localhost:8000"
echo "  前端 PID: $(cat "$LOG_DIR/frontend.pid")  →  http://localhost:3000"
echo ""
echo "  停止所有: bash $0 --stop"
echo "  后端日志: tail -f $LOG_DIR/backend.log"
echo "  前端日志: tail -f $LOG_DIR/frontend.log"
echo "=========================================="
