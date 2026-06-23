@echo off
setlocal
set "LAUNCHER_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $root=Resolve-Path -LiteralPath (Join-Path $env:LAUNCHER_DIR '..'); $base=-join([char[]](30005,21147,24037,20855,33050,26412)); $name=-join([char[]](30005,37327,27719,24635,24037,20855)); $bat=Join-Path (Join-Path (Join-Path $root $base) $name) ((-join([char[]](21551,21160,30005,37327,27719,24635,24037,20855))) + '.bat'); & $bat"
if errorlevel 1 pause
