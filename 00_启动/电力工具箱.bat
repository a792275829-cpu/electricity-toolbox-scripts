@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0.."
if exist "%ROOT%\电力工具箱.exe" (
  start "" "%ROOT%\电力工具箱.exe"
  exit /b 0
)
if exist "%ROOT%\runtime\python311\pythonw.exe" (
  start "" "%ROOT%\runtime\python311\pythonw.exe" "%ROOT%\packaging\windows\portable_bootstrap.py"
  exit /b 0
)
if exist "%ROOT%\.venv\Scripts\pythonw.exe" (
  start "" "%ROOT%\.venv\Scripts\pythonw.exe" "%ROOT%\电力工具箱\toolbox_launcher.pyw"
  exit /b 0
)
echo Portable runtime not found. Extract the complete Windows portable ZIP first.
pause
exit /b 1
