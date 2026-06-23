@echo off
setlocal
cd /d "%~dp0"

set "SCRIPT=%~dp0wps_excel_to_kdocs_gui.py"
if not exist "%SCRIPT%" (
  for /f "delims=" %%F in ('dir /b /s "%~dp0wps_excel_to_kdocs_gui.py" 2^>nul') do (
    set "SCRIPT=%%F"
    goto :found_script
  )
)

:found_script
if not exist "%SCRIPT%" (
  echo Could not find wps_excel_to_kdocs_gui.py under:
  echo %~dp0
  pause
  exit /b 1
)

if "%WPS_WRITER_SMOKE%"=="1" (
  echo WPS_WRITER_SCRIPT=%SCRIPT%
  exit /b 0
)

set "PLAYWRIGHT_BROWSERS_PATH=%~dp0..\runtime\ms-playwright"
set "PY=%~dp0..\runtime\python311\python.exe"
if not exist "%PY%" set "PY=C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" "%SCRIPT%"
if errorlevel 1 (
  echo.
  echo Script exited with an error. If dependencies are missing, run:
  for %%D in ("%SCRIPT%") do echo "%PY%" -m pip install -r "%%~dpDrequirements.txt"
  pause
)
