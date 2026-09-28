@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================
REM bat_many_ping.bat            - guarded start (safe for the watchdog task)
REM bat_many_ping.bat restart    - stop everything, then start from current targets
REM bat_many_ping.bat stop       - stop this folder's monitors
REM
REM Editing targets*.txt does NOT affect already-running monitors (one
REM persistent ping.py per target is spawned at launch) - use "restart".
REM stop / restart self-elevate so they can also kill SYSTEM-run monitors.
REM ============================================
set "SCRIPT_FULL=%~f0"
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_SHOW=%SCRIPT_DIR%many_ping.py"
set "SCRIPT_HIDDEN=%SCRIPT_DIR%many_ping_hidden.py"
set "PATHPING_SCRIPT=%SCRIPT_DIR%pathping_monitor.py"
set "ENV_FILE=%SCRIPT_DIR%.env"
set "ACTION=%~1"

if /i "%ACTION%"=="ADMIN_STOP"    goto :DO_STOP
if /i "%ACTION%"=="ADMIN_RESTART" goto :DO_RESTART
if /i "%ACTION%"=="stop"          goto :STOP
if /i "%ACTION%"=="restart"       goto :RESTART

REM ---- default: guarded start ----
call :IS_RUNNING
if errorlevel 1 (
    echo Monitors already running for %SCRIPT_DIR%
    echo Edited targets*.txt? Reload them with:  "%~nx0" restart
    exit /b 0
)
goto :START

REM ============================================
:STOP
net session >nul 2>&1 && goto :DO_STOP
echo Elevating to stop monitors...
powershell -NoProfile -Command "Start-Process '%SCRIPT_FULL%' -Verb RunAs -ArgumentList 'ADMIN_STOP'"
exit /b

:DO_STOP
call :KILL_MONITORS
echo.
echo Monitors stopped.
if /i "%ACTION%"=="ADMIN_STOP" pause
exit /b

REM ============================================
:RESTART
net session >nul 2>&1 && goto :DO_RESTART
echo Elevating to restart monitors...
powershell -NoProfile -Command "Start-Process '%SCRIPT_FULL%' -Verb RunAs -ArgumentList 'ADMIN_RESTART'"
exit /b

:DO_RESTART
call :KILL_MONITORS
REM If a scheduled task owns monitoring, relaunch through it so it keeps
REM running as SYSTEM; otherwise start here.
schtasks /Query /TN "PingMonitor" >nul 2>&1
if not errorlevel 1 (
    echo Relaunching via scheduled task PingMonitor...
    schtasks /Run /TN "PingMonitor" >nul 2>&1
    echo Done - now monitoring the current targets*.txt.
    if /i "%ACTION%"=="ADMIN_RESTART" pause
    exit /b
)
set "ACTION="
goto :START

REM ============================================
:START
REM ---- config from .env ----
set "PYTHON_PING="
set "PYTHON_PATHPING="
set "PYTHON_EXE="
set "RUN_MODE=hidden"
set "RUN_PATHPING=N"
set "NUM_LINKS=1"

if exist "%ENV_FILE%" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        if /i "%%A"=="PYTHON_PING"     if not "%%B"=="" set "PYTHON_PING=%%B"
        if /i "%%A"=="PYTHON_PATHPING" if not "%%B"=="" set "PYTHON_PATHPING=%%B"
        if /i "%%A"=="PYTHON_EXE"      if not "%%B"=="" set "PYTHON_EXE=%%B"
        if /i "%%A"=="RUN_MODE"        if not "%%B"=="" set "RUN_MODE=%%B"
        if /i "%%A"=="RUN_PATHPING"    if not "%%B"=="" set "RUN_PATHPING=%%B"
        if /i "%%A"=="NUM_LINKS"       if not "%%B"=="" set "NUM_LINKS=%%B"
    )
)

if not defined PYTHON_PING     set "PYTHON_PING=%PYTHON_EXE%"
if not defined PYTHON_PING     set "PYTHON_PING=python"
if not defined PYTHON_PATHPING set "PYTHON_PATHPING=%PYTHON_EXE%"
if not defined PYTHON_PATHPING set "PYTHON_PATHPING=%PYTHON_PING%"

set "SHOW=0"
if /i "%RUN_MODE%"=="show" set "SHOW=1"

REM ---- one many_ping(.py/_hidden.py) per link ----
for /l %%L in (1,1,%NUM_LINKS%) do (
    set "LNUM2=0%%L"
    set "LNUM2=!LNUM2:~-2!"

    if "%NUM_LINKS%"=="1" (
        set "LINK_TARGETS=%SCRIPT_DIR%targets.txt"
    ) else (
        set "LINK_TARGETS=%SCRIPT_DIR%targets!LNUM2!.txt"
    )

    set "LINK_ISP="
    if exist "%ENV_FILE%" (
        for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
            if /i "%%A"=="ISP!LNUM2!" if not "%%B"=="" set "LINK_ISP=%%B"
        )
    )

    if "%SHOW%"=="1" (
        echo Running SHOW mode for link !LNUM2! ^(!LINK_ISP!^) - !LINK_TARGETS!...
        "%PYTHON_PING%" -u "%SCRIPT_SHOW%" "!LINK_TARGETS!" "!LINK_ISP!"
    ) else (
        echo Running HIDDEN mode for link !LNUM2! ^(!LINK_ISP!^) - !LINK_TARGETS!...
        "%PYTHON_PING%" -u "%SCRIPT_HIDDEN%" "!LINK_TARGETS!" "!LINK_ISP!"
    )
)

REM ---- optionally chain pathping_monitor.py as its own detached process ----
if /i "%RUN_PATHPING%"=="Y" (
    if "%SHOW%"=="1" (
        echo Launching pathping_monitor.py ^(visible^)...
        powershell -NoProfile -Command ^
            "Start-Process -FilePath '%PYTHON_PATHPING%' -ArgumentList '-u','%PATHPING_SCRIPT%' -WorkingDirectory '%SCRIPT_DIR%'"
    ) else (
        echo Launching pathping_monitor.py ^(hidden^)...
        powershell -NoProfile -Command ^
            "Start-Process -FilePath '%PYTHON_PATHPING%' -ArgumentList '-u','%PATHPING_SCRIPT%' -WorkingDirectory '%SCRIPT_DIR%' -WindowStyle Hidden -RedirectStandardOutput '%SCRIPT_DIR%pathping_monitor.log' -RedirectStandardError '%SCRIPT_DIR%pathping_monitor.err.log'"
    )
)

exit /b

REM ============================================
:IS_RUNNING
REM errorlevel 1 if this folder already has ping/pathping monitors running.
REM Name -match 'python' also excludes this checker (powershell.exe).
powershell -NoProfile -Command ^
    "$d=[regex]::Escape('%SCRIPT_DIR%');" ^
    "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match $d -and $_.CommandLine -match '(many_ping|ping|pathping_monitor)\.py' }) { exit 1 }"
exit /b %errorlevel%

REM ============================================
:KILL_MONITORS
echo Stopping monitors for %SCRIPT_DIR% ...
powershell -NoProfile -Command ^
    "$d=[regex]::Escape('%SCRIPT_DIR%');" ^
    "$p=Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match $d -and $_.CommandLine -match '(many_ping|ping|pathping_monitor)\.py' };" ^
    "if (-not $p) { 'none running' } else { $p | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; 'stopped ' + $_.ProcessId } catch { 'could not stop ' + $_.ProcessId } } }"
exit /b
