@echo off
chcp 65001 >nul
cd /d "%~dp0"
".venv\Scripts\python.exe" -X utf8 deploy\native\desktop.py accounts
pause
