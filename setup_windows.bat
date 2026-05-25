@echo off
setlocal enabledelayedexpansion
title AI Team Brain - Setup

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║   AI Team Brain - Windows Setup          ║
echo  ╚══════════════════════════════════════════╝
echo.

REM ── Check Python ─────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo         Install Python 3.11 from https://python.org
    echo         Make sure to check "Add Python to PATH"
    pause & exit /b 1
)

REM ── Prefer Python 3.11 ───────────────────────────────────────
where py >nul 2>&1
if not errorlevel 1 (
    py -3.11 --version >nul 2>&1
    if not errorlevel 1 (
        set PYTHON=py -3.11
        echo [OK] Using Python 3.11
        goto :create_venv
    )
    py -3.12 --version >nul 2>&1
    if not errorlevel 1 (
        set PYTHON=py -3.12
        echo [OK] Using Python 3.12
        goto :create_venv
    )
    py -3.10 --version >nul 2>&1
    if not errorlevel 1 (
        set PYTHON=py -3.10
        echo [OK] Using Python 3.10
        goto :create_venv
    )
)
set PYTHON=python
echo [OK] Using default Python

:create_venv
echo.
echo [1/4] Creating virtual environment with !PYTHON!...
if exist venv ( rmdir /s /q venv )
!PYTHON! -m venv venv
if errorlevel 1 ( echo [ERROR] venv creation failed & pause & exit /b 1 )
echo       Done.

echo.
echo [2/4] Activating virtual environment...
call venv\Scripts\activate.bat
if errorlevel 1 ( echo [ERROR] Activation failed & pause & exit /b 1 )
echo       Done.

echo.
echo [3/4] Installing packages (3-5 minutes)...
python -m pip install --upgrade pip --quiet

pip install flask==3.0.3 flask-cors==4.0.0 flask-jwt-extended==4.6.0 flask-socketio==5.3.6 flask-sqlalchemy==3.1.1 --quiet
if errorlevel 1 goto :err

pip install sqlalchemy==2.0.23 alembic==1.13.0 python-dotenv==1.0.0 loguru==0.7.2 werkzeug==3.0.3 --quiet
if errorlevel 1 goto :err

pip install groq==0.4.2 google-generativeai==0.3.2 openai==1.6.1 --quiet
if errorlevel 1 goto :err

pip install "Pillow>=10.0.0" PyPDF2==3.0.1 python-docx==1.1.0 --quiet
if errorlevel 1 goto :err

pip install "numpy>=1.24.0,<2.0.0" --quiet
pip install "faiss-cpu>=1.7.4" --quiet
if errorlevel 1 ( echo [WARN] faiss-cpu failed - vector search will use keyword fallback )

pip install sentence-transformers==2.2.2 --quiet
if errorlevel 1 ( echo [WARN] sentence-transformers failed - semantic search disabled )

pip install firebase-admin==6.4.0 apscheduler==3.10.4 python-socketio==5.11.0 --quiet

echo       All packages installed.

echo.
echo [4/4] Creating .env file...
if not exist .env (
    copy .env.example .env >nul
    echo       Created .env from template.
) else (
    echo       .env already exists - skipping.
)

echo.
echo  ╔══════════════════════════════════════════╗
echo  ║   Setup Complete!                        ║
echo  ╠══════════════════════════════════════════╣
echo  ║                                          ║
echo  ║  NEXT: Edit backend\.env and add your   ║
echo  ║  Groq API key (free at console.groq.com) ║
echo  ║                                          ║
echo  ║  Then run:  start.bat                   ║
echo  ║                                          ║
echo  ╚══════════════════════════════════════════╝
echo.
pause
exit /b 0

:err
echo.
echo [ERROR] Package installation failed.
echo         Try running setup_windows.bat again.
pause
exit /b 1
