@echo off
:: Push attendance from ZKTeco devices to the live server (run every 10-15 min with Windows Task Scheduler)
::   run_push_agent.bat              -> all devices in push_agent.ini
::   run_push_agent.bat --days 60    -> first time: send the last 60 days
:: Python venv is looked up in this order:  1) %ATTENDANCE_VENV%   2) .\venv   3) ..\env
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

"%ATTENDANCE_VENV%\Scripts\python.exe" push_agent.py %* >> "%~dp0push_agent.log" 2>&1
exit /b %ERRORLEVEL%
