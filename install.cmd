@echo off
rem TSPU Monitor - Windows installer (double-click friendly).
rem Runs install.ps1 (next to this file or downloaded) with bypass policy.
setlocal
set "LOCAL=%~dp0install.ps1"
set "SCRIPT=%LOCAL%"
if not exist "%LOCAL%" (
    set "SCRIPT=%TEMP%\tspu-install.ps1"
    echo [*] Downloading install.ps1...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/Fairen8/tspu-monitor/main/install.ps1' -OutFile '%TEMP%\tspu-install.ps1'"
)
set "TSPU_PAUSE_OWNED=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
set "RC=%ERRORLEVEL%"
echo.
if defined CI exit /b %RC%
pause
exit /b %RC%
