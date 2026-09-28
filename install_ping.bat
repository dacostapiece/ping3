@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================
REM install_ping.bat - one-time setup for the ping monitoring suite:
REM   * multi-link / multi-ISP config (.env NUM_LINKS / ISPnn)
REM   * pick which python.exe runs ping / pathping (.env PYTHON_PING / PYTHON_PATHPING)
REM   * console visibility (.env RUN_MODE = hidden|show)
REM   * whether to also launch pathping_monitor.py (.env RUN_PATHPING = Y|N)
REM   * how monitoring starts: 1) Scheduled Task  2) Start Menu shortcut  3) manual
REM     -- a Desktop shortcut is created in every case.
REM Use uninstall_ping.bat to undo the tasks / shortcuts.
REM ============================================
set "SCRIPT_FULL=%~f0"
set "SCRIPT_DIR=%~dp0"
set "TARGET_BAT=%SCRIPT_DIR%bat_many_ping.bat"
set "ENV_FILE=%SCRIPT_DIR%.env"

REM Daily time (24h HH:MM) for the scheduled-task option's daily relaunch + report.
set "DAILY_TIME=18:00"

for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESKTOP=%%D"
for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Programs')"`) do set "STARTMENU=%%D"
set "DESKTOP_LNK=%DESKTOP%\Ping Monitor.lnk"
set "STARTMENU_LNK=%STARTMENU%\Ping Monitor.lnk"

REM ============================================
REM Elevated re-entry (scheduled-task creation needs admin): %1 = ADMIN_TASK
REM ============================================
if /i "%1"=="ADMIN_TASK" goto :DO_TASK

REM ---- config steps (no admin needed - only .env / targetsNN.txt in this folder) ----
call :CONFIGURE_LINKS
call :CONFIGURE_PYTHON
call :CONFIGURE_RUN_MODE
call :CONFIGURE_PATHPING
call :SAVE_SETTINGS

REM ============================================
REM Startup method
REM ============================================
echo.
echo How should monitoring start?
echo    1^) Scheduled Task   (as SYSTEM, no user interaction; at logon + daily at %DAILY_TIME%; relaunches if closed)
echo    2^) Start Menu shortcut
echo    3^) Manual only
echo.
set "STARTUP_CHOICE="
set /p "STARTUP_CHOICE=Choose 1, 2 or 3: "

REM Desktop shortcut in every case
call :MAKE_SHORTCUT "%DESKTOP_LNK%"
echo Desktop shortcut: %DESKTOP_LNK%

if "%STARTUP_CHOICE%"=="1" (
    net session >nul 2>&1
    if errorlevel 1 (
        echo Elevating to create the scheduled tasks - a separate admin window will open...
        echo.
        echo When it finishes, monitoring runs automatically at logon / %DAILY_TIME%.
        echo To start it right now, run this from an elevated prompt:
        echo     schtasks /Run /TN "PingMonitor"
        powershell -NoProfile -Command ^
            "Start-Process '%SCRIPT_FULL%' -Verb RunAs -ArgumentList 'ADMIN_TASK'"
        goto :DONE
    )
    goto :DO_TASK
)
if "%STARTUP_CHOICE%"=="2" (
    call :MAKE_SHORTCUT "%STARTMENU_LNK%"
    echo Start Menu shortcut: %STARTMENU_LNK%
    goto :DONE
)
echo Manual only - launch monitoring with bat_many_ping.bat or the Desktop shortcut.
goto :DONE

REM ============================================
:DO_TASK
REM Runs elevated. Re-read the python path from .env (vars don't survive the
REM UAC relaunch) for the daily-report task.
REM ============================================
set "PY_REPORT=python"
if exist "%ENV_FILE%" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        if /i "%%A"=="PYTHON_PING"     if not "%%B"=="" set "PY_REPORT=%%B"
        if /i "%%A"=="PYTHON_PATHPING" if not "%%B"=="" set "PY_REPORT=%%B"
    )
)

if not defined DAILY_TIME set "DAILY_TIME=18:00"
echo Creating scheduled tasks as SYSTEM (no user interaction; daily at %DAILY_TIME%)...
schtasks /Create /TN "PingMonitor"             /TR "\"%TARGET_BAT%\""                              /SC ONLOGON              /RU "SYSTEM" /RL HIGHEST /F
schtasks /Create /TN "PingMonitor-Daily"       /TR "\"%TARGET_BAT%\" restart"                      /SC DAILY /ST %DAILY_TIME% /RU "SYSTEM" /RL HIGHEST /F
schtasks /Create /TN "PingMonitor-DailyReport" /TR "\"%PY_REPORT%\" \"%SCRIPT_DIR%log_report.py\"" /SC DAILY /ST %DAILY_TIME% /RU "SYSTEM" /RL HIGHEST /F
echo.
echo Tasks created (run as SYSTEM): PingMonitor (at logon), PingMonitor-Daily (%DAILY_TIME%,
echo   restarts monitoring so edited targets*.txt take effect), PingMonitor-DailyReport (%DAILY_TIME%).
echo Note: SYSTEM tasks run in session 0 - monitoring is always hidden regardless of RUN_MODE.
echo.
REM The triggers only fire at the next logon / %DAILY_TIME%, so kick it off now.
schtasks /Run /TN "PingMonitor" >nul 2>&1
if not errorlevel 1 (
    echo Started monitoring now via:  schtasks /Run /TN "PingMonitor"
) else (
    echo Tasks are registered but not started yet. To begin monitoring now, run:
    echo     schtasks /Run /TN "PingMonitor"
)
REM Elevated re-entry runs in its own window - hold it open so the above is readable.
if /i "%1"=="ADMIN_TASK" (
    echo.
    pause
)
goto :DONE

REM ============================================
:MAKE_SHORTCUT   (%~1 = .lnk path)
REM ============================================
powershell -NoLogo -NoProfile -Command ^
   "$ws = New-Object -ComObject WScript.Shell;" ^
   "$s = $ws.CreateShortcut('%~1');" ^
   "$s.TargetPath = '%TARGET_BAT%';" ^
   "$s.WorkingDirectory = '%SCRIPT_DIR%';" ^
   "$s.Save();"
exit /b

REM ============================================
:CONFIGURE_PYTHON
REM select_python.ps1 runs the interpreter menu on the console and writes the
REM two chosen paths to PYSEL as  PING=<path>  /  PATHPING=<path>.
REM ============================================
set "PICK_PING="
set "PICK_PATHPING="
set "PYSEL=%TEMP%\ping_pysel_%RANDOM%.txt"
del /q "%PYSEL%" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%select_python.ps1" -OutFile "%PYSEL%"
if exist "%PYSEL%" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%PYSEL%") do (
        if /i "%%A"=="PING"     set "PICK_PING=%%B"
        if /i "%%A"=="PATHPING" set "PICK_PATHPING=%%B"
    )
    del /q "%PYSEL%" >nul 2>&1
)
if not defined PICK_PING (
    echo WARNING: no Python selected - leaving PYTHON_PING / PYTHON_PATHPING unchanged.
)
goto :eof

REM ============================================
:CONFIGURE_RUN_MODE
REM ============================================
set "CURRENT_RUN_MODE=hidden"
if exist "%ENV_FILE%" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        if /i "%%A"=="RUN_MODE" if not "%%B"=="" set "CURRENT_RUN_MODE=%%B"
    )
)
set "RUN_MODE_INPUT="
set /p "RUN_MODE_INPUT=Console mode [hidden/show] (current: !CURRENT_RUN_MODE!, Enter to keep): "
if not defined RUN_MODE_INPUT set "RUN_MODE_INPUT=!CURRENT_RUN_MODE!"
if /i not "!RUN_MODE_INPUT!"=="show" if /i not "!RUN_MODE_INPUT!"=="hidden" set "RUN_MODE_INPUT=hidden"
goto :eof

REM ============================================
:CONFIGURE_PATHPING
REM ============================================
set "CURRENT_RUN_PATHPING=N"
if exist "%ENV_FILE%" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        if /i "%%A"=="RUN_PATHPING" if not "%%B"=="" set "CURRENT_RUN_PATHPING=%%B"
    )
)
set "RUN_PATHPING_INPUT="
set /p "RUN_PATHPING_INPUT=Run pathping alongside ping? [Y/N] (current: !CURRENT_RUN_PATHPING!, Enter to keep): "
if not defined RUN_PATHPING_INPUT set "RUN_PATHPING_INPUT=!CURRENT_RUN_PATHPING!"
if /i "!RUN_PATHPING_INPUT!"=="Y" (set "RUN_PATHPING_INPUT=Y") else (set "RUN_PATHPING_INPUT=N")
goto :eof

REM ============================================
:SAVE_SETTINGS
REM One .env rewrite for PYTHON_PING / PYTHON_PATHPING / RUN_MODE / RUN_PATHPING.
REM ============================================
if not defined PICK_PING for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do if /i "%%A"=="PYTHON_PING" set "PICK_PING=%%B"
if not defined PICK_PATHPING set "PICK_PATHPING=!PICK_PING!"

set "TMP_ENV=%ENV_FILE%.tmp"
> "%TMP_ENV%" (
    if exist "%ENV_FILE%" (
        for /f "usebackq delims=" %%L in ("%ENV_FILE%") do (
            set "LINE=%%L"
            set "SKIP=0"
            echo(!LINE!| findstr /r /i "^PYTHON_PING=" >nul && set "SKIP=1"
            if !SKIP! EQU 0 (echo(!LINE!| findstr /r /i "^PYTHON_PATHPING=" >nul && set "SKIP=1")
            if !SKIP! EQU 0 (echo(!LINE!| findstr /r /i "^RUN_MODE=" >nul && set "SKIP=1")
            if !SKIP! EQU 0 (echo(!LINE!| findstr /r /i "^RUN_PATHPING=" >nul && set "SKIP=1")
            if !SKIP! EQU 0 (echo(!LINE!| findstr /r /i "^# --- Runtime settings" >nul && set "SKIP=1")
            if !SKIP! EQU 0 echo(!LINE!
        )
    )
    echo(
    echo # --- Runtime settings, managed by install_ping.bat ---
    if defined PICK_PING     echo PYTHON_PING=!PICK_PING!
    if defined PICK_PATHPING echo PYTHON_PATHPING=!PICK_PATHPING!
    echo RUN_MODE=!RUN_MODE_INPUT!
    echo RUN_PATHPING=!RUN_PATHPING_INPUT!
)
move /y "%TMP_ENV%" "%ENV_FILE%" >nul
echo.
echo Saved to .env:  PYTHON_PING=!PICK_PING!
echo                 PYTHON_PATHPING=!PICK_PATHPING!
echo                 RUN_MODE=!RUN_MODE_INPUT!   RUN_PATHPING=!RUN_PATHPING_INPUT!
goto :eof

REM ============================================
:CONFIGURE_LINKS
REM Asks how many internet links/ISPs this machine monitors, and an
REM optional ISP name per link, then writes NUM_LINKS / ISPnn to .env
REM (see .env.example). NUM_LINKS>1 also creates a blank targetsNN.txt
REM per extra link (link 1 reuses/seeds from the existing targets.txt).
REM Re-running this shows current values as defaults, so it never
REM silently wipes a previously configured setup.
REM ============================================
if not exist "%ENV_FILE%" if exist "%SCRIPT_DIR%.env.example" copy "%SCRIPT_DIR%.env.example" "%ENV_FILE%" >nul

set "CURRENT_NUM_LINKS=1"
if exist "%ENV_FILE%" (
    for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
        if /i "%%A"=="NUM_LINKS" if not "%%B"=="" set "CURRENT_NUM_LINKS=%%B"
    )
)

echo.
set "NUM_LINKS_INPUT="
set /p "NUM_LINKS_INPUT=How many internet links / ISPs do you have? [current: %CURRENT_NUM_LINKS%, Enter to keep]: "
if not defined NUM_LINKS_INPUT set "NUM_LINKS_INPUT=%CURRENT_NUM_LINKS%"
set /a NUM_LINKS_INPUT+=0 2>nul
if %NUM_LINKS_INPUT% LSS 1 set "NUM_LINKS_INPUT=1"

REM Strip any previous NUM_LINKS / ISPnn lines from .env, keep everything else
set "TMP_ENV=%ENV_FILE%.tmp"
> "%TMP_ENV%" (
    if exist "%ENV_FILE%" (
        for /f "usebackq delims=" %%L in ("%ENV_FILE%") do (
            set "LINE=%%L"
            set "SKIP=0"
            echo(!LINE!| findstr /r /i "^NUM_LINKS=" >nul && set "SKIP=1"
            if !SKIP! EQU 0 (echo(!LINE!| findstr /r /i "^ISP[0-9][0-9]*=" >nul && set "SKIP=1")
            if !SKIP! EQU 0 (echo(!LINE!| findstr /r /i "^# --- Multi-link" >nul && set "SKIP=1")
            if !SKIP! EQU 0 echo(!LINE!
        )
    )
    echo(
    echo # --- Multi-link / multi-ISP setup, managed by install_ping.bat ---
    echo NUM_LINKS=%NUM_LINKS_INPUT%
)

REM Prompt for each ISP name (current value shown as default) and append it,
REM plus make sure that link's targets file exists once there's more than one.
for /l %%L in (1,1,%NUM_LINKS_INPUT%) do (
    set "LNUM2=0%%L"
    set "LNUM2=!LNUM2:~-2!"

    set "CURRENT_ISP="
    if exist "%ENV_FILE%" (
        for /f "usebackq eol=# tokens=1,* delims==" %%A in ("%ENV_FILE%") do (
            if /i "%%A"=="ISP!LNUM2!" set "CURRENT_ISP=%%B"
        )
    )
    set "ISP_NAME_INPUT="
    set /p "ISP_NAME_INPUT=  ISP name for link !LNUM2! [current: !CURRENT_ISP!, optional, Enter to keep]: "
    if not defined ISP_NAME_INPUT set "ISP_NAME_INPUT=!CURRENT_ISP!"
    >> "%TMP_ENV%" echo ISP!LNUM2!=!ISP_NAME_INPUT!

    if %NUM_LINKS_INPUT% GTR 1 (
        set "LINK_TARGETS=%SCRIPT_DIR%targets!LNUM2!.txt"
        if not exist "!LINK_TARGETS!" (
            if %%L EQU 1 (
                if exist "%SCRIPT_DIR%targets.txt" (
                    copy "%SCRIPT_DIR%targets.txt" "!LINK_TARGETS!" >nul
                ) else if exist "%SCRIPT_DIR%targets.txt.example" (
                    copy "%SCRIPT_DIR%targets.txt.example" "!LINK_TARGETS!" >nul
                ) else (
                    > "!LINK_TARGETS!" echo # Targets for link !LNUM2! - one host per line, # starts a comment.
                )
            ) else (
                > "!LINK_TARGETS!" echo # Targets for link !LNUM2! - one host per line, # starts a comment.
            )
            echo   Created !LINK_TARGETS!
        )
    )
)

REM Single link reads targets.txt - seed it from the example on a fresh clone.
if %NUM_LINKS_INPUT% EQU 1 if not exist "%SCRIPT_DIR%targets.txt" if exist "%SCRIPT_DIR%targets.txt.example" (
    copy "%SCRIPT_DIR%targets.txt.example" "%SCRIPT_DIR%targets.txt" >nul
    echo   Created %SCRIPT_DIR%targets.txt from targets.txt.example
)

move /y "%TMP_ENV%" "%ENV_FILE%" >nul
echo NUM_LINKS=%NUM_LINKS_INPUT% saved to .env
echo.
goto :eof

REM ============================================
:DONE
echo.
if /i "!RUN_MODE_INPUT!"=="hidden" (
    echo NOTE: hidden mode runs monitoring with no visible console window.
    echo To stop running code - call stop_background_ping.bat - be aware
    echo this script will kill all python processes; if you use python on
    echo this computer for other jobs, kill the ping job in background at
    echo your own risk instead ^("bat_many_ping.bat stop" only stops this
    echo folder's monitors^).
    echo.
)
echo install_ping.bat finished.
endlocal
exit /b
