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
    start "" pyw "%SCRIPT_PATH%"
    exit /b 0
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%SCRIPT_PATH%"
    exit /b 0
)

where python >nul 2>nul
if %errorlevel%==0 (
    start "" python "%SCRIPT_PATH%"
    exit /b 0
)

echo Failed to launch the tool.
echo Please confirm Python is installed and available in PATH.
pause
exit /b 1
