@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"

chcp 65001 >nul

rem Stable ASCII wrapper for the project launcher.
set "PROJECT_DIR=%~dp0"
if "%PORT%"=="" set "PORT=8000"
set "HOST=0.0.0.0"
set "START_SCRIPT=%PROJECT_DIR%backend\start_server.ps1"
set "PYTHON_EXE=%PROJECT_DIR%.venv_py312\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Project Python runtime was not found: %PYTHON_EXE%
    pause
    endlocal & exit /b 1
)
if not exist "%START_SCRIPT%" (
    echo [ERROR] Backend launcher was not found: %START_SCRIPT%
    pause
    endlocal & exit /b 1
)

echo ==========================================
echo  Cloud Brain
echo  Local: http://127.0.0.1:%PORT%
echo  LAN:   http://LAN-IP:%PORT%
echo  Loading frontend and backend from this workspace.
echo ==========================================

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%START_SCRIPT%" -Port %PORT% -BindAddress %HOST%
set "START_EXIT_CODE=%ERRORLEVEL%"
if not "%START_EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] Server stopped with exit code %START_EXIT_CODE%.
    pause
)
endlocal & exit /b %START_EXIT_CODE%
