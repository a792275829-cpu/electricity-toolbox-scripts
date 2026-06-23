@echo off
setlocal
set "LAUNCHER_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $root=Resolve-Path -LiteralPath (Join-Path $env:LAUNCHER_DIR '..'); $base=-join([char[]](30005,21147,24037,20855,33050,26412)); $name=-join([char[]](30005,21147,20132,26131,20998,26512,24037,20855)); $bat=Join-Path (Join-Path (Join-Path $root $base) $name) 'launch_electricity_report_tool.bat'; & $bat"
if errorlevel 1 pause
