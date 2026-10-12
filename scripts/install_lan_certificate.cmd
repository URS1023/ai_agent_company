@echo off
setlocal DisableDelayedExpansion
set "CERT_FILE=%~dp0Cloud-Brain-LAN-CA.cer"
set "SERVER_URL_FILE=%~dp0server_url.txt"
set "SERVER_URL="
if exist "%SERVER_URL_FILE%" set /p "SERVER_URL="<"%SERVER_URL_FILE%"
if not defined SERVER_URL set "SERVER_URL=(server URL not included; ask the administrator)"
if not "%~1"=="" if /i not "%~1"=="--check" goto usage
if not exist "%CERT_FILE%" (
    echo [ERROR] Cloud-Brain-LAN-CA.cer is missing. Copy the complete client folder first.
    goto failed
)
echo Certificate: %CERT_FILE%
certutil -dump "%CERT_FILE%" >nul
if errorlevel 1 goto failed
echo.
echo Verify this SHA-256 fingerprint with the server administrator:
certutil -hashfile "%CERT_FILE%" SHA256
if errorlevel 1 goto failed
if /i "%~1"=="--check" exit /b 0
echo.
echo The certificate will be installed for the current Windows user.
choice /c YN /n /m "Press Y after verifying the source and fingerprint; N to cancel [Y/N]: "
if errorlevel 2 goto cancelled
if errorlevel 1 goto install
goto cancelled
:install
certutil -user -addstore Root "%CERT_FILE%"
if errorlevel 1 goto failed
echo.
echo Installation succeeded. Reopen the browser and visit %SERVER_URL%
echo If needed, remove it with certmgr.msc from Trusted Root Certification Authorities.
pause
exit /b 0
:cancelled
echo Cancelled. No certificate was installed.
exit /b 0
:usage
echo Usage: install_lan_certificate.cmd [--check]
exit /b 2
:failed
echo [ERROR] Operation failed. See the message above.
if /i not "%~1"=="--check" pause
exit /b 1
