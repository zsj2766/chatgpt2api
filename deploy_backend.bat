@echo off
chcp 65001 >nul
setlocal

echo ==========================================
echo   chatgpt2api 后端部署脚本
echo ==========================================
echo.

set ROOT=%~dp0
cd /d "%ROOT%"

:: ── 1. 安装后端依赖 ──
echo [1/3] 安装 Python 依赖...
pip install uvicorn fastapi pydantic requests aiohttp tiktoken pybase64 pillow httpx python-multipart gitpython curl-cffi sqlalchemy pyyaml --quiet
if %errorlevel% neq 0 (
    echo 依赖安装失败，请检查网络或 pip 配置
    exit /b 1
)
echo   完成

:: ── 2. 释放占用端口 ──
echo [2/3] 检查端口 8000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do (
    echo   端口被 PID %%a 占用，正在终止...
    taskkill /PID %%a /F >nul 2>&1
)
echo   完成

:: ── 3. 启动后端服务 ──
echo [3/3] 启动后端服务...
echo   后端地址: http://localhost:8000
echo.
echo 按 Ctrl+C 停止服务
echo ==========================================

cd /d "%ROOT%"
python main.py
