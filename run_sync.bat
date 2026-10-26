@echo off
:: Auto sync attendance from all ZKTeco devices (run every hour with Windows Task Scheduler)
::   run_sync.bat                    -> all departments that have a device IP/Port
::   run_sync.bat --department 3     -> only department id 3
:: Project folder = the folder of this file. Python venv is looked up in this order:
::   1) %ATTENDANCE_VENV%   2) .\venv   3) ..\env
setlocal
cd /d "%~dp0"

if not defined ATTENDANCE_VENV (
    if exist "%~dp0venv\Scripts\python.exe" (
        set "ATTENDANCE_VENV=%~dp0venv"
    ) else (
        set "ATTENDANCE_VENV=%~dp0..\env"
    )
)

if not exist "%ATTENDANCE_VENV%\Scripts\python.exe" (
    echo Python venv not found: "%ATTENDANCE_VENV%". Set ATTENDANCE_VENV to your venv folder.
    exit /b 1
)

echo [%date% %time%] sync start >> "%~dp0sync.log"
"%ATTENDANCE_VENV%\Scripts\python.exe" manage.py sync_attendance %* >> "%~dp0sync.log" 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] sync end (exit %RC%) >> "%~dp0sync.log"
exit /b %RC%
