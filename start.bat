@echo off
setlocal enabledelayedexpansion

echo ========================================
echo ChatGPT2API Startup Script
echo ========================================
echo.

:: Check Python dependencies
echo [1/5] Checking Python dependencies...
python -c "import importlib.util; import sys; missing = []; pkgs = [('fastapi', 'fastapi'), ('uvicorn', 'uvicorn[standard]'), ('pydantic', 'pydantic'), ('curl_cffi', 'curl_cffi'), ('PIL', 'Pillow'), ('git', 'gitpython'), ('pybase64', 'pybase64'), ('tiktoken', 'tiktoken'), ('sqlalchemy', 'sqlalchemy'), ('starlette', 'starlette')]; [missing.append(p[1]) for p in pkgs if importlib.util.find_spec(p[0]) is None]; sys.exit(len(missing) if missing else 0) or print('\n'.join(missing))" 2>nul
if errorlevel 1 (
    echo [ERROR] Missing Python packages detected. Installing...
    python -c "import importlib.util; pkgs = [('fastapi', 'fastapi'), ('uvicorn', 'uvicorn[standard]'), ('pydantic', 'pydantic'), ('curl_cffi', 'curl_cffi'), ('PIL', 'Pillow'), ('git', 'gitpython'), ('pybase64', 'pybase64'), ('tiktoken', 'tiktoken'), ('sqlalchemy', 'sqlalchemy'), ('starlette', 'starlette')]; missing = [p[1] for p in pkgs if importlib.util.find_spec(p[0]) is None]; print(' '.join(missing))" > %TEMP%\missing_pkgs.txt
    set /p MISSING_PKGS=<%TEMP%\missing_pkgs.txt
    echo Installing: !MISSING_PKGS!
    pip install !MISSING_PKGS! --quiet
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies. Please run: pip install !MISSING_PKGS!
        pause
        exit /b 1
    )
    echo [OK] Dependencies installed successfully
) else (
    echo [OK] All Python dependencies satisfied
)

:: Check frontend dependencies
echo [2/5] Checking frontend dependencies...
if not exist "web\node_modules" (
    echo [WARN] node_modules not found. Installing...
    cd web
    call npm install --silent
    if errorlevel 1 (
        echo [ERROR] npm install failed. Please run: cd web ^&^& npm install
        cd ..
        pause
        exit /b 1
    )
    cd ..
    echo [OK] Frontend dependencies installed
) else (
    echo [OK] Frontend dependencies satisfied
)

echo [3/5] Stopping old processes...
:: Stop backend (port 8000)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo   Stopping backend process %%a
    taskkill /F /PID %%a >nul 2>&1
)

:: Stop frontend (port 3000) - kill all node processes in web directory
for /f "tokens=2" %%a in ('tasklist /FI "IMAGENAME eq node.exe" /FO LIST ^| findstr "PID:"') do (
    taskkill /F /PID %%a >nul 2>&1
)

echo   Waiting for ports to be released...
timeout /t 3 /nobreak >nul

echo [4/5] Starting backend...
start "ChatGPT2API-Backend" cmd /c "python main.py & echo. & echo Backend stopped. & pause"

echo [5/5] Starting frontend...
timeout /t 2 /nobreak >nul
cd web
start "ChatGPT2API-Frontend" cmd /c "npm run dev & echo. & echo Frontend stopped. & pause"
cd ..

echo.
echo ========================================
echo Services started successfully
echo Backend: http://127.0.0.1:8000
echo Frontend: http://localhost:3000
echo ========================================
echo.
echo Press any key to exit (services will keep running)...
pause >nul
