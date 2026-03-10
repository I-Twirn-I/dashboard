@echo off
cd /d "%~dp0"
start /min "" python app.py
timeout /t 2 /nobreak >nul
start http://localhost:5000
