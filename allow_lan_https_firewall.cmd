@echo off
setlocal DisableDelayedExpansion
setlocal DisableDelayedExpansion
set "RULE_NAME=Cloud Brain TCP 8443 (LocalSubnet)"
if not "%~1"=="" if /i not "%~1"=="--check" goto usage
if /i "%~1"=="--check" goto check
fltmc >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Run this file as Administrator on the server.
    pause
    exit /b 1
)
netsh advfirewall firewall show rule name="%RULE_NAME%" >nul 2>&1
if not errorlevel 1 (
    echo Rule already exists. Current configuration:
    netsh advfirewall firewall show rule name="%RULE_NAME%"
    pause
    exit /b 0
)
echo This will allow LocalSubnet access to TCP 8443.
choice /c YN /n /m "Press Y to add the firewall rule; N to cancel [Y/N]: "
if errorlevel 2 exit /b 0
if errorlevel 1 goto add
exit /b 0
:add
netsh advfirewall firewall add rule name="%RULE_NAME%" dir=in action=allow protocol=TCP localport=8443 remoteip=LocalSubnet profile=private,public
if errorlevel 1 (
    echo [ERROR] Firewall rule creation failed.
    pause
    exit /b 1
)
echo TCP 8443 is allowed for LocalSubnet.
pause
exit /b 0
:check
netsh advfirewall firewall show rule name="%RULE_NAME%"
exit /b %ERRORLEVEL%
:usage
echo Usage: allow_lan_https_firewall.cmd [--check]
exit /b 2
