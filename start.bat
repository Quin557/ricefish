@echo off
rem =====================================================================
rem  ricefish one-click launcher
rem  ASCII-only on purpose: Chinese UI lives in setup.ps1, so cmd codepage
rem  problems can never garble the messages.
rem =====================================================================
setlocal
cd /d "%~dp0"

set "PS=powershell"
where powershell >nul 2>nul
if not errorlevel 1 goto run

set "PS=pwsh"
where pwsh >nul 2>nul
if not errorlevel 1 goto run

echo [!] PowerShell not found.
echo     Please run setup.ps1 manually, or install PowerShell.
pause
exit /b 1

:run
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "CODE=%ERRORLEVEL%"

if not "%CODE%"=="0" (
    echo.
    echo ricefish launcher exited with code %CODE%
    echo See setup.log for details.
    pause
)

exit /b %CODE%
