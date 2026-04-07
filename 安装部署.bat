@echo off
title ZEN70 Installer
cd /d "%~dp0"

echo.
echo  ========================================
echo    ZEN70 Graphical Installer Starting...
echo  ========================================
echo.

where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.11+
    echo Download: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

echo [OK] Python found.
echo [..] Checking dependencies...

python -c "import fastapi, uvicorn, yaml, pydantic" >nul 2>&1
if %errorlevel% neq 0 (
    echo [..] Installing dependencies...
    python -m pip install fastapi uvicorn pyyaml pydantic "ruamel.yaml"
    if %errorlevel% neq 0 (
        echo [ERROR] Dependency install failed.
        pause
        exit /b 1
    )
)

echo [OK] Dependencies ready.
echo [..] Launching web installer...
echo.
echo     If browser does not open, visit: http://localhost:8765
echo.

python start_installer.py

echo.
echo ========================================
echo  Installer exited.
echo  If browser did not open, visit:
echo  http://localhost:8765
echo ========================================
echo.
pause
