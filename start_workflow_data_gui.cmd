@echo off
cd /d "%~dp0"
if not exist ".venv_py312\Scripts\pythonw.exe" (
  echo Python runtime not found: .venv_py312\Scripts\pythonw.exe
  pause
  exit /b 1
)
start "" ".venv_py312\Scripts\pythonw.exe" "scripts\workflow_data_gui.py"
