@echo off
chcp 65001 >nul
setlocal
set "PY=%~dp0runtime\python311\python.exe"
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
echo Extract the complete maintenance ZIP first.
pause
exit /b 1
)
"%PY%" -u -B "%~dp0packaging\windows\maintenance.py" desktop-test
set "RESULT=%ERRORLEVEL%"
echo.
echo Exit code: %RESULT%
pause
exit /b %RESULT%
