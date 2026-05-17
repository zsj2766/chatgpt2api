@echo off
chcp 65001 >nul
setlocal

echo ==========================================
echo   chatgpt2api 一键部署脚本
echo ==========================================
echo.

set ROOT=%~dp0
cd /d "%ROOT%"

:: ── 1. 安装后端依赖 ──
echo [1/6] 安装 Python 依赖...
pip install uvicorn fastapi pydantic requests aiohttp tiktoken pybase64 pillow httpx python-multipart gitpython curl-cffi sqlalchemy pyyaml --quiet
if %errorlevel% neq 0 (
    echo 依赖安装失败，请检查网络或 pip 配置
    exit /b 1
)
echo   完成

:: ── 2. 安装前端依赖 ──
echo [2/6] 安装前端依赖...
cd /d "%ROOT%web"
call npm install --silent 2>nul
if %errorlevel% neq 0 (
    echo 前端依赖安装失败
    exit /b 1
)
echo   完成

:: ── 3. 释放占用端口 ──
echo [3/6] 检查端口 8000 和 3000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do (
    echo   端口 8000 被 PID %%a 占用，正在终止...
    taskkill /PID %%a /F >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :3000 ^| findstr LISTENING') do (
    echo   端口 3000 被 PID %%a 占用，正在终止...
    taskkill /PID %%a /F >nul 2>&1
)
echo   完成

:: ── 4. 构建前端 ──
echo [4/6] 构建前端...
call npm run build 2>nul
if %errorlevel% neq 0 (
    echo 前端构建失败
    exit /b 1
)
echo   完成

:: ── 5. 启动服务 ──
echo [5/6] 启动服务...
echo   后端: http://localhost:8000（热重载）
echo   前端: http://localhost:3000
echo.
echo 按 Ctrl+C 停止所有服务
echo ==========================================

cd /d "%ROOT%"
start "chatgpt2api-backend" cmd /c "python main.py --reload"
cd /d "%ROOT%web"
start "chatgpt2api-frontend" cmd /c "npm run dev"

echo.
echo [6/6] 部署完成！访问 http://localhost:3000
timeout /t 5 >nul
