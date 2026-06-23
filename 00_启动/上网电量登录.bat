@echo off
setlocal
set "LAUNCHER_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $root=Resolve-Path -LiteralPath (Join-Path $env:LAUNCHER_DIR '..'); $tool=Join-Path $root (-join([char[]](19978,32593,30005,37327,25235,21462))); Set-Location -LiteralPath $tool; $py='C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe'; if(-not(Test-Path -LiteralPath $py)){$py='python'}; & $py 'export_online_energy.py' '--login'; [void](Read-Host 'Press Enter to exit')"
if errorlevel 1 pause
