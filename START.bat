@echo off
REM Always run from the folder where START.bat is located (handles Extract + Double-click, even "Run as admin")
cd /d "%~dp0"
title SMARTQUALIHOME - One-Click Start
color 0A
echo ============================================================
echo  SMARTQUALIHOME - One-Click Local Setup
echo  For friend: Just double-click this file after extracting ZIP
echo  1) Start XAMPP -> Click "Start" on Apache and MySQL
echo  2) Double-click START.bat (this file)
echo  That's it!
echo ============================================================
echo.

echo [1/4] Checking virtual environment...
if exist ".venv\Scripts\python.exe" (
    echo   .venv found.
) else (
    REM Check if Python or Py Launcher is available
    py --version >nul 2>&1
    if %errorlevel% equ 0 (
        set "PY_CMD=py"
    ) else (
        python --version >nul 2>&1
        if %errorlevel% equ 0 (
            set "PY_CMD=python"
        ) else (
            echo [ERROR] Python not found!
            echo   Please install Python 3.10+ from https://www.python.org/
            echo   Check "Add python.exe to PATH" during install
            pause
            exit /b
        )
    )
    echo   Creating .venv (first time only, ~10 sec)...
    %PY_CMD% -m venv --system-site-packages .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create venv
        pause
        exit /b
    )
)

echo.
echo [2/4] Checking requirements...
".venv\Scripts\pip.exe" install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt
if %errorlevel% neq 0 (
    echo [WARN] pip install had warnings, continuing...
)

echo.
echo [3/4] Setting up database (auto-imports migrations/schema.sql)...
".venv\Scripts\python.exe" install.py --db-only
if %errorlevel% neq 0 (
    echo [WARN] MySQL setup had warnings, will try SQLite fallback
)

echo.
echo [4/4] Starting server at http://127.0.0.1:5000
echo   Login accounts:
echo     Admin  - admin@smartqualihome.com / Admin@2026!
echo     Agent  - agent@smartqualihome.com / Agent@2026!
echo     Client - client@smartqualihome.com / Client@2026!
echo.
echo   Your browser will open automatically. Press CTRL+C to stop.
echo   Keep this window open while testing.
echo ============================================================
timeout /t 2 >nul
start http://127.0.0.1:5000
".venv\Scripts\python.exe" run.py
pause
