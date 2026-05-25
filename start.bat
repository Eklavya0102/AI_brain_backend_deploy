@echo off
title AI Team Brain - Backend
echo.
echo  ╔══════════════════════════════════════════╗
echo  ║   AI Team Brain — Backend Server         ║
echo  ╚══════════════════════════════════════════╝
echo.

if not exist venv (
    echo [ERROR] venv not found. Run setup_windows.bat first!
    pause & exit /b 1
)

if not exist .env (
    echo [ERROR] .env not found. Run setup_windows.bat first!
    pause & exit /b 1
)

call venv\Scripts\activate.bat
echo [OK] Virtual environment activated
echo [OK] Starting server on http://localhost:5000
echo [OK] Press Ctrl+C to stop
echo.
python app.py
