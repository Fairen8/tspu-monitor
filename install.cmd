@echo off
rem TSPU Monitor - Windows installer (double-click friendly).
rem Uses install.ps1 next to this file only if it is recent (has the safe
rem native runner); otherwise downloads the latest one. This protects from
rem stale copies lying around in the download folder.
setlocal
set "LOCAL=%~dp0install.ps1"
set "SCRIPT="
if exist "%LOCAL%" (
    findstr /c:"Invoke-Native" "%LOCAL%" >nul 2>&1 && set "SCRIPT=%LOCAL%"
)
if not defined SCRIPT (
    set "SCRIPT=%TEMP%\tspu-install.ps1"
    echo [*] Downloading latest install.ps1...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/Fairen8/tspu-monitor/main/install.ps1' -OutFile '%TEMP%\tspu-install.ps1'"
)
set "TSPU_PAUSE_OWNED=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
set "RC=%ERRORLEVEL%"
echo.
if defined CI exit /b %RC%
pause
exit /b %RC%
