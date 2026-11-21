@echo off
:: Missing Out-Punch check (Windows Task Scheduler: every day at 23:59)
::   run_missing_punch_check.bat                    -> today, all companies with the alert ON (HR Settings)
::   run_missing_punch_check.bat --date 2026-11-18  -> a specific day
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

echo [%date% %time%] missing punch check start >> "%~dp0missing_punch.log"
"%ATTENDANCE_VENV%\Scripts\python.exe" manage.py check_missing_punches %* >> "%~dp0missing_punch.log" 2>&1
set "RC=%ERRORLEVEL%"
echo [%date% %time%] missing punch check end (exit %RC%) >> "%~dp0missing_punch.log"
exit /b %RC%
