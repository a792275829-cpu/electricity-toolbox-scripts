@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_PATH=%SCRIPT_DIR%generate_electricity_report.py"
where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3.11 "%SCRIPT_PATH%"
    goto :eof
)

where py >nul 2>nul
if %errorlevel%==0 (
    start "" py -3.11 "%SCRIPT_PATH%"
    goto :eof
)

echo Python launcher not found. Please install Python 3.11.
pause
