@echo off
setlocal
set "LAUNCHER_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $root=Resolve-Path -LiteralPath (Join-Path $env:LAUNCHER_DIR '..'); $app=-join([char[]](27599,26085,29983,20135,32463,33829,24773,20917,27719,25253,33258,21160,29983,25104,24037,20855)); $tool=Join-Path (Join-Path $root $app) 'scripts'; Set-Location -LiteralPath $tool; $py='C:\Users\lllg\AppData\Local\Programs\Python\Python311\python.exe'; if(-not(Test-Path -LiteralPath $py)){$py='python'}; & $py 'fetch_daily_data.py' '--login'; [void](Read-Host 'Press Enter to exit')"
if errorlevel 1 pause
