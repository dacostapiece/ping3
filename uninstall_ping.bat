@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================
REM uninstall_ping.bat - undoes what install_ping.bat created:
REM   * scheduled tasks PingMonitor / PingMonitor-Daily / PingMonitor-DailyReport
REM     (and the older PingMonitor-Watchdog name, if present)
REM   * Start Menu shortcut  "Ping Monitor.lnk"
REM   * Desktop shortcut     "Ping Monitor.lnk"
REM   * legacy all-users Startup shortcut  bat_many_ping.lnk
REM Leaves .env, targets*.txt and results/ untouched.
REM ============================================
set "SCRIPT_FULL=%~f0"
set "SCRIPT_DIR=%~dp0"

for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESKTOP=%%D"
for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Programs')"`) do set "STARTMENU=%%D"
set "DESKTOP_LNK=%DESKTOP%\Ping Monitor.lnk"
set "STARTMENU_LNK=%STARTMENU%\Ping Monitor.lnk"
set "LEGACY_LNK=C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Startup\bat_many_ping.lnk"

if /i "%1"=="ADMIN_UNINSTALL" goto :DO_UNINSTALL

net session >nul 2>&1
if errorlevel 1 (
    echo Elevating to remove scheduled tasks...
    powershell -NoProfile -Command ^
        "Start-Process '%SCRIPT_FULL%' -Verb RunAs -ArgumentList 'ADMIN_UNINSTALL'"
    exit /b
)

:DO_UNINSTALL
echo Removing scheduled tasks...
for %%T in (PingMonitor PingMonitor-Daily PingMonitor-Watchdog PingMonitor-DailyReport) do (
    schtasks /Query /TN "%%T" >nul 2>&1 && (
        schtasks /Delete /TN "%%T" /F >nul 2>&1 && echo   deleted task %%T
    ) || echo   task %%T not present
)

echo Removing shortcuts...
for %%L in ("%DESKTOP_LNK%" "%STARTMENU_LNK%" "%LEGACY_LNK%") do (
    if exist "%%~L" (
        del /f /q "%%~L" && echo   deleted %%~L
    ) else (
        echo   not present: %%~L
    )
)

echo.
echo uninstall_ping.bat finished. Any running monitor processes were left alone
echo - close their windows or reboot to stop them.
endlocal
exit /b
