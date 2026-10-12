@echo off
setlocal DisableDelayedExpansion

set "PROJECT_DIR=%~dp0"
set "LAN_SCRIPT=%PROJECT_DIR%start_lan.ps1"

if not exist "%LAN_SCRIPT%" (
    echo [ERROR] Cannot find %LAN_SCRIPT%
    exit /b 1
)

rem Bypass applies only to this child PowerShell process.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%LAN_SCRIPT%" %*
set "SCRIPT_EXIT_CODE=%ERRORLEVEL%"

if not "%SCRIPT_EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] LAN command failed with exit code %SCRIPT_EXIT_CODE%.
    echo Run this terminal as Administrator when firewall configuration is requested.
)

exit /b %SCRIPT_EXIT_CODE%
