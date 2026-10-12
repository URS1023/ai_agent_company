@echo off
setlocal DisableDelayedExpansion
rem Usage: start_lan_https.cmd LAN_IP [PORT] [--prepare-only]
set "LAN_IP=%~1"
set "PORT=%~2"
set "PREPARE_ONLY="
if /i "%LAN_IP%"=="" goto usage
if /i "%LAN_IP%"=="--prepare-only" goto usage
if "%PORT%"=="" set "PORT=8443"
if /i "%~3"=="--prepare-only" set "PREPARE_ONLY=--prepare-only"
if not "%~3"=="" if /i not "%~3"=="--prepare-only" goto usage
set "PROJECT_DIR=%~dp0"
set "PYTHON_EXE=%PROJECT_DIR%.venv_py312\Scripts\python.exe"
set "CERT_DIR=%PROJECT_DIR%.implementation\lan-https"
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Project Python runtime was not found: %PYTHON_EXE%
    goto failed
)
if not exist "%CERT_DIR%" mkdir "%CERT_DIR%"
if not exist "%CERT_DIR%" goto failed
icacls "%CERT_DIR%" /inheritance:r /grant:r "%USERDOMAIN%\%USERNAME%:(OI)(CI)F" "*S-1-5-18:(OI)(CI)F" >nul
if errorlevel 1 goto failed
"%PYTHON_EXE%" "%PROJECT_DIR%scripts\create_lan_certificate.py" --ip "%LAN_IP%" --directory "%CERT_DIR%"
if errorlevel 1 goto failed
copy /y "%PROJECT_DIR%scripts\install_lan_certificate.cmd" "%CERT_DIR%\client\install_lan_certificate.cmd" >nul
if errorlevel 1 goto failed
copy /y "%PROJECT_DIR%scripts\install_lan_certificate.ps1" "%CERT_DIR%\client\install_lan_certificate.ps1" >nul
if errorlevel 1 goto failed
>"%CERT_DIR%\client\server_url.txt" echo https://%LAN_IP%:%PORT%/ai
echo.
echo URL: https://%LAN_IP%:%PORT%/ai
echo Client certificate directory: %CERT_DIR%\client
echo Run install_lan_certificate.cmd on each client before first use.
if /i "%PREPARE_ONLY%"=="--prepare-only" exit /b 0
echo Starting HTTPS server. Press Ctrl+C to stop.
set "HOST=0.0.0.0"
set "SSL_CERTFILE=%CERT_DIR%\server.crt"
set "SSL_KEYFILE=%CERT_DIR%\server.key"
set "SCHEDULER_ENABLED=1"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_DIR%backend\start_server.ps1" -Port %PORT% -BindAddress 0.0.0.0
set "SERVER_EXIT=%ERRORLEVEL%"
if not "%SERVER_EXIT%"=="0" goto failed
exit /b 0
:usage
echo Usage: start_lan_https.cmd LAN_IP [PORT] [--prepare-only]
echo Example: start_lan_https.cmd 192.168.6.18 8443
echo Example: start_lan_https.cmd 192.168.6.18 8443 --prepare-only
exit /b 2
:failed
echo.
echo [ERROR] Operation failed. Existing services were not stopped.
if /i not "%~3"=="--prepare-only" pause
exit /b 1
