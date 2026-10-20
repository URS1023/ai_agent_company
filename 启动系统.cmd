@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -X utf8 deploy\native\desktop.py start
if errorlevel 1 (
  pause
  exit /b 1
)
start "" "http://127.0.0.1:8080"
start "" "http://127.0.0.1:13000"
