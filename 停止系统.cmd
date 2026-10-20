@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -X utf8 deploy\native\desktop.py stop
pause
