@echo off
setlocal EnableExtensions

REM ============================================
REM stop_background_ping.bat - emergency stop for HIDDEN-mode ping monitoring
REM (RUN_MODE=hidden means there's no visible console window to Ctrl+C).
REM
REM WARNING: this kills EVERY python.exe / python3.exe process on this
REM machine, not just this project's monitors. If you use Python on this
REM computer for other jobs, they get killed too - stop those yourself first,
REM or use "bat_many_ping.bat stop" instead, which only targets this folder's
REM ping/pathping monitors (matched by command line, not a blanket kill).
REM
REM Self-elevates: monitors started via the PingMonitor scheduled task run as
REM SYSTEM, and taskkill needs admin rights to stop those.
REM ============================================
set "SCRIPT_FULL=%~f0"

if /i "%~1"=="ADMIN_KILL" goto :DO_KILL

net session >nul 2>&1
if errorlevel 1 (
    echo Elevating to stop all python.exe / python3.exe processes...
    powershell -NoProfile -Command "Start-Process '%SCRIPT_FULL%' -Verb RunAs -ArgumentList 'ADMIN_KILL'"
    exit /b
)
goto :DO_KILL

:DO_KILL
echo Killing ALL python.exe / python3.exe processes on this machine...
taskkill /IM python.exe /F
taskkill /IM python3.exe /F
echo.
echo Done. If a PingMonitor scheduled task exists, it won't relaunch on its own
echo until its next trigger (logon / daily) - use uninstall_ping.bat to remove
echo it if you don't want that.
if /i "%~1"=="ADMIN_KILL" pause
exit /b
