@echo off
chcp 65001 >nul
setlocal

echo ==========================================
echo   chatgpt2api 前端部署脚本
echo ==========================================
echo.

set ROOT=%~dp0
cd /d "%ROOT%"

:: ── 1. 安装前端依赖 ──
echo [1/4] 安装前端依赖...
cd /d "%ROOT%web"
call npm install --silent 2>nul
if %errorlevel% neq 0 (
    echo 依赖安装失败
    exit /b 1
)
echo   完成

:: ── 2. 释放占用端口 ──
echo [2/4] 检查端口 3000...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :3000 ^| findstr LISTENING') do (
    echo   端口被 PID %%a 占用，正在终止...
    taskkill /PID %%a /F >nul 2>&1
)
echo   完成

:: ── 3. 构建前端 ──
echo [3/4] 构建前端...
call npm run build 2>nul
if %errorlevel% neq 0 (
    echo 前端构建失败
    exit /b 1
)
echo   完成

:: ── 4. 启动前端服务 ──
echo [4/4] 启动前端服务...
echo   前端地址: http://localhost:3000
echo.
echo 按 Ctrl+C 停止服务
echo ==========================================

call npm run dev
