@echo off
setlocal
set "LAUNCHER_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $root=Resolve-Path -LiteralPath (Join-Path $env:LAUNCHER_DIR '..'); $base=-join([char[]](30005,21147,24037,20855,33050,26412)); $bat=Join-Path (Join-Path (Join-Path $root $base) 'private-data-uploader-tool') ((-join([char[]](31169,26377,25968,25454,19978,20256,24037,20855))) + '.bat'); & $bat"
if errorlevel 1 pause
