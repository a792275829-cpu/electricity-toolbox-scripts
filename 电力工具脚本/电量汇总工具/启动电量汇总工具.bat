@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_PATH=%SCRIPT_DIR%summarize_511_excel.py"
if not exist "%SCRIPT_PATH%" (
    echo Script not found:
    echo %SCRIPT_PATH%
    pause
    exit /b 1
)

where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3.11 "%SCRIPT_PATH%"
    exit /b 0
)

where py >nul 2>nul
if %errorlevel%==0 (
    start "" py -3.11 "%SCRIPT_PATH%"
    exit /b 0
)

echo Failed to launch the tool.
echo Please confirm Python 3.11 is installed.
pause
exit /b 1
